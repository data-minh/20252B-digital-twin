import json
import sys
import importlib.util
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

import stream_clean_to_json as stream


def load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_frame(root: Path, split: str = "incoming", frame_id: str = "0001", label_text: str | None = None):
    images = root / split / "images"
    labels = root / split / "labels"
    images.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    image = images / f"{frame_id}.jpg"
    image.write_bytes(b"fake image")
    if label_text is not None:
        (labels / f"{frame_id}.txt").write_text(label_text, encoding="utf-8")
    return image, labels


def test_payload_uses_notebook_style_slot_ids(tmp_path):
    image, labels = write_frame(
        tmp_path,
        label_text=(
            "1 0.10 0.10 0.05 0.05\n"
            "0 0.20 0.10 0.05 0.05\n"
            "1 0.10 0.30 0.05 0.05\n"
        ),
    )

    payload = stream.frame_payload(image.stem, labels / "0001.txt", start_timestamp=1634567890)

    assert payload == [
        {"frame_id": 1, "id": "A01", "occupied": 1, "timestamp": 1634567890},
        {"frame_id": 1, "id": "A02", "occupied": 0, "timestamp": 1634567890},
        {"frame_id": 1, "id": "B01", "occupied": 1, "timestamp": 1634567890},
    ]


def test_payload_timestamp_increments_by_frame_order(tmp_path):
    image, labels = write_frame(tmp_path, frame_id="0002", label_text="1 0.10 0.10 0.05 0.05\n")

    payload = stream.frame_payload(
        image.stem,
        labels / "0002.txt",
        start_timestamp=1634567890,
        frame_interval_seconds=10,
    )

    assert payload == [{"frame_id": 2, "id": "A01", "occupied": 1, "timestamp": 1634567900}]


def test_default_payload_timestamp_increments_one_second_per_frame(tmp_path):
    image, labels = write_frame(tmp_path, frame_id="0710", label_text="1 0.10 0.10 0.05 0.05\n")

    payload = stream.frame_payload(image.stem, labels / "0710.txt", start_timestamp=1634567890)

    assert payload == [{"frame_id": 710, "id": "A01", "occupied": 1, "timestamp": 1634568599}]


def test_payload_uses_current_time_when_start_timestamp_is_missing(tmp_path, monkeypatch):
    image, labels = write_frame(tmp_path, frame_id="0003", label_text="1 0.10 0.10 0.05 0.05\n")
    monkeypatch.setattr(stream.time, "time", lambda: 1712345678)

    payload = stream.frame_payload(image.stem, labels / "0003.txt")

    assert payload[0]["timestamp"] == 1712345678


def test_process_frame_skips_image_without_label(tmp_path):
    image, labels = write_frame(tmp_path, label_text=None)

    result = stream.process_frame("incoming", image, labels / "0001.txt", tmp_path / "out")

    assert result is None
    assert not list((tmp_path / "out").rglob("*.json"))


def test_process_frame_uploads_template_records_to_thingspeak_by_default(tmp_path, monkeypatch):
    image, labels = write_frame(tmp_path, label_text="1 0.10 0.10 0.05 0.05\n0 0.20 0.10 0.05 0.05\n")
    monkeypatch.setenv("THINGSPEAK_URL", "https://example.test/update")
    monkeypatch.setenv("THINGSPEAK_API_KEY", "secret")
    calls = []

    def fake_post(url, data, timeout):
        calls.append({"url": url, "data": data, "timeout": timeout})

        class Response:
            status_code = 200
            text = "123"

            def raise_for_status(self):
                return None

        return Response()

    monkeypatch.setattr(stream.requests, "post", fake_post)

    result = stream.process_frame(
        "incoming",
        image,
        labels / "0001.txt",
        tmp_path / "out",
        storage="both",
        thingspeak_url_env="THINGSPEAK_URL",
        thingspeak_api_key_env="THINGSPEAK_API_KEY",
    )

    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved == [
        {"frame_id": 1, "id": "A01", "occupied": 1, "timestamp": 1634567890},
        {"frame_id": 1, "id": "A02", "occupied": 0, "timestamp": 1634567890},
    ]
    assert calls == [
        {
            "url": "https://example.test/update",
            "data": {
                "api_key": "secret",
                "field1": 1,
                "field2": "A01",
                "field3": 1,
                "field4": 1634567890,
            },
            "timeout": 10,
        },
        {
            "url": "https://example.test/update",
            "data": {
                "api_key": "secret",
                "field1": 1,
                "field2": "A02",
                "field3": 0,
                "field4": 1634567890,
            },
            "timeout": 10,
        }
    ]


def test_process_frame_can_upload_slots_to_thingspeak_when_requested(tmp_path, monkeypatch):
    image, labels = write_frame(tmp_path, label_text="1 0.10 0.10 0.05 0.05\n0 0.20 0.10 0.05 0.05\n")
    monkeypatch.setenv("THINGSPEAK_URL", "https://example.test/update")
    monkeypatch.setenv("THINGSPEAK_API_KEY", "secret")
    calls = []

    def fake_post(url, data, timeout):
        calls.append({"url": url, "data": data, "timeout": timeout})

        class Response:
            text = "123"

            def raise_for_status(self):
                return None

        return Response()

    monkeypatch.setattr(stream.requests, "post", fake_post)

    stream.process_frame(
        "incoming",
        image,
        labels / "0001.txt",
        tmp_path / "out",
        storage="thingspeak",
        thingspeak_url_env="THINGSPEAK_URL",
        thingspeak_api_key_env="THINGSPEAK_API_KEY",
        thingspeak_upload_mode="slot",
    )

    assert [call["data"] for call in calls] == [
        {"api_key": "secret", "field1": 1, "field2": "A01", "field3": 1, "field4": 1634567890},
        {"api_key": "secret", "field1": 1, "field2": "A02", "field3": 0, "field4": 1634567890},
    ]


def test_process_frame_logs_uploaded_slots(tmp_path, monkeypatch, capsys):
    image, labels = write_frame(tmp_path, label_text="1 0.10 0.10 0.05 0.05\n0 0.20 0.10 0.05 0.05\n")
    monkeypatch.setenv("THINGSPEAK_URL", "https://example.test/update")
    monkeypatch.setenv("THINGSPEAK_API_KEY", "secret")

    def fake_post(url, data, timeout):
        class Response:
            text = f"entry-{data['field2']}"

            def raise_for_status(self):
                return None

        return Response()

    monkeypatch.setattr(stream.requests, "post", fake_post)

    stream.process_frame(
        "incoming",
        image,
        labels / "0001.txt",
        tmp_path / "out",
        storage="thingspeak",
        thingspeak_upload_mode="slot",
    )

    output = capsys.readouterr().out
    assert f"Uploading frame 0001 from {image} with 2 slots" in output
    assert "Uploaded slot frame_id=1 slot_id=A01 occupied=1 response=entry-A01" in output
    assert "Uploaded slot frame_id=1 slot_id=A02 occupied=0 response=entry-A02" in output


def test_camera_mqtt_message_wraps_frame_payload():
    camera = load_module_from_path("camera_to_mqtt", Path("camera_device/camera_to_mqtt.py"))
    payload = [{"frame_id": 1, "id": "A01", "occupied": 1, "timestamp": 1634567890}]

    message = camera.frame_message("train", "0001", "data/content/dataset/train/images/0001.jpg", payload)

    assert message == {
        "split": "train",
        "frame_id": 1,
        "source_frame_id": "0001",
        "image": "data/content/dataset/train/images/0001.jpg",
        "payload": payload,
    }


def test_camera_restarts_dataset_from_first_frame_when_looping(tmp_path, monkeypatch):
    camera = load_module_from_path("camera_to_mqtt", Path("camera_device/camera_to_mqtt.py"))
    image_1, labels = write_frame(tmp_path, split="train", frame_id="0001", label_text="1 0.10 0.10 0.05 0.05\n")
    image_2, _ = write_frame(tmp_path, split="train", frame_id="0002", label_text="0 0.10 0.10 0.05 0.05\n")
    published = []

    class FakePublishResult:
        def wait_for_publish(self):
            return None

    class FakeClient:
        def connect(self, mqtt_host, mqtt_port, keepalive):
            return None

        def loop_start(self):
            return None

        def publish(self, topic, payload, qos):
            published.append(json.loads(payload))
            return FakePublishResult()

        def loop_stop(self):
            return None

        def disconnect(self):
            return None

    monkeypatch.setattr(camera.mqtt, "Client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(camera.time, "sleep", lambda seconds: None)

    count = camera.publish_dataset(
        input_dir=tmp_path,
        mqtt_host="mqtt-broker",
        mqtt_port=1883,
        topic="parking/frames",
        start_timestamp=1634567890,
        frame_interval_seconds=1,
        publish_interval_seconds=0,
        max_frames=3,
        loop_dataset=True,
    )

    assert count == 3
    assert [message["frame_id"] for message in published] == [1, 2, 1]
    assert [message["source_frame_id"] for message in published] == ["0001", "0002", "0001"]
    assert [message["image"] for message in published] == [str(image_1), str(image_2), str(image_1)]
    assert [message["payload"][0]["timestamp"] for message in published] == [1634567890, 1634567891, 1634567890]


def test_camera_configures_mqtt_username_and_password():
    camera = load_module_from_path("camera_to_mqtt", Path("camera_device/camera_to_mqtt.py"))
    calls = []

    class FakeClient:
        def username_pw_set(self, username, password):
            calls.append((username, password))

    client = FakeClient()

    camera.configure_mqtt_auth(client, "camera_pub", "pub-secret")

    assert calls == [("camera_pub", "pub-secret")]


def test_camera_retries_mqtt_connect_until_broker_is_available(monkeypatch):
    camera = load_module_from_path("camera_to_mqtt", Path("camera_device/camera_to_mqtt.py"))
    calls = []

    class FakeClient:
        def connect(self, mqtt_host, mqtt_port, keepalive):
            calls.append((mqtt_host, mqtt_port, keepalive))
            if len(calls) == 1:
                raise OSError("broker not ready")
            return None

    monkeypatch.setattr(camera.time, "sleep", lambda seconds: None)

    camera.connect_mqtt_with_retry(FakeClient(), "mqtt-broker", 1883)

    assert calls == [("mqtt-broker", 1883, 60), ("mqtt-broker", 1883, 60)]


def test_thingspeak_sink_uploads_received_frame_payload(monkeypatch):
    sink = load_module_from_path("mqtt_to_thingspeak", Path("thingspeak_sink/mqtt_to_thingspeak.py"))
    calls = []

    def fake_upload(payload, **kwargs):
        calls.append({"payload": payload, **kwargs})
        return ["123"]

    monkeypatch.setattr(sink.stream, "upload_to_thingspeak", fake_upload)
    message = {
        "frame_id": 1,
        "payload": [{"frame_id": 1, "id": "A01", "occupied": 1, "timestamp": 1634567890}],
    }

    responses = sink.upload_frame_message(
        message,
        thingspeak_url_env="TS_URL",
        thingspeak_api_key_env="TS_KEY",
        thingspeak_delay_seconds=16,
        thingspeak_upload_mode="slot",
    )

    assert responses == ["123"]
    assert calls == [
        {
            "payload": message["payload"],
            "thingspeak_url_env": "TS_URL",
            "thingspeak_api_key_env": "TS_KEY",
            "delay_seconds": 16,
            "upload_mode": "slot",
            "log_uploads": True,
        }
    ]


def test_clear_thingspeak_channel_deletes_channel_feeds(monkeypatch):
    monkeypatch.setenv("THINGSPEAK_CHANNEL_ID", "3410910")
    monkeypatch.setenv("THINGSPEAK_USER_API_KEY", "user-secret")
    calls = []

    def fake_delete(url, data, timeout):
        calls.append({"url": url, "data": data, "timeout": timeout})

        class Response:
            text = "[]"

            def raise_for_status(self):
                return None

        return Response()

    monkeypatch.setattr(stream.requests, "delete", fake_delete)

    result = stream.clear_thingspeak_channel(
        "THINGSPEAK_CHANNEL_ID",
        "THINGSPEAK_USER_API_KEY",
    )

    assert result == "[]"
    assert calls == [
        {
            "url": "https://api.thingspeak.com/channels/3410910/feeds.json",
            "data": {"api_key": "user-secret"},
            "timeout": 10,
        }
    ]


def test_load_environment_accepts_env_file_with_utf8_bom(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('\ufeffTHINGSPEAK_API_KEY="write-secret"\n', encoding="utf-8")
    monkeypatch.delenv("THINGSPEAK_API_KEY", raising=False)

    stream.load_environment(env_file)

    assert stream.os.environ["THINGSPEAK_API_KEY"] == "write-secret"


def test_main_clears_thingspeak_before_one_shot_upload(tmp_path, monkeypatch):
    input_dir = tmp_path / "dataset"
    input_dir.mkdir()
    output_dir = tmp_path / "out"
    events = []

    def fake_clear(channel_id_env, user_api_key_env):
        events.append(("clear", channel_id_env, user_api_key_env))
        return "[]"

    def fake_process_once(input_path, output_path, **kwargs):
        events.append(("process_once", input_path, output_path, kwargs["storage"]))
        return [], 0

    monkeypatch.setattr(stream, "clear_thingspeak_channel", fake_clear)
    monkeypatch.setattr(stream, "process_once", fake_process_once)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stream_clean_to_json.py",
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--storage",
            "thingspeak",
            "--clear-thingspeak-before-upload",
        ],
    )

    stream.main()

    assert events == [
        ("clear", "THINGSPEAK_CHANNEL_ID", "THINGSPEAK_USER_API_KEY"),
        ("process_once", input_dir, output_dir, "thingspeak"),
    ]


def test_iter_frames_accepts_split_folder_directly(tmp_path):
    image, labels = write_frame(tmp_path, split="train", label_text="1 0.10 0.10 0.05 0.05\n")

    frames = list(stream.iter_frames(tmp_path / "train"))

    assert frames == [("train", image, labels / "0001.txt", "0001")]


def test_resolve_input_path_accepts_data_prefix_when_running_inside_data_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    data_dir = project / "data"
    target = data_dir / "content" / "dataset_clean" / "train"
    target.mkdir(parents=True)
    monkeypatch.chdir(data_dir)

    resolved = stream.resolve_input_dir(Path("data/content/dataset_clean/train"))

    assert resolved == target


def test_process_once_can_limit_number_of_frames(tmp_path):
    write_frame(tmp_path, split="train", frame_id="0001", label_text="1 0.10 0.10 0.05 0.05\n")
    write_frame(tmp_path, split="train", frame_id="0002", label_text="1 0.10 0.10 0.05 0.05\n")

    written, skipped = stream.process_once(tmp_path, tmp_path / "out", max_frames=1)

    assert skipped == 0
    assert len(written) == 1
    assert written[0].name == "0001.json"


def test_iter_frames_filters_and_renumbers_raw_roboflow_split(tmp_path):
    images = tmp_path / "train" / "images"
    labels = tmp_path / "train" / "labels"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)

    raw_10 = images / "4k-time-lapse-car-parking-lot-stock-video-download-video-clip-now-istock_TyROSAGZ_mp4-10_jpg.rf.aaa.jpg"
    raw_2 = images / "4k-time-lapse-car-parking-lot-stock-video-download-video-clip-now-istock_TyROSAGZ_mp4-2_jpg.rf.bbb.jpg"
    other = images / "not-the-target-video-999.jpg"
    raw_10.write_bytes(b"fake image")
    raw_2.write_bytes(b"fake image")
    other.write_bytes(b"fake image")
    (labels / f"{raw_10.stem}.txt").write_text("1 0.10 0.10 0.05 0.05\n", encoding="utf-8")
    (labels / f"{raw_2.stem}.txt").write_text("0 0.10 0.10 0.05 0.05\n", encoding="utf-8")
    (labels / f"{other.stem}.txt").write_text("1 0.10 0.10 0.05 0.05\n", encoding="utf-8")

    frames = list(stream.iter_frames(tmp_path / "train"))

    assert frames == [
        ("train", raw_2, labels / f"{raw_2.stem}.txt", "0001"),
        ("train", raw_10, labels / f"{raw_10.stem}.txt", "0002"),
    ]


def test_process_once_outputs_clean_numbering_from_raw_dataset(tmp_path):
    images = tmp_path / "test" / "images"
    labels = tmp_path / "test" / "labels"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    raw_image = images / "4k-time-lapse-car-parking-lot-stock-video-download-video-clip-now-istock_TyROSAGZ_mp4-7_jpg.rf.abc.jpg"
    raw_image.write_bytes(b"fake image")
    (labels / f"{raw_image.stem}.txt").write_text("1 0.10 0.10 0.05 0.05\n", encoding="utf-8")

    written, skipped = stream.process_once(tmp_path, tmp_path / "out")

    payload = json.loads((tmp_path / "out" / "0001.json").read_text(encoding="utf-8"))
    assert skipped == 0
    assert written == [tmp_path / "out" / "0001.json"]
    assert payload == [{"frame_id": 1, "id": "A01", "occupied": 1, "timestamp": 1634567890}]


def test_postgres_history_row_uses_hash_unique_id_and_timestamps():
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    record = {"frame_id": 2, "id": "A01", "occupied": 1, "timestamp": 1634567900}

    row = sink.history_row(record)

    expected_unique_id = sha256("2:A01".encode("utf-8")).hexdigest()
    expected_timestamp = datetime(2021, 10, 18, 21, 38, 20)
    assert row == {
        "unique_id": expected_unique_id,
        "frame_id": 2,
        "id": "A01",
        "occupied": 1,
        "timestamp": expected_timestamp,
        "startdate": expected_timestamp,
        "enddate": None,
        "status": "active",
    }


def test_postgres_scd2_closes_changed_active_row_and_inserts_new_version():
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    record = {"frame_id": 2, "id": "A01", "occupied": 1, "timestamp": 1634567900}
    current = {"unique_id": "old-version", "occupied": 0}

    actions = sink.scd2_actions(current, sink.history_row(record))

    effective_time = datetime(2021, 10, 18, 21, 38, 20)
    assert actions == [
        {
            "action": "close",
            "unique_id": "old-version",
            "enddate": effective_time,
            "status": "inactive",
        },
        {
            "action": "insert",
            "row": sink.history_row(record),
        },
    ]


def test_postgres_scd2_ignores_unchanged_active_row():
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    record = {"frame_id": 3, "id": "A01", "occupied": 1, "timestamp": 1634567910}
    current = {"unique_id": "current-version", "occupied": 1}

    actions = sink.scd2_actions(current, sink.history_row(record))

    assert actions == []


def test_postgres_connect_prefers_database_url(monkeypatch):
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    calls = []

    class FakePsycopg2:
        @staticmethod
        def connect(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return "connection"

    monkeypatch.setattr(sink, "psycopg2", FakePsycopg2)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@example.test/db?sslmode=require")
    monkeypatch.setenv("POSTGRES_HOST", "ignored-host")

    connection = sink.connect_postgres()

    assert connection == "connection"
    assert calls == [
        {
            "args": ("postgresql://user:secret@example.test/db?sslmode=require",),
            "kwargs": {},
        }
    ]


def test_postgres_sink_configures_mqtt_username_and_password():
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    calls = []

    class FakeClient:
        def username_pw_set(self, username, password):
            calls.append((username, password))

    client = FakeClient()

    sink.configure_mqtt_auth(client, "postgres_sub", "sub-secret")

    assert calls == [("postgres_sub", "sub-secret")]


def test_postgres_applies_frame_payload_in_one_transaction(monkeypatch):
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))
    events = []

    class FakeCursor:
        def __enter__(self):
            events.append("cursor-open")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("cursor-close")

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            events.append("commit")

    def fake_fetch(cursor, slot_ids):
        events.append(("fetch", tuple(slot_ids)))
        return {
            "A01": {"unique_id": "old-a01", "occupied": 0},
            "A02": {"unique_id": "old-a02", "occupied": 1},
        }

    def fake_close(cursor, action):
        events.append(("close", action["unique_id"]))

    def fake_insert(cursor, row):
        events.append(("insert", row["id"]))

    monkeypatch.setattr(sink, "fetch_active_rows", fake_fetch)
    monkeypatch.setattr(sink, "close_history_row", fake_close)
    monkeypatch.setattr(sink, "insert_history_row", fake_insert)

    summary = sink.apply_scd2_records(
        FakeConn(),
        [
            {"frame_id": 2, "id": "A01", "occupied": 1, "timestamp": 1634567900},
            {"frame_id": 2, "id": "A02", "occupied": 1, "timestamp": 1634567900},
            {"frame_id": 2, "id": "A03", "occupied": 0, "timestamp": 1634567900},
        ],
    )

    assert summary == {"inserted": 2, "closed": 1, "skipped": 1}
    assert events == [
        "cursor-open",
        ("fetch", ("A01", "A02", "A03")),
        ("close", "old-a01"),
        ("insert", "A01"),
        ("insert", "A03"),
        "cursor-close",
        "commit",
    ]


def test_postgres_schema_uses_timestamp_without_time_zone():
    sink = load_module_from_path("postgres_sink", Path("postgres_sink/postgres_sink.py"))

    statements = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, sql, params=None):
            statements.append(" ".join(sql.split()))

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

    sink.ensure_schema(FakeConn())

    schema_sql = " ".join(statements)
    assert '"timestamp" TIMESTAMP(6) NOT NULL' in schema_sql
    assert 'startdate TIMESTAMP(6) NOT NULL' in schema_sql
    assert 'enddate TIMESTAMP(6) NULL' in schema_sql
    assert 'TIMESTAMPTZ' not in schema_sql


def test_mqtt_entrypoint_makes_generated_auth_files_readable_by_mosquitto():
    entrypoint = Path("mqtt_broker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'chown mosquitto:mosquitto "$PASSWORD_FILE" "$ACL_FILE"' in entrypoint
