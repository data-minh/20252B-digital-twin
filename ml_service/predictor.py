import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import joblib

from ml_service.features import FEATURE_NAMES


@dataclass(frozen=True)
class Prediction:
    seconds_to_full: int
    predicted_fill_at: datetime
    inference_ms: float


class Predictor:
    def __init__(self, model, model_version):
        self.model = model
        self.model_version = model_version

    @classmethod
    def load(cls, artifact_dir):
        artifact_dir = Path(artifact_dir)
        model_path = artifact_dir / "model.joblib"
        metadata_path = artifact_dir / "metadata.json"
        schema_path = artifact_dir / "feature_schema.json"
        for path in (model_path, metadata_path, schema_path):
            if not path.is_file():
                raise RuntimeError(f"model artifact is missing: {path}")

        metadata = json.loads(metadata_path.read_text("utf-8"))
        schema_bytes = schema_path.read_bytes()
        schema = json.loads(schema_bytes)
        schema_hash = sha256(schema_bytes).hexdigest()
        model_hash = sha256(model_path.read_bytes()).hexdigest()
        if schema_hash != metadata.get("feature_schema_hash"):
            raise RuntimeError("feature schema hash mismatch")
        if model_hash != metadata.get("artifact_sha256"):
            raise RuntimeError("model artifact hash mismatch")
        if tuple(schema.get("feature_names", ())) != FEATURE_NAMES:
            raise RuntimeError(
                "embedded feature names do not match runtime"
            )
        return cls(
            joblib.load(model_path),
            str(metadata["model_version"]),
        )

    def predict(self, observed_at, ordered_features):
        if len(ordered_features) != len(FEATURE_NAMES):
            raise ValueError(
                f"feature count must be {len(FEATURE_NAMES)}"
            )
        started = perf_counter()
        raw = float(self.model.predict([ordered_features])[0])
        seconds = min(10800, max(0, int(round(raw))))
        return Prediction(
            seconds_to_full=seconds,
            predicted_fill_at=observed_at + timedelta(seconds=seconds),
            inference_ms=(perf_counter() - started) * 1000,
        )
