import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import paho.mqtt.client as mqtt
import ssl
import base64
from Crypto.Cipher import AES

try:
    import psycopg2
except ImportError:  # pragma: no cover - unit tests exercise pure functions without PostgreSQL installed.
    psycopg2 = None


TABLE_NAME = "parking_slot_history"
LOCAL_TIMESTAMP_TIMEZONE = timezone(timedelta(hours=7))


def unix_timestamp_to_datetime(value):
    """Chuyển đổi một giá trị timestamp theo kiểu Unix sang đối tượng datetime.

    Hàm này chuyển timestamp sang múi giờ cục bộ là UTC+7 rồi bỏ thông tin
    timezone để thuận tiện cho việc lưu vào PostgreSQL.

    Args:
        value: Giá trị timestamp theo đơn vị giây.

    Returns:
        datetime: Đối tượng datetime đã được chuyển đổi.
    """
    return datetime.fromtimestamp(int(value), tz=LOCAL_TIMESTAMP_TIMEZONE).replace(tzinfo=None)


def record_unique_id(frame_id, slot_id):
    """Tạo một khóa duy nhất cho một bản ghi lịch sử của slot.

    Giá trị này được tạo bằng hàm băm SHA-256 từ frame_id và slot_id, nhằm
    đảm bảo mỗi bản ghi có một định danh duy nhất và nhất quán.

    Args:
        frame_id: Mã khung hình nhận được từ dữ liệu camera.
        slot_id: Mã định danh của slot trong khung hình.

    Returns:
        str: Chuỗi hash dùng làm unique_id.
    """
    return sha256(f"{frame_id}:{slot_id}".encode("utf-8")).hexdigest()


def history_row(record):
    """Chuyển một bản ghi dữ liệu frame thành cấu trúc hàng lịch sử SCD2.

    Hàm này chuẩn hóa dữ liệu nhận được từ payload thành một bản ghi có các
    trường cần thiết để lưu vào bảng lịch sử.

    Args:
        record: Một bản ghi dữ liệu slot từ payload MQTT.

    Returns:
        dict: Bản ghi đã được chuẩn hóa để insert vào PostgreSQL.
    """
    event_time = unix_timestamp_to_datetime(record["timestamp"])
    return {
        "unique_id": record_unique_id(record["frame_id"], record["id"]),
        "frame_id": int(record["frame_id"]),
        "id": str(record["id"]),
        "occupied": int(record["occupied"]),
        "timestamp": event_time,
        "startdate": event_time,
        "enddate": None,
        "status": "active",
    }


def scd2_actions(current_active_row, new_row):
    """Xác định các hành động SCD2 cần thực hiện khi có bản ghi mới.

    Nếu chưa có bản ghi đang hoạt động thì chỉ cần insert bản ghi mới. Nếu trạng
    thái occupied thay đổi thì cần đóng bản ghi cũ và insert bản ghi mới.

    Args:
        current_active_row: Bản ghi đang hoạt động hiện tại của slot.
        new_row: Bản ghi mới cần ghi nhận.

    Returns:
        list[dict]: Danh sách các hành động cần thực hiện.
    """
    if current_active_row is None:
        return [{"action": "insert", "row": new_row}]

    if int(current_active_row["occupied"]) == new_row["occupied"]:
        return []

    return [
        {
            "action": "close",
            "unique_id": current_active_row["unique_id"],
            "enddate": new_row["startdate"],
            "status": "inactive",
        },
        {"action": "insert", "row": new_row},
    ]


def connect_postgres():
    """Tạo kết nối đến cơ sở dữ liệu PostgreSQL.

    Hàm này ưu tiên dùng biến môi trường DATABASE_URL nếu có, nếu không thì
    xây dựng kết nối từ các biến POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
    POSTGRES_USER và POSTGRES_PASSWORD.

    Returns:
        Connection: Đối tượng kết nối PostgreSQL.

    Raises:
        RuntimeError: Khi thư viện psycopg2 chưa được cài đặt.
    """
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required to run the PostgreSQL sink")

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "parking"),
        user=os.environ.get("POSTGRES_USER", "parking"),
        password=os.environ.get("POSTGRES_PASSWORD", "parking"),
    )


def ensure_schema(conn):
    """Đảm bảo bảng lịch sử và các chỉ mục cần thiết đã tồn tại trong PostgreSQL.

    Hàm này tạo bảng parking_slot_history nếu chưa có, thiết lập múi giờ và
    điều chỉnh kiểu dữ liệu của các cột thời gian phù hợp cho lưu trữ.

    Args:
        conn: Đối tượng kết nối PostgreSQL.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                unique_id TEXT PRIMARY KEY,
                frame_id INTEGER NOT NULL,
                id TEXT NOT NULL,
                occupied INTEGER NOT NULL,
                "timestamp" TIMESTAMP(6) NOT NULL,
                startdate TIMESTAMP(6) NOT NULL,
                enddate TIMESTAMP(6) NULL,
                status TEXT NOT NULL
            )
            """
        )
        cursor.execute("SET TIME ZONE 'Asia/Ho_Chi_Minh'")
        for column in ('"timestamp"', "startdate", "enddate"):
            cursor.execute(
                f"""
                ALTER TABLE {TABLE_NAME}
                ALTER COLUMN {column}
                TYPE TIMESTAMP(6) WITHOUT TIME ZONE
                """
            )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_active_slot
            ON {TABLE_NAME} (id, status)
            """
        )
    conn.commit()


def normalize_current_row(row):
    """Chuẩn hóa kết quả truy vấn thành cấu trúc dict để dễ xử lý.

    Args:
        row: Dòng dữ liệu trả về từ cursor, có thể là dict hoặc tuple.

    Returns:
        dict | None: Bản ghi đã chuẩn hóa hoặc None nếu không có dữ liệu.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {"unique_id": row[0], "occupied": row[1]}


def fetch_active_row(cursor, slot_id):
    """Lấy bản ghi đang hoạt động gần nhất của một slot.

    Args:
        cursor: Cursor của kết nối PostgreSQL.
        slot_id: Mã slot cần truy vấn.

    Returns:
        dict | None: Bản ghi đang hoạt động hoặc None nếu không tồn tại.
    """
    cursor.execute(
        f"""
        SELECT unique_id, occupied
        FROM {TABLE_NAME}
        WHERE id = %s AND status = 'active'
        ORDER BY startdate DESC
        LIMIT 1
        """,
        (slot_id,),
    )
    return normalize_current_row(cursor.fetchone())


def fetch_active_rows(cursor, slot_ids):
    """Lấy các bản ghi đang hoạt động cho nhiều slot cùng lúc.

    Args:
        cursor: Cursor của kết nối PostgreSQL.
        slot_ids: Danh sách các slot cần truy vấn.

    Returns:
        dict: Bảng ánh xạ từ slot_id sang bản ghi đang hoạt động tương ứng.
    """
    if not slot_ids:
        return {}

    cursor.execute(
        f"""
        SELECT DISTINCT ON (id) id, unique_id, occupied
        FROM {TABLE_NAME}
        WHERE id = ANY(%s) AND status = 'active'
        ORDER BY id, startdate DESC
        """,
        (list(slot_ids),),
    )
    return {
        row[0]: {"unique_id": row[1], "occupied": row[2]}
        for row in cursor.fetchall()
    }


def close_history_row(cursor, action):
    """Đóng một bản ghi lịch sử đang hoạt động bằng cách cập nhật enddate và status.

    Args:
        cursor: Cursor của kết nối PostgreSQL.
        action: Thông tin hành động close từ SCD2.
    """
    cursor.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET enddate = %s, status = %s
        WHERE unique_id = %s
        """,
        (action["enddate"], action["status"], action["unique_id"]),
    )


def insert_history_row(cursor, row):
    """Chèn một bản ghi lịch sử mới vào bảng PostgreSQL.

    Args:
        cursor: Cursor của kết nối PostgreSQL.
        row: Bản ghi mới cần chèn.
    """
    cursor.execute(
        f"""
        INSERT INTO {TABLE_NAME}
            (unique_id, frame_id, id, occupied, "timestamp", startdate, enddate, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (unique_id) DO NOTHING
        """,
        (
            row["unique_id"],
            row["frame_id"],
            row["id"],
            row["occupied"],
            row["timestamp"],
            row["startdate"],
            row["enddate"],
            row["status"],
        ),
    )


def apply_scd2_record(conn, record):
    """Áp dụng quy trình SCD2 cho một bản ghi đơn lẻ.

    Hàm này tạo bản ghi lịch sử mới và xử lý việc đóng bản ghi cũ nếu trạng
    thái occupied thay đổi.

    Args:
        conn: Đối tượng kết nối PostgreSQL.
        record: Một bản ghi dữ liệu slot.
    """
    row = history_row(record)
    with conn.cursor() as cursor:
        current = fetch_active_row(cursor, row["id"])
        for action in scd2_actions(current, row):
            if action["action"] == "close":
                close_history_row(cursor, action)
            elif action["action"] == "insert":
                insert_history_row(cursor, action["row"])
    conn.commit()


def apply_scd2_records(conn, payload):
    """Áp dụng quy trình SCD2 cho toàn bộ payload nhận được.

    Hàm này xử lý nhiều bản ghi cùng lúc, tối ưu bằng cách lấy tất cả slot
    đang hoạt động một lần rồi áp dụng các hành động close/insert tương ứng.

    Args:
        conn: Đối tượng kết nối PostgreSQL.
        payload: Danh sách các bản ghi cần xử lý.

    Returns:
        dict: Thống kê số bản ghi inserted, closed và skipped.
    """
    rows = [history_row(record) for record in payload]
    summary = {"inserted": 0, "closed": 0, "skipped": 0}

    with conn.cursor() as cursor:
        active_rows = fetch_active_rows(cursor, [row["id"] for row in rows])
        for row in rows:
            actions = scd2_actions(active_rows.get(row["id"]), row)
            if not actions:
                summary["skipped"] += 1
                continue

            for action in actions:
                if action["action"] == "close":
                    close_history_row(cursor, action)
                    summary["closed"] += 1
                elif action["action"] == "insert":
                    insert_history_row(cursor, action["row"])
                    summary["inserted"] += 1

    conn.commit()
    return summary


def upload_frame_message(conn, message):
    """Nhận một tin nhắn frame từ MQTT và ghi dữ liệu vào PostgreSQL.

    Hàm này lấy payload từ tin nhắn, áp dụng quy trình SCD2 và in ra thống kê
    kết quả để theo dõi việc ghi dữ liệu.

    Args:
        conn: Đối tượng kết nối PostgreSQL.
        message: Tin nhắn JSON nhận được từ MQTT.

    Returns:
        int: Số lượng slot có trong payload.
    """
    payload = message["payload"]
    print(
        f"Postgres sink received frame_id={message.get('frame_id')} "
        f"source_frame_id={message.get('source_frame_id')} slots={len(payload)}",
        flush=True,
    )
    print(f"Postgres sink message: {json.dumps(message, ensure_ascii=False)}", flush=True)
    print(f"Postgres sink payload: {json.dumps(payload, ensure_ascii=False)}", flush=True)
    summary = apply_scd2_records(conn, payload)
    print(
        f"Postgres sink wrote frame_id={message.get('frame_id')} "
        f"inserted={summary['inserted']} closed={summary['closed']} skipped={summary['skipped']}",
        flush=True,
    )
    return len(payload)


def wait_for_postgres():
    """Đợi cho đến khi PostgreSQL sẵn sàng trước khi bắt đầu xử lý.

    Nếu database chưa sẵn sàng ngay lúc khởi động, hàm này sẽ thử kết nối lại
    sau mỗi vài giây cho đến khi thành công.

    Returns:
        Connection: Kết nối PostgreSQL đã sẵn sàng.
    """
    while True:
        try:
            conn = connect_postgres()
            ensure_schema(conn)
            return conn
        except Exception as exc:
            print(f"Postgres sink waiting for database: {exc}", flush=True)
            time.sleep(2)


def configure_mqtt_auth(client, username: str | None = None, password: str | None = None):
    """Cấu hình thông tin xác thực MQTT cho client nếu có username.

    Args:
        client: Đối tượng client MQTT.
        username: Tên đăng nhập MQTT.
        password: Mật khẩu MQTT.
    """
    if username:
        client.username_pw_set(username, password or None)


def configure_mqtt_tls(client, ca_certs: str | None = None, certfile: str | None = None, keyfile: str | None = None, insecure: bool = False):
    if ca_certs or certfile or keyfile:
        client.tls_set(ca_certs if ca_certs else None, certfile=certfile, keyfile=keyfile, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.tls_insecure_set(insecure)


def normalize_aes_key(key: bytes | str | None) -> bytes | None:
    if key is None:
        return None
    if isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key

    if len(key_bytes) in (16, 24, 32):
        return key_bytes
    return hashlib.sha256(key_bytes).digest()


def load_encryption_key(path_or_env: str | None) -> bytes | None:
    if not path_or_env:
        return None

    env_key = os.environ.get("ENCRYPTION_KEY")

    try:
        p = Path(path_or_env)
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            return normalize_aes_key(text)
    except Exception:
        pass

    if env_key and (str(path_or_env).startswith("/") or "\\" in str(path_or_env) or str(path_or_env).endswith((".txt", ".key", ".pem", ".bin"))):
        return normalize_aes_key(env_key)

    try:
        return normalize_aes_key(base64.b64decode(path_or_env))
    except Exception:
        return normalize_aes_key(path_or_env)


def decrypt_aes_gcm(key: bytes, b64payload: bytes) -> bytes:
    data = base64.b64decode(b64payload)
    if len(data) < 28:
        raise ValueError("ciphertext too short for nonce+tag")
    nonce = data[:12]
    tag = data[12:28]
    ciphertext = data[28:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return plaintext


def run_sink(
    mqtt_host: str,
    mqtt_port: int,
    topic: str,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
    use_tls: bool = False,
    ca_certs: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
    tls_insecure: bool = False,
    encryption_key_spec: str | None = None,
):
    """Chạy service lắng nghe MQTT và ghi dữ liệu vào PostgreSQL.

    Hàm này kết nối tới database, đăng ký listener cho topic MQTT, nhận tin
    nhắn và chuyển payload vào cơ chế SCD2 để lưu lịch sử trạng thái slot.

    Args:
        mqtt_host: Địa chỉ broker MQTT.
        mqtt_port: Cổng broker MQTT.
        topic: Topic cần đăng ký nhận tin nhắn.
        mqtt_username: Tên đăng nhập MQTT tùy chọn.
        mqtt_password: Mật khẩu MQTT tùy chọn.
    """
    conn = wait_for_postgres()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    configure_mqtt_auth(client, mqtt_username, mqtt_password)
    if use_tls:
        configure_mqtt_tls(client, ca_certs=ca_certs, certfile=client_cert, keyfile=client_key, insecure=tls_insecure)

    encryption_key = load_encryption_key(encryption_key_spec or os.environ.get('ENCRYPTION_KEY'))

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Postgres sink connected to MQTT reason_code={reason_code}; subscribing topic={topic}", flush=True)
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        raw_payload = msg.payload.decode("utf-8", errors="replace")
        print(f"Postgres sink raw payload on {msg.topic}: {raw_payload}", flush=True)

        try:
            message = json.loads(raw_payload)
            print("Postgres sink parsed as plaintext JSON", flush=True)
            upload_frame_message(conn, message)
            return
        except Exception:
            if encryption_key:
                try:
                    plaintext = decrypt_aes_gcm(encryption_key, msg.payload)
                    decrypted_text = plaintext.decode("utf-8", errors="replace")
                    print(f"Postgres sink decrypted payload on {msg.topic}: {decrypted_text}", flush=True)
                    message = json.loads(decrypted_text)
                    upload_frame_message(conn, message)
                    return
                except Exception as exc:
                    print(f"Postgres sink failed to decrypt/parse payload on {msg.topic}: {exc}", flush=True)
            else:
                print(f"Postgres sink received non-JSON payload but no encryption key configured on {msg.topic}", flush=True)
            try:
                conn.rollback()
            except Exception:
                pass

    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(mqtt_host, mqtt_port, keepalive=60)
            break
        except OSError as exc:
            print(f"Postgres sink waiting for MQTT broker {mqtt_host}:{mqtt_port}: {exc}", flush=True)
            time.sleep(2)

    client.loop_forever()


def main():
    """Điểm vào chương trình để chạy PostgreSQL sink từ dòng lệnh.

    Hàm này parse các tham số CLI và gọi run_sink để bắt đầu lắng nghe MQTT.
    """
    parser = argparse.ArgumentParser(description="Subscribe to MQTT parking frames and write SCD2 history to PostgreSQL.")
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "mqtt-broker"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default=os.environ.get("MQTT_TOPIC", "parking/frames"))
    parser.add_argument("--mqtt-username", default=os.environ.get("MQTT_USERNAME"))
    parser.add_argument("--mqtt-password", default=os.environ.get("MQTT_PASSWORD"))
    parser.add_argument("--tls", action="store_true", default=os.environ.get("MQTT_TLS") in ("1", "true", "True"))
    parser.add_argument("--ca-certs", default=os.environ.get("MQTT_CA_CERTS"))
    parser.add_argument("--client-cert", default=os.environ.get("MQTT_CLIENT_CERT"))
    parser.add_argument("--client-key", default=os.environ.get("MQTT_CLIENT_KEY"))
    parser.add_argument("--tls-insecure", action="store_true", default=os.environ.get("MQTT_TLS_INSECURE") in ("1", "true", "True"))
    parser.add_argument("--encryption-key-file", default=os.environ.get("ENCRYPTION_KEY_FILE") or os.environ.get("ENCRYPTION_KEY"))
    args = parser.parse_args()

    run_sink(
        args.mqtt_host,
        args.mqtt_port,
        args.topic,
        args.mqtt_username,
        args.mqtt_password,
        use_tls=args.tls,
        ca_certs=args.ca_certs,
        client_cert=args.client_cert,
        client_key=args.client_key,
        tls_insecure=args.tls_insecure,
        encryption_key_spec=args.encryption_key_file,
    )


if __name__ == "__main__":
    main()
