import json
from uuid import UUID

from ml_service.features import FEATURE_NAMES
from ml_service.train import (
    TrainingResult,
    load_training_rows,
    register_model,
    train_model,
)


RUN_ID = UUID("33333333-3333-3333-3333-333333333333")


def training_rows():
    rows = []
    total = 600
    for index in range(total):
        free = index % 50 + 1
        occupied = 60 - free
        features = {name: 0.0 for name in FEATURE_NAMES}
        features.update(
            {
                "occupied_count": occupied,
                "free_count": free,
                "occupancy_ratio": occupied / 60,
                "arrivals_5m": 10,
                "departures_5m": 5,
            }
        )
        split = (
            "train"
            if index < 420
            else "validation"
            if index < 510
            else "test"
        )
        rows.append(
            {
                "features": features,
                "seconds_to_full": free * 100,
                "dataset_split": split,
            }
        )
    return rows


def test_train_writes_artifacts_and_beats_baseline(tmp_path):
    result = train_model(
        training_rows(),
        tmp_path,
        model_version="test-v1",
        training_run_id=RUN_ID,
        seed=42,
        max_iter=80,
    )

    assert (tmp_path / "model.joblib").exists()
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "feature_schema.json").exists()
    assert result.metrics["mae_seconds"] < result.metrics["baseline_mae_seconds"]
    assert result.metadata["model_version"] == "test-v1"
    assert len(result.metadata["artifact_sha256"]) == 64
    assert result.metadata["parameters"]["max_iter"] == 80
    schema = json.loads((tmp_path / "feature_schema.json").read_text("utf-8"))
    assert tuple(schema["feature_names"]) == FEATURE_NAMES


def test_load_training_rows_applies_temporal_sample_stride():
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params

        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()

        def cursor(self):
            return self.cursor_value

    conn = Connection()

    assert load_training_rows(conn, RUN_ID, sample_stride=3) == []
    assert "MOD(sample_id, %s) = 0" in conn.cursor_value.sql
    assert conn.cursor_value.params == (str(RUN_ID), 3)


def test_register_model_updates_existing_version_metadata():
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.sql = " ".join(sql.split())

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()
            self.commits = 0

        def cursor(self):
            return self.cursor_value

        def commit(self):
            self.commits += 1

    result = TrainingResult(
        metrics={"mae_seconds": 10.0},
        metadata={
            "model_version": "v1",
            "algorithm": "HistGradientBoostingRegressor",
            "artifact_sha256": "a" * 64,
            "feature_schema_hash": "b" * 64,
            "training_run_id": str(RUN_ID),
            "metrics": {"mae_seconds": 10.0},
            "parameters": {"max_iter": 10},
        },
    )
    conn = Connection()

    register_model(conn, result)

    assert "ON CONFLICT (model_version) DO UPDATE SET" in conn.cursor_value.sql
    assert "artifact_sha256 = EXCLUDED.artifact_sha256" in conn.cursor_value.sql
    assert conn.commits == 1
