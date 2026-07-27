import argparse
import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from Crypto.Cipher import AES

from ml_service.features import FEATURE_NAMES, RollingFeatureBuilder
from ml_service.predictor import Predictor
from ml_service.schema import ensure_ml_schema
from ml_service.simulation_repository import connect_database
from ml_service.slots import (
    CAPACITY,
    empty_slot_state,
    normalize_slot_state,
)


LOCAL_TIMESTAMP_TIMEZONE = timezone(timedelta(hours=7))


def unix_timestamp_to_datetime(value):
    return datetime.fromtimestamp(
        int(value),
        tz=LOCAL_TIMESTAMP_TIMEZONE,
    ).replace(tzinfo=None)


def normalize_encryption_key(key):
    if key is None:
        return None
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    if len(key_bytes) in (16, 24, 32):
        return key_bytes
    return hashlib.sha256(key_bytes).digest()


def decode_mqtt_message(payload, encryption_key=None):
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        key = normalize_encryption_key(encryption_key)
        if key is None:
            raise ValueError(
                "MQTT payload is not JSON and no encryption key is configured"
            )
        packaged = base64.b64decode(payload)
        if len(packaged) < 28:
            raise ValueError("encrypted MQTT payload is too short")
        nonce = packaged[:12]
        tag = packaged[12:28]
        ciphertext = packaged[28:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return json.loads(plaintext.decode("utf-8"))


@dataclass(frozen=True)
class ProcessResult:
    inserted: bool
    unknown_slot_ids: tuple[str, ...]
    capacity: int
    occupied_count: int


class PredictionRepository:
    def __init__(self, conn):
        self.conn = conn

    def prediction_exists(self, source_event_id, model_version):
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM parking_fill_predictions
                WHERE source_event_id = %s AND model_version = %s
                """,
                (source_event_id, model_version),
            )
            return cursor.fetchone() is not None

    def insert_prediction(
        self,
        source_event_id,
        source_frame_id,
        observed_at,
        model_version,
        occupied_count,
        prediction,
    ):
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO parking_fill_predictions (
                        source_event_id, source_frame_id, observed_at,
                        model_version, occupied_count, capacity,
                        predicted_seconds_to_full, predicted_fill_at,
                        inference_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_event_id, model_version)
                    DO NOTHING
                    RETURNING prediction_id
                    """,
                    (
                        source_event_id,
                        source_frame_id,
                        observed_at,
                        model_version,
                        occupied_count,
                        CAPACITY,
                        prediction.seconds_to_full,
                        prediction.predicted_fill_at,
                        prediction.inference_ms,
                    ),
                )
                inserted = cursor.fetchone() is not None
            self.conn.commit()
            return inserted
        except Exception:
            self.conn.rollback()
            raise


class PredictionService:
    def __init__(self, predictor, repository):
        self.predictor = predictor
        self.repository = repository
        self.state = empty_slot_state()
        self.feature_builder = RollingFeatureBuilder()

    def process_message(self, message):
        source_event_id = str(message["source_event_id"])
        source_frame_id = int(message["frame_id"])
        payload = message.get("payload")
        if not isinstance(payload, list) or not payload:
            raise ValueError("payload must not be empty")

        if self.repository.prediction_exists(
            source_event_id,
            self.predictor.model_version,
        ):
            return ProcessResult(
                False,
                (),
                CAPACITY,
                sum(self.state.values()),
            )

        timestamps = {int(record["timestamp"]) for record in payload}
        if len(timestamps) != 1:
            raise ValueError("all frame records must share one timestamp")
        observed_at = unix_timestamp_to_datetime(timestamps.pop())
        state, unknown = normalize_slot_state(self.state, payload)
        self.feature_builder.update(observed_at, state)
        features = self.feature_builder.vector(observed_at)
        prediction = self.predictor.predict(
            observed_at,
            [features[name] for name in FEATURE_NAMES],
        )
        inserted = self.repository.insert_prediction(
            source_event_id,
            source_frame_id,
            observed_at,
            self.predictor.model_version,
            sum(state.values()),
            prediction,
        )
        self.state = state
        return ProcessResult(
            inserted,
            tuple(unknown),
            CAPACITY,
            sum(state.values()),
        )


def configure_mqtt_auth(client, username=None, password=None):
    if username:
        client.username_pw_set(username, password or None)


def connect_mqtt_with_retry(
    client,
    mqtt_host,
    mqtt_port,
    retry_delay_seconds=2,
):
    while True:
        try:
            return client.connect(mqtt_host, mqtt_port, keepalive=60)
        except OSError as exc:
            print(
                f"Prediction service waiting for MQTT "
                f"{mqtt_host}:{mqtt_port}: {exc}",
                flush=True,
            )
            time.sleep(retry_delay_seconds)


def run_service(
    mqtt_host,
    mqtt_port,
    topic,
    model_dir,
    mqtt_username=None,
    mqtt_password=None,
    mqtt_client_id="parking-prediction-service",
    encryption_key=None,
):
    conn = connect_database()
    ensure_ml_schema(conn)
    predictor = Predictor.load(model_dir)
    service = PredictionService(
        predictor,
        PredictionRepository(conn),
    )
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=mqtt_client_id,
    )
    configure_mqtt_auth(client, mqtt_username, mqtt_password)

    def on_connect(client, userdata, flags, reason_code, properties):
        print(
            f"Prediction service connected reason_code={reason_code}; "
            f"subscribing topic={topic}",
            flush=True,
        )
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        try:
            message = decode_mqtt_message(msg.payload, encryption_key)
            result = service.process_message(message)
            print(
                f"Prediction frame_id={message.get('frame_id')} "
                f"inserted={result.inserted} "
                f"occupied={result.occupied_count}/{result.capacity}",
                flush=True,
            )
            if result.unknown_slot_ids:
                print(
                    "Prediction ignored unknown slots="
                    + ",".join(result.unknown_slot_ids),
                    flush=True,
                )
        except Exception as exc:
            print(
                f"Prediction failed on topic={msg.topic}: {exc}",
                flush=True,
            )

    client.on_connect = on_connect
    client.on_message = on_message
    connect_mqtt_with_retry(client, mqtt_host, mqtt_port)
    try:
        client.loop_forever()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Predict parking fill ETA from MQTT frames."
    )
    parser.add_argument(
        "--mqtt-host",
        default=os.environ.get("MQTT_HOST", "mqtt-broker"),
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=int(os.environ.get("MQTT_PORT", "1883")),
    )
    parser.add_argument(
        "--topic",
        default=os.environ.get("MQTT_TOPIC", "parking/frames"),
    )
    parser.add_argument(
        "--mqtt-username",
        default=os.environ.get("MQTT_USERNAME"),
    )
    parser.add_argument(
        "--mqtt-password",
        default=os.environ.get("MQTT_PASSWORD"),
    )
    parser.add_argument(
        "--mqtt-client-id",
        default=os.environ.get(
            "MQTT_CLIENT_ID",
            "parking-prediction-service",
        ),
    )
    parser.add_argument(
        "--model-dir",
        default=os.environ.get(
            "MODEL_DIR",
            "models/parking_fill_eta",
        ),
    )
    parser.add_argument(
        "--encryption-key",
        default=os.environ.get("ENCRYPTION_KEY"),
    )
    args = parser.parse_args()
    run_service(
        args.mqtt_host,
        args.mqtt_port,
        args.topic,
        args.model_dir,
        args.mqtt_username,
        args.mqtt_password,
        args.mqtt_client_id,
        args.encryption_key,
    )


if __name__ == "__main__":
    main()
