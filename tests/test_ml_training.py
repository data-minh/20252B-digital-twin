import json
from uuid import UUID

from ml_service.features import FEATURE_NAMES
from ml_service.train import train_model


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
    )

    assert (tmp_path / "model.joblib").exists()
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "feature_schema.json").exists()
    assert result.metrics["mae_seconds"] < result.metrics["baseline_mae_seconds"]
    assert result.metadata["model_version"] == "test-v1"
    assert len(result.metadata["artifact_sha256"]) == 64
    schema = json.loads((tmp_path / "feature_schema.json").read_text("utf-8"))
    assert tuple(schema["feature_names"]) == FEATURE_NAMES
