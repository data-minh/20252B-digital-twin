from datetime import datetime, timedelta

import pytest

from ml_service.prediction_service import (
    PredictionRepository,
    PredictionService,
)
from ml_service.predictor import Prediction


class FakePredictor:
    model_version = "test-v1"

    def __init__(self):
        self.calls = 0

    def predict(self, observed_at, ordered_features):
        self.calls += 1
        return Prediction(
            seconds_to_full=900,
            predicted_fill_at=observed_at + timedelta(seconds=900),
            inference_ms=1.5,
        )


class FakeRepository:
    def __init__(self):
        self.keys = set()
        self.rows = []

    def prediction_exists(self, source_event_id, model_version):
        return (source_event_id, model_version) in self.keys

    def insert_prediction(
        self,
        source_event_id,
        source_frame_id,
        observed_at,
        model_version,
        occupied_count,
        prediction,
    ):
        key = (source_event_id, model_version)
        if key in self.keys:
            return False
        self.keys.add(key)
        self.rows.append(
            {
                "source_event_id": source_event_id,
                "source_frame_id": source_frame_id,
                "observed_at": observed_at,
                "occupied_count": occupied_count,
                "prediction": prediction,
            }
        )
        return True


def valid_message():
    return {
        "source_event_id": "session-a:1:0001",
        "frame_id": 1,
        "payload": [
            {
                "frame_id": 1,
                "id": "A01",
                "occupied": 1,
                "timestamp": 1634567890,
            }
        ],
    }


def test_frame_creates_one_prediction():
    predictor = FakePredictor()
    repository = FakeRepository()
    service = PredictionService(predictor, repository)

    result = service.process_message(valid_message())

    assert result.inserted is True
    assert result.capacity == 60
    assert result.occupied_count == 1
    assert len(repository.rows) == 1
    assert predictor.calls == 1


def test_duplicate_source_event_is_idempotent():
    predictor = FakePredictor()
    repository = FakeRepository()
    service = PredictionService(predictor, repository)
    message = valid_message()

    service.process_message(message)
    second = service.process_message(message)

    assert second.inserted is False
    assert len(repository.rows) == 1
    assert predictor.calls == 1


def test_empty_frame_is_rejected():
    service = PredictionService(FakePredictor(), FakeRepository())
    message = valid_message()
    message["payload"] = []

    with pytest.raises(ValueError, match="payload must not be empty"):
        service.process_message(message)


def test_repository_uses_idempotent_insert_and_commits():
    class Cursor:
        def __init__(self):
            self.sql = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.sql = " ".join(sql.split())

        def fetchone(self):
            return (123,)

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()
            self.commits = 0

        def cursor(self):
            return self.cursor_value

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("rollback not expected")

    conn = Connection()
    repository = PredictionRepository(conn)
    prediction = Prediction(
        900,
        datetime(2026, 1, 1, 0, 15),
        1.0,
    )

    inserted = repository.insert_prediction(
        "event-1",
        1,
        datetime(2026, 1, 1),
        "test-v1",
        42,
        prediction,
    )

    assert inserted is True
    assert "ON CONFLICT (source_event_id, model_version) DO NOTHING" in conn.cursor_value.sql
    assert conn.commits == 1
