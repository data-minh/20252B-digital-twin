import argparse
import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

import stream_clean_to_json as stream


def optional_int(value):
    return int(value) if value not in {None, ""} else None


def optional_bool(value, default=False):
    if value in {None, ""}:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def frame_message(split: str, source_frame_id: str, image_file: str | Path, payload):
    return {
        "split": split,
        "frame_id": payload[0]["frame_id"] if payload else stream.parse_frame_id(source_frame_id),
        "source_frame_id": source_frame_id,
        "image": str(image_file),
        "payload": payload,
    }


def configure_mqtt_auth(client, username: str | None = None, password: str | None = None):
    if username:
        client.username_pw_set(username, password or None)


def connect_mqtt_with_retry(client, mqtt_host: str, mqtt_port: int, retry_delay_seconds: float = 2):
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
    start_timestamp: int,
    frame_interval_seconds: int,
    publish_interval_seconds: float,
    max_frames: int | None = None,
    start_delay_seconds: float = 0,
    loop_dataset: bool = True,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
):
    if start_delay_seconds > 0:
        print(f"Camera waiting {start_delay_seconds} seconds before publishing", flush=True)
        time.sleep(start_delay_seconds)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    configure_mqtt_auth(client, mqtt_username, mqtt_password)
    connect_mqtt_with_retry(client, mqtt_host, mqtt_port)
    client.loop_start()

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
                    start_timestamp=start_timestamp,
                    frame_interval_seconds=frame_interval_seconds,
                )
                message = frame_message(split, frame_id, image_file, payload)
                result = client.publish(topic, json.dumps(message), qos=1)
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
    parser.add_argument("--start-timestamp", type=int, default=stream.DEFAULT_START_TIMESTAMP)
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
