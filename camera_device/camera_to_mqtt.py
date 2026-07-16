import argparse
import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

import stream_clean_to_json as stream


def optional_int(value):
    """Chuyển một giá trị tùy chọn từ CLI hoặc biến môi trường thành số nguyên hoặc None.

    Hàm này dùng cho các tham số có thể bị bỏ trống. Chuỗi rỗng và None được
    xem là giá trị thiếu và trả về None thay vì gây lỗi.

    Args:
        value: Giá trị thô cần chuyển đổi, thường đến từ argparse hoặc biến môi trường.

    Returns:
        int | None: Số nguyên đã parse nếu có, ngược lại là None.
    """
    return int(value) if value not in {None, ""} else None


def optional_bool(value, default=False):
    """Chuyển một giá trị thành boolean với hỗ trợ các chuỗi đúng phổ biến.

    Hàm này chấp nhận các giá trị như "1", "true", "yes", "y" và "on"
    thành True, trong khi None hoặc chuỗi rỗng sẽ trả về giá trị mặc định.

    Args:
        value: Giá trị thô cần phân tích.
        default: Giá trị boolean trả về khi đầu vào bị thiếu.

    Returns:
        bool: Giá trị boolean đã được phân tích.
    """
    if value in {None, ""}:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def frame_message(split: str, source_frame_id: str, image_file: str | Path, payload):
    """Tạo cấu trúc tin nhắn JSON để phát cho một khung hình từ camera.

    Dictionary kết quả chứa thông tin về phân chia dữ liệu, frame id đã giải
    quyết, frame id gốc, đường dẫn hình ảnh và payload được tạo từ file nhãn.

    Args:
        split: Tên phân đoạn dữ liệu như train, valid hoặc test.
        source_frame_id: Mã định danh khung hình gốc trong dataset.
        image_file: Đường dẫn tới file hình ảnh liên quan đến khung hình.
        payload: Payload có cấu trúc được tạo cho khung hình.

    Returns:
        dict: Dictionary sẵn sàng để chuyển thành JSON và gửi qua MQTT.
    """
    return {
        "split": split,
        "frame_id": payload[0]["frame_id"] if payload else stream.parse_frame_id(source_frame_id),
        "source_frame_id": source_frame_id,
        "image": str(image_file),
        "payload": payload,
    }


def configure_mqtt_auth(client, username: str | None = None, password: str | None = None):
    """Gán thông tin xác thực MQTT cho client khi được cung cấp.

    Args:
        client: Đối tượng client MQTT cần cấu hình.
        username: Tên đăng nhập tùy chọn để xác thực với broker.
        password: Mật khẩu tùy chọn để xác thực với broker.
    """
    if username:
        client.username_pw_set(username, password or None)


def connect_mqtt_with_retry(client, mqtt_host: str, mqtt_port: int, retry_delay_seconds: float = 2):
    """Kết nối tới broker MQTT và thử lại liên tục nếu gặp lỗi tạm thời.

    Hàm này sẽ tiếp tục thử cho đến khi kết nối thành công. Điều này hữu ích
    khi broker chưa sẵn sàng ngay khi khởi động.

    Args:
        client: Đối tượng client MQTT dùng để kết nối.
        mqtt_host: Tên máy hoặc địa chỉ IP của broker MQTT.
        mqtt_port: Cổng mà broker MQTT đang lắng nghe.
        retry_delay_seconds: Thời gian chờ giữa các lần thử kết nối.

    Returns:
        int: Mã kết quả trả về từ lệnh connect của client MQTT.
    """
    while True:
        try:
            return client.connect(mqtt_host, mqtt_port, keepalive=60)
        except OSError as exc:
            print(f"Camera waiting for MQTT broker {mqtt_host}:{mqtt_port}: {exc}", flush=True)
            time.sleep(retry_delay_seconds)


def publish_dataset(
    input_dir: Path,
    mqtt_host: str,
    mqtt_port: int,
    topic: str,
    start_timestamp: int | None,
    frame_interval_seconds: int,
    publish_interval_seconds: float,
    max_frames: int | None = None,
    start_delay_seconds: float = 0,
    loop_dataset: bool = True,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
):
    """Đẩy tất cả các khung hình từ thư mục dataset lên topic MQTT.

    Hàm này duyệt qua dataset, bỏ qua các khung hình không có file nhãn,
    tạo payload JSON cho từng khung hình và gửi tới topic MQTT đã cấu hình.
    Nó có thể lặp lại dataset nhiều vòng, chờ trước khi phát và dừng sau khi
    đạt số khung hình tối đa.

    Args:
        input_dir: Thư mục chứa các file hình ảnh và file nhãn của dataset.
        mqtt_host: Tên máy chủ của broker MQTT.
        mqtt_port: Cổng của broker MQTT.
        topic: Topic MQTT để gửi dữ liệu.
        start_timestamp: Mốc thời gian bắt đầu dùng khi tạo payload cho khung hình.
        frame_interval_seconds: Khoảng thời gian giữa các khung hình trong payload.
        publish_interval_seconds: Thời gian chờ giữa các lần publish.
        max_frames: Số khung hình tối đa cần publish, tùy chọn.
        start_delay_seconds: Thời gian chờ trước khi bắt đầu vòng publish.
        loop_dataset: Có lặp lại dataset vô hạn hay không.
        mqtt_username: Tên đăng nhập MQTT tùy chọn để xác thực.
        mqtt_password: Mật khẩu MQTT tùy chọn để xác thực.

    Returns:
        int: Tổng số khung hình đã được publish.
    """
    if start_delay_seconds > 0:
        print(f"Camera waiting {start_delay_seconds} seconds before publishing", flush=True)
        time.sleep(start_delay_seconds)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    configure_mqtt_auth(client, mqtt_username, mqtt_password)
    connect_mqtt_with_retry(client, mqtt_host, mqtt_port)
    client.loop_start()

    effective_start_timestamp = stream.resolve_start_timestamp(start_timestamp)

    published = 0
    cycle = 1
    try:
        while True:
            published_this_cycle = 0
            for split, image_file, label_file, frame_id in stream.iter_frames(input_dir):
                if not label_file.exists():
                    print(f"Camera skipped {image_file}: missing label {label_file}", flush=True)
                    continue

                payload = stream.frame_payload(
                    frame_id,
                    label_file,
                    start_timestamp=effective_start_timestamp,
                    frame_interval_seconds=frame_interval_seconds,
                )
                message = frame_message(split, frame_id, image_file, payload)
                message_json = json.dumps(message, ensure_ascii=False)
                print(
                    f"Camera publishing cycle={cycle} frame_id={message['frame_id']} "
                    f"source_frame_id={frame_id} slots={len(payload)} topic={topic}",
                    flush=True,
                )
                print(f"Camera payload: {message_json}", flush=True)
                result = client.publish(topic, message_json, qos=1)
                result.wait_for_publish()
                published += 1
                published_this_cycle += 1
                print(
                    f"Camera published cycle={cycle} frame_id={message['frame_id']} "
                    f"source_frame_id={frame_id} slots={len(payload)} topic={topic}",
                    flush=True,
                )

                if max_frames is not None and published >= max_frames:
                    return published
                if publish_interval_seconds > 0:
                    time.sleep(publish_interval_seconds)

            if not loop_dataset:
                break
            if published_this_cycle == 0:
                print(f"Camera found no publishable frames in {input_dir}; stopping", flush=True)
                break
            print(f"Camera completed cycle={cycle}; restarting dataset", flush=True)
            cycle += 1
    finally:
        client.loop_stop()
        client.disconnect()

    return published


def main():
    """Chạy trình publish camera dưới dạng script CLI.

    Điểm vào này phân tích các tham số dòng lệnh, giải quyết thư mục dataset
    đầu vào và bắt đầu gửi các khung hình tới broker MQTT bằng các tùy chọn
    đã cấu hình.
    """
    parser = argparse.ArgumentParser(description="Publish parking frame payloads to MQTT.")
    parser.add_argument("--input", default=os.environ.get("CAMERA_INPUT", "data/content/dataset"))
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "mqtt-broker"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default=os.environ.get("MQTT_TOPIC", "parking/frames"))
    parser.add_argument("--mqtt-username", default=os.environ.get("MQTT_USERNAME"))
    parser.add_argument("--mqtt-password", default=os.environ.get("MQTT_PASSWORD"))
    parser.add_argument("--publish-interval", type=float, default=float(os.environ.get("CAMERA_PUBLISH_INTERVAL", "1")))
    parser.add_argument("--start-delay", type=float, default=float(os.environ.get("CAMERA_START_DELAY", "5")))
    parser.add_argument("--max-frames", type=int, default=optional_int(os.environ.get("MAX_FRAMES")))
    parser.add_argument("--loop-dataset", action="store_true", default=optional_bool(os.environ.get("CAMERA_LOOP_DATASET"), True))
    parser.add_argument("--no-loop-dataset", dest="loop_dataset", action="store_false")
    parser.add_argument("--start-timestamp", type=int, default=None)
    parser.add_argument(
        "--frame-interval-seconds",
        type=int,
        default=int(os.environ.get("CAMERA_FRAME_INTERVAL_SECONDS", str(stream.DEFAULT_FRAME_INTERVAL_SECONDS))),
    )
    args = parser.parse_args()

    input_dir = stream.resolve_input_dir(Path(args.input))
    publish_dataset(
        input_dir=input_dir,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        topic=args.topic,
        start_timestamp=args.start_timestamp,
        frame_interval_seconds=args.frame_interval_seconds,
        publish_interval_seconds=args.publish_interval,
        max_frames=args.max_frames,
        start_delay_seconds=args.start_delay,
        loop_dataset=args.loop_dataset,
        mqtt_username=args.mqtt_username,
        mqtt_password=args.mqtt_password,
    )


if __name__ == "__main__":
    main()
