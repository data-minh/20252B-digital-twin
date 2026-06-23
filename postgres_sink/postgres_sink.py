import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import paho.mqtt.client as mqtt

try:
    import psycopg2
except ImportError:  # pragma: no cover - unit tests exercise pure functions without PostgreSQL installed.
    psycopg2 = None


TABLE_NAME = "parking_slot_history"
LOCAL_TIMESTAMP_TIMEZONE = timezone(timedelta(hours=7))


def unix_timestamp_to_datetime(value):
    return datetime.fromtimestamp(int(value), tz=LOCAL_TIMESTAMP_TIMEZONE).replace(tzinfo=None)


def record_unique_id(frame_id, slot_id):
    return sha256(f"{frame_id}:{slot_id}".encode("utf-8")).hexdigest()


def history_row(record):
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
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {"unique_id": row[0], "occupied": row[1]}


def fetch_active_row(cursor, slot_id):
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
    cursor.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET enddate = %s, status = %s
        WHERE unique_id = %s
        """,
        (action["enddate"], action["status"], action["unique_id"]),
    )


def insert_history_row(cursor, row):
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
    payload = message["payload"]
    print(
        f"Postgres sink received frame_id={message.get('frame_id')} "
        f"source_frame_id={message.get('source_frame_id')} slots={len(payload)}",
        flush=True,
    )
    summary = apply_scd2_records(conn, payload)
    print(
        f"Postgres sink wrote frame_id={message.get('frame_id')} "
        f"inserted={summary['inserted']} closed={summary['closed']} skipped={summary['skipped']}",
        flush=True,
    )
    return len(payload)


def wait_for_postgres():
    while True:
        try:
            conn = connect_postgres()
            ensure_schema(conn)
            return conn
        except Exception as exc:
            print(f"Postgres sink waiting for database: {exc}", flush=True)
            time.sleep(2)


def run_sink(mqtt_host: str, mqtt_port: int, topic: str):
    conn = wait_for_postgres()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Postgres sink connected to MQTT reason_code={reason_code}; subscribing topic={topic}", flush=True)
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        try:
            message = json.loads(msg.payload.decode("utf-8"))
            upload_frame_message(conn, message)
        except Exception as exc:
            print(f"Postgres sink failed to process MQTT message on {msg.topic}: {exc}", flush=True)
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
    parser = argparse.ArgumentParser(description="Subscribe to MQTT parking frames and write SCD2 history to PostgreSQL.")
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "mqtt-broker"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default=os.environ.get("MQTT_TOPIC", "parking/frames"))
    args = parser.parse_args()

    run_sink(args.mqtt_host, args.mqtt_port, args.topic)


if __name__ == "__main__":
    main()
