import argparse
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
STORAGE_CHOICES = {"json", "thingspeak", "both"}
THINGSPEAK_UPLOAD_MODES = {"frame", "slot"}
DEFAULT_START_TIMESTAMP = 1634567890
DEFAULT_FRAME_INTERVAL_SECONDS = 1
DEFAULT_THINGSPEAK_CHANNEL_ID_ENV = "THINGSPEAK_CHANNEL_ID"
DEFAULT_THINGSPEAK_USER_API_KEY_ENV = "THINGSPEAK_USER_API_KEY"
KEEP_PATTERN = re.compile(
    r"4k-time-lapse-car-parking-lot-stock-video-download-video-clip-now-istock_TyROSAGZ_mp4-(\d+)",
    re.IGNORECASE,
)


def resolve_input_dir(input_dir: Path):
    if input_dir.exists():
        return input_dir

    parts = input_dir.parts
    if parts and parts[0].lower() == "data" and Path.cwd().name.lower() == "data":
        from_project_root = Path.cwd().parent / input_dir
        if from_project_root.exists():
            return from_project_root

        without_duplicate_data = Path.cwd() / Path(*parts[1:])
        if without_duplicate_data.exists():
            return without_duplicate_data

    raise FileNotFoundError(f"Input folder not found: {input_dir}")


def load_environment(dotenv_path: Path | str = ".env"):
    return load_dotenv(dotenv_path=dotenv_path, encoding="utf-8-sig")


def parse_label(label_file: Path):
    boxes = []
    with label_file.open(encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 5:
                continue

            class_id = int(parts[0])
            coords = list(map(float, parts[1:]))

            if len(coords) == 4:
                x, y, w, h = coords
            else:
                xs = coords[0::2]
                ys = coords[1::2]
                x = (min(xs) + max(xs)) / 2
                y = (min(ys) + max(ys)) / 2
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)

            boxes.append({"class_id": class_id, "x": x, "y": y, "w": w, "h": h})
    return boxes


def extract_raw_frame_order(image_file: Path):
    match = KEEP_PATTERN.search(image_file.name)
    return int(match.group(1)) if match else None


def assign_slot_ids(boxes, row_gap=0.08, row_names="ABCDEFGHIJKLMNOPQRSTUVWXYZ", max_columns_per_row=99):
    if not boxes:
        return []

    boxes_sorted = sorted(boxes, key=lambda box: box["y"])
    rows = []
    current_row = [boxes_sorted[0]]

    for box in boxes_sorted[1:]:
        if box["y"] - current_row[-1]["y"] > row_gap:
            rows.append(current_row)
            current_row = [box]
        else:
            current_row.append(box)
    rows.append(current_row)

    assigned = []
    if len(rows) > len(row_names):
        raise ValueError(
            f"Detected {len(rows)} rows; row labels support up to {len(row_names)} rows."
        )

    for row_index, row in enumerate(rows, start=1):
        sorted_row = sorted(row, key=lambda box: box["x"])
        if len(sorted_row) > max_columns_per_row:
            raise ValueError(
                f"Row {row_index} has {len(sorted_row)} slots; "
                f"slot labels support up to {max_columns_per_row} columns."
            )

        for column_index, box in enumerate(sorted_row, start=1):
            assigned_box = dict(box)
            assigned_box["slot_id"] = f"{row_names[row_index - 1]}{column_index:02d}"
            assigned.append(assigned_box)

    return assigned


def assign_numeric_slot_ids(boxes, row_gap=0.08, max_columns_per_row=99):
    return assign_slot_ids(boxes, row_gap=row_gap, max_columns_per_row=max_columns_per_row)


def parse_frame_id(frame_id: str):
    return int(frame_id) if frame_id.isdigit() else frame_id


def frame_timestamp(frame_id, start_timestamp: int, frame_interval_seconds: int):
    if isinstance(frame_id, int):
        frame_index = frame_id
    elif str(frame_id).isdigit():
        frame_index = int(frame_id)
    else:
        frame_index = 1
    return int(start_timestamp + max(frame_index - 1, 0) * frame_interval_seconds)


def frame_payload(
    frame_id: str,
    label_file: Path,
    start_timestamp: int = DEFAULT_START_TIMESTAMP,
    frame_interval_seconds: int = DEFAULT_FRAME_INTERVAL_SECONDS,
):
    boxes = parse_label(label_file)
    boxes = assign_numeric_slot_ids(boxes)
    parsed_frame_id = parse_frame_id(frame_id)
    timestamp = frame_timestamp(parsed_frame_id, start_timestamp, frame_interval_seconds)

    return [
        {
            "frame_id": parsed_frame_id,
            "id": box["slot_id"],
            "occupied": box["class_id"],
            "timestamp": timestamp,
        }
        for box in sorted(boxes, key=lambda box: box["slot_id"])
    ]


def output_path_for(output_dir: Path, split: str, frame_id: str):
    return output_dir / f"{frame_id}.json"


def thingspeak_payload(record):
    return {
        "field1": record["frame_id"],
        "field2": record["id"],
        "field3": record["occupied"],
        "field4": record["timestamp"],
    }


def thingspeak_frame_payload(payload):
    if not payload:
        return {}

    occupied_count = sum(record["occupied"] for record in payload)
    compact_slots = [
        {"id": record["id"], "occupied": record["occupied"]}
        for record in payload
    ]
    return {
        "field1": payload[0]["frame_id"],
        "field2": payload[0]["timestamp"],
        "field3": len(payload),
        "field4": occupied_count,
        "field5": len(payload) - occupied_count,
        "field6": json.dumps(compact_slots, separators=(",", ":")),
    }


def upload_to_thingspeak(
    payload,
    thingspeak_url_env: str,
    thingspeak_api_key_env: str,
    timeout_seconds: int = 10,
    delay_seconds: float = 0,
    upload_mode: str = "slot",
    log_uploads: bool = False,
):
    if upload_mode not in THINGSPEAK_UPLOAD_MODES:
        raise ValueError(f"upload_mode must be one of {sorted(THINGSPEAK_UPLOAD_MODES)}")

    url = os.environ.get(thingspeak_url_env)
    api_key = os.environ.get(thingspeak_api_key_env)
    if not url:
        raise RuntimeError(f"Missing ThingSpeak URL env var: {thingspeak_url_env}")
    if not api_key:
        raise RuntimeError(f"Missing ThingSpeak API key env var: {thingspeak_api_key_env}")

    if upload_mode == "frame":
        payloads = [thingspeak_frame_payload(payload)] if payload else []
    else:
        payloads = [thingspeak_payload(record) for record in payload]

    responses = []
    for payload_item in payloads:
        data = {"api_key": api_key, **payload_item}
        response = requests.post(url, data=data, timeout=timeout_seconds)
        response.raise_for_status()
        if response.text == "0":
            raise RuntimeError(f"ThingSpeak rejected payload: {data}")
        responses.append(response.text)
        if log_uploads:
            if upload_mode == "frame":
                print(
                    f"Uploaded frame frame_id={payload_item['field1']} "
                    f"total_slots={payload_item['field3']} "
                    f"occupied_count={payload_item['field4']} "
                    f"response={response.text}"
                )
            else:
                print(
                    f"Uploaded slot frame_id={payload_item['field1']} "
                    f"slot_id={payload_item['field2']} "
                    f"occupied={payload_item['field3']} "
                    f"response={response.text}"
                )
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return responses


def clear_thingspeak_channel(
    thingspeak_channel_id_env: str = DEFAULT_THINGSPEAK_CHANNEL_ID_ENV,
    thingspeak_user_api_key_env: str = DEFAULT_THINGSPEAK_USER_API_KEY_ENV,
    timeout_seconds: int = 10,
):
    channel_id = os.environ.get(thingspeak_channel_id_env)
    user_api_key = os.environ.get(thingspeak_user_api_key_env)
    if not channel_id:
        raise RuntimeError(f"Missing ThingSpeak channel ID env var: {thingspeak_channel_id_env}")
    if not user_api_key:
        raise RuntimeError(f"Missing ThingSpeak user API key env var: {thingspeak_user_api_key_env}")

    url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    response = requests.delete(url, data={"api_key": user_api_key}, timeout=timeout_seconds)
    response.raise_for_status()
    return response.text


def process_frame(
    split: str,
    image_file: Path,
    label_file: Path,
    output_dir: Path,
    storage: str = "json",
    thingspeak_url_env: str = "THINGSPEAK_URL",
    thingspeak_api_key_env: str = "THINGSPEAK_API_KEY",
    thingspeak_delay_seconds: float = 0,
    frame_id: str | None = None,
    start_timestamp: int = DEFAULT_START_TIMESTAMP,
    frame_interval_seconds: int = DEFAULT_FRAME_INTERVAL_SECONDS,
    thingspeak_upload_mode: str = "slot",
):
    if not label_file.exists():
        return None

    if storage not in STORAGE_CHOICES:
        raise ValueError(f"storage must be one of {sorted(STORAGE_CHOICES)}")

    frame_id = frame_id or image_file.stem
    payload = frame_payload(
        frame_id,
        label_file,
        start_timestamp=start_timestamp,
        frame_interval_seconds=frame_interval_seconds,
    )
    out_file = output_path_for(output_dir, split, frame_id)

    if storage in {"json", "both"}:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if storage in {"thingspeak", "both"}:
        print(f"Uploading frame {frame_id} from {image_file} with {len(payload)} slots")
        upload_to_thingspeak(
            payload,
            thingspeak_url_env,
            thingspeak_api_key_env,
            delay_seconds=thingspeak_delay_seconds,
            upload_mode=thingspeak_upload_mode,
            log_uploads=True,
        )

    return out_file


def iter_frames(input_dir: Path):
    images_dir = input_dir / "images"
    labels_dir = input_dir / "labels"
    if images_dir.exists() and labels_dir.exists():
        image_files = [
            image_file
            for image_file in sorted(images_dir.iterdir())
            if image_file.is_file() and image_file.suffix.lower() in IMAGE_EXTENSIONS
        ]
        raw_images = [
            image_file
            for image_file in image_files
            if extract_raw_frame_order(image_file) is not None
        ]

        if raw_images:
            for index, image_file in enumerate(sorted(raw_images, key=extract_raw_frame_order), start=1):
                yield input_dir.name, image_file, labels_dir / f"{image_file.stem}.txt", f"{index:04d}"
            return

        for image_file in image_files:
            yield input_dir.name, image_file, labels_dir / f"{image_file.stem}.txt", image_file.stem
        return

    for split_dir in sorted(p for p in input_dir.iterdir() if p.is_dir()):
        images_dir = split_dir / "images"
        labels_dir = split_dir / "labels"
        if not images_dir.exists() or not labels_dir.exists():
            continue

        image_files = [
            image_file
            for image_file in sorted(images_dir.iterdir())
            if image_file.is_file() and image_file.suffix.lower() in IMAGE_EXTENSIONS
        ]
        raw_images = [
            image_file
            for image_file in image_files
            if extract_raw_frame_order(image_file) is not None
        ]

        if raw_images:
            for index, image_file in enumerate(sorted(raw_images, key=extract_raw_frame_order), start=1):
                yield split_dir.name, image_file, labels_dir / f"{image_file.stem}.txt", f"{index:04d}"
            continue

        for image_file in image_files:
            yield split_dir.name, image_file, labels_dir / f"{image_file.stem}.txt", image_file.stem


def process_once(
    input_dir: Path,
    output_dir: Path,
    storage: str = "json",
    thingspeak_url_env: str = "THINGSPEAK_URL",
    thingspeak_api_key_env: str = "THINGSPEAK_API_KEY",
    thingspeak_delay_seconds: float = 0,
    max_frames: int | None = None,
    start_timestamp: int = DEFAULT_START_TIMESTAMP,
    frame_interval_seconds: int = DEFAULT_FRAME_INTERVAL_SECONDS,
    thingspeak_upload_mode: str = "slot",
    frame_upload_interval: float = 0,
):
    written = []
    skipped = 0

    for split, image_file, label_file, frame_id in iter_frames(input_dir):
        out_file = process_frame(
            split,
            image_file,
            label_file,
            output_dir,
            storage=storage,
            thingspeak_url_env=thingspeak_url_env,
            thingspeak_api_key_env=thingspeak_api_key_env,
            thingspeak_delay_seconds=thingspeak_delay_seconds,
            frame_id=frame_id,
            start_timestamp=start_timestamp,
            frame_interval_seconds=frame_interval_seconds,
            thingspeak_upload_mode=thingspeak_upload_mode,
        )
        if out_file is None:
            skipped += 1
        else:
            written.append(out_file)
            if (
                frame_upload_interval > 0
                and storage in {"thingspeak", "both"}
                and thingspeak_upload_mode == "frame"
            ):
                time.sleep(frame_upload_interval)
            if max_frames is not None and len(written) >= max_frames:
                break

    return written, skipped


def watch(
    input_dir: Path,
    output_dir: Path,
    interval_seconds: float,
    storage: str = "json",
    thingspeak_url_env: str = "THINGSPEAK_URL",
    thingspeak_api_key_env: str = "THINGSPEAK_API_KEY",
    thingspeak_delay_seconds: float = 0,
    start_timestamp: int = DEFAULT_START_TIMESTAMP,
    frame_interval_seconds: int = DEFAULT_FRAME_INTERVAL_SECONDS,
    thingspeak_upload_mode: str = "slot",
    frame_upload_interval: float = 0,
):
    seen = set()
    print(f"Watching {input_dir} -> {output_dir}")
    print("Slot rule: row letter + zero-padded column. Example: A01=row A col 1, B02=row B col 2.")

    while True:
        for split, image_file, label_file, frame_id in iter_frames(input_dir):
            key = (split, image_file.resolve())
            if key in seen:
                continue

            if not label_file.exists():
                continue

            out_file = process_frame(
                split,
                image_file,
                label_file,
                output_dir,
                storage=storage,
                thingspeak_url_env=thingspeak_url_env,
                thingspeak_api_key_env=thingspeak_api_key_env,
                thingspeak_delay_seconds=thingspeak_delay_seconds,
                frame_id=frame_id,
                start_timestamp=start_timestamp,
                frame_interval_seconds=frame_interval_seconds,
                thingspeak_upload_mode=thingspeak_upload_mode,
            )
            if out_file:
                print(f"Processed {image_file}")
                seen.add(key)
                if (
                    frame_upload_interval > 0
                    and storage in {"thingspeak", "both"}
                    and thingspeak_upload_mode == "frame"
                ):
                    time.sleep(frame_upload_interval)

        time.sleep(interval_seconds)


def main():
    load_environment()

    parser = argparse.ArgumentParser(
        description="Convert cleaned YOLO parking frames into one JSON payload file per frame."
    )
    parser.add_argument(
        "--input",
        default="data/content/dataset_clean",
        help="Clean dataset folder containing split/images and split/labels.",
    )
    parser.add_argument(
        "--output",
        default="data/stream_json_outputs",
        help="Folder where per-frame JSON files will be written.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously poll input folder and process new image+label pairs.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds when --watch is enabled.",
    )
    parser.add_argument(
        "--storage",
        choices=sorted(STORAGE_CHOICES),
        default="json",
        help="Choose where to send processed frame payloads.",
    )
    parser.add_argument(
        "--thingspeak-url-env",
        default="THINGSPEAK_URL",
        help="Environment variable name containing the ThingSpeak update URL.",
    )
    parser.add_argument(
        "--thingspeak-api-key-env",
        default="THINGSPEAK_API_KEY",
        help="Environment variable name containing the ThingSpeak API key.",
    )
    parser.add_argument(
        "--thingspeak-channel-id-env",
        default=DEFAULT_THINGSPEAK_CHANNEL_ID_ENV,
        help="Environment variable name containing the ThingSpeak channel ID.",
    )
    parser.add_argument(
        "--thingspeak-user-api-key-env",
        default=DEFAULT_THINGSPEAK_USER_API_KEY_ENV,
        help="Environment variable name containing the ThingSpeak user API key for clearing channel feeds.",
    )
    parser.add_argument(
        "--clear-thingspeak-before-upload",
        action="store_true",
        help="Clear all existing ThingSpeak channel feeds once before uploading data.",
    )
    parser.add_argument(
        "--thingspeak-delay",
        type=float,
        default=16.0,
        help="Delay in seconds between ThingSpeak updates.",
    )
    parser.add_argument(
        "--thingspeak-upload-mode",
        choices=sorted(THINGSPEAK_UPLOAD_MODES),
        default="slot",
        help="Upload one ThingSpeak update per frame or one update per slot.",
    )
    parser.add_argument(
        "--frame-upload-interval",
        type=float,
        default=0,
        help="Delay in seconds after uploading each frame in frame upload mode.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum number of frames to process in one-shot mode.",
    )
    parser.add_argument(
        "--start-timestamp",
        type=int,
        default=DEFAULT_START_TIMESTAMP,
        help="Unix timestamp used for the first frame.",
    )
    parser.add_argument(
        "--frame-interval-seconds",
        type=int,
        default=DEFAULT_FRAME_INTERVAL_SECONDS,
        help="Synthetic time difference in seconds between consecutive frames.",
    )
    args = parser.parse_args()

    input_dir = resolve_input_dir(Path(args.input))
    output_dir = Path(args.output)

    if args.clear_thingspeak_before_upload and args.storage in {"thingspeak", "both"}:
        clear_thingspeak_channel(
            args.thingspeak_channel_id_env,
            args.thingspeak_user_api_key_env,
        )
        print("Cleared ThingSpeak channel feeds before upload")

    if args.watch:
        watch(
            input_dir,
            output_dir,
            args.interval,
            storage=args.storage,
            thingspeak_url_env=args.thingspeak_url_env,
            thingspeak_api_key_env=args.thingspeak_api_key_env,
            thingspeak_delay_seconds=args.thingspeak_delay,
            start_timestamp=args.start_timestamp,
            frame_interval_seconds=args.frame_interval_seconds,
            thingspeak_upload_mode=args.thingspeak_upload_mode,
            frame_upload_interval=args.frame_upload_interval,
        )
        return

    written, skipped = process_once(
        input_dir,
        output_dir,
        storage=args.storage,
        thingspeak_url_env=args.thingspeak_url_env,
        thingspeak_api_key_env=args.thingspeak_api_key_env,
        thingspeak_delay_seconds=args.thingspeak_delay,
        max_frames=args.max_frames,
        start_timestamp=args.start_timestamp,
        frame_interval_seconds=args.frame_interval_seconds,
        thingspeak_upload_mode=args.thingspeak_upload_mode,
        frame_upload_interval=args.frame_upload_interval,
    )
    print("Slot rule: row letter + zero-padded column. Example: A01=row A col 1, B02=row B col 2.")
    print(f"Processed {len(written)} frame files")
    if args.storage in {"json", "both"}:
        print(f"Wrote JSON files to {output_dir}")
    if skipped:
        print(f"Skipped {skipped} images without matching label files")


if __name__ == "__main__":
    main()
