import json
from datetime import datetime, timedelta
from hashlib import sha256

import joblib
import pytest

from ml_service.features import FEATURE_NAMES
from ml_service.predictor import Predictor


class ConstantModel:
    def __init__(self, value):
        self.value = value

    def predict(self, rows):
        return [self.value for _ in rows]


def write_artifacts(path, prediction=20000):
    model_path = path / "model.joblib"
    joblib.dump(ConstantModel(prediction), model_path)
    schema_bytes = json.dumps(
        {
            "schema_version": 1,
            "feature_names": list(FEATURE_NAMES),
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    (path / "feature_schema.json").write_bytes(schema_bytes)
    metadata = {
        "model_version": "test-v1",
        "artifact_sha256": sha256(model_path.read_bytes()).hexdigest(),
        "feature_schema_hash": sha256(schema_bytes).hexdigest(),
    }
    (path / "metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )


def test_predictor_bounds_eta_and_calculates_fill_time(tmp_path):
    write_artifacts(tmp_path, prediction=20000)
    predictor = Predictor.load(tmp_path)
    observed_at = datetime(2026, 1, 1, 12, 0, 0)

    result = predictor.predict(
        observed_at,
        [0.0] * len(FEATURE_NAMES),
    )

    assert result.seconds_to_full == 10800
    assert result.predicted_fill_at == observed_at + timedelta(seconds=10800)
    assert result.inference_ms >= 0
    assert predictor.model_version == "test-v1"


def test_predictor_rejects_schema_hash_mismatch(tmp_path):
    write_artifacts(tmp_path)
    (tmp_path / "feature_schema.json").write_text(
        '{"feature_names":[]}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="feature schema hash"):
        Predictor.load(tmp_path)


def test_predictor_rejects_wrong_feature_count(tmp_path):
    write_artifacts(tmp_path, prediction=10)
    predictor = Predictor.load(tmp_path)

    with pytest.raises(ValueError, match="feature count"):
        predictor.predict(datetime(2026, 1, 1), [0.0])
