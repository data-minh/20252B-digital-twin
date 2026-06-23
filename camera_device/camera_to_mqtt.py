import argparse
import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

import stream_clean_to_json as stream


def optional_int(value):
    return int(value) if value not in {None, ""} else None


def frame_message(split: str, source_frame_id: str, image_file: str | Path, payload):
    return {
        "split": split,
        "frame_id": payload[0]["frame_id"] if payload else stream.parse_frame_id(source_frame_id),
        "source_frame_id": source_frame_id,
        "image": str(image_file),
        "payload": payload,
    }


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
):
    if start_delay_seconds > 0:
        print(f"Camera waiting {start_delay_seconds} seconds before publishing", flush=True)
        time.sleep(start_delay_seconds)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(mqtt_host, mqtt_port, keepalive=60)
    client.loop_start()

    published = 0
    try:
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
            print(
                f"Camera published frame_id={message['frame_id']} "
                f"source_frame_id={frame_id} slots={len(payload)} topic={topic}",
                flush=True,
            )

            if max_frames is not None and published >= max_frames:
                break
            if publish_interval_seconds > 0:
                time.sleep(publish_interval_seconds)
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
    parser.add_argument("--publish-interval", type=float, default=float(os.environ.get("CAMERA_PUBLISH_INTERVAL", "1")))
    parser.add_argument("--start-delay", type=float, default=float(os.environ.get("CAMERA_START_DELAY", "5")))
    parser.add_argument("--max-frames", type=int, default=optional_int(os.environ.get("MAX_FRAMES")))
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
    )


if __name__ == "__main__":
    main()
