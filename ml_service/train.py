import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from ml_service.features import FEATURE_NAMES
from ml_service.simulation_repository import connect_database


@dataclass(frozen=True)
class TrainingResult:
    metrics: dict
    metadata: dict


def _matrix(rows):
    return np.asarray(
        [
            [float(row["features"][name]) for name in FEATURE_NAMES]
            for row in rows
        ],
        dtype=np.float64,
    )


def _targets(rows):
    return np.asarray(
        [float(row["seconds_to_full"]) for row in rows],
        dtype=np.float64,
    )


def _baseline_predictions(rows):
    predictions = []
    for row in rows:
        features = row["features"]
        free_slots = float(features["free_count"])
        net_arrivals = (
            float(features["arrivals_5m"])
            - float(features["departures_5m"])
        )
        if net_arrivals <= 0:
            eta = 10800
        else:
            eta = free_slots / (net_arrivals / 300)
        predictions.append(min(10800, max(0, eta)))
    return np.asarray(predictions, dtype=np.float64)


def _evaluation_metrics(actual, predicted, baseline):
    errors = np.abs(actual - predicted)
    return {
        "mae_seconds": float(mean_absolute_error(actual, predicted)),
        "median_absolute_error_seconds": float(np.median(errors)),
        "p90_absolute_error_seconds": float(np.percentile(errors, 90)),
        "within_5_minutes": float(np.mean(errors <= 300)),
        "within_15_minutes": float(np.mean(errors <= 900)),
        "within_30_minutes": float(np.mean(errors <= 1800)),
        "baseline_mae_seconds": float(
            mean_absolute_error(actual, baseline)
        ),
        "test_samples": int(len(actual)),
    }


def _write_bytes_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_model_atomic(path, model):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        delete=False,
        suffix=".joblib",
    ) as handle:
        temporary = Path(handle.name)
    try:
        joblib.dump(model, temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def train_model(
    rows,
    artifact_dir,
    model_version,
    training_run_id,
    seed=20260727,
    overwrite=False,
):
    artifact_dir = Path(artifact_dir)
    model_path = artifact_dir / "model.joblib"
    if model_path.exists() and not overwrite:
        raise FileExistsError(
            f"model artifact already exists: {model_path}"
        )

    split_rows = {
        split: [
            row for row in rows if row["dataset_split"] == split
        ]
        for split in ("train", "validation", "test")
    }
    if any(not split_rows[split] for split in split_rows):
        raise ValueError("train, validation, and test rows are required")

    model = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.05,
        max_iter=300,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=0.1,
        random_state=seed,
        early_stopping=False,
    )
    model.fit(
        _matrix(split_rows["train"]),
        _targets(split_rows["train"]),
    )

    validation_actual = _targets(split_rows["validation"])
    validation_predictions = np.clip(
        model.predict(_matrix(split_rows["validation"])),
        0,
        10800,
    )
    test_actual = _targets(split_rows["test"])
    test_predictions = np.clip(
        model.predict(_matrix(split_rows["test"])),
        0,
        10800,
    )
    metrics = _evaluation_metrics(
        test_actual,
        test_predictions,
        _baseline_predictions(split_rows["test"]),
    )
    metrics["validation_mae_seconds"] = float(
        mean_absolute_error(
            validation_actual,
            validation_predictions,
        )
    )
    if metrics["mae_seconds"] >= metrics["baseline_mae_seconds"]:
        raise RuntimeError("trained model did not beat baseline MAE")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    schema = {
        "schema_version": 1,
        "feature_names": list(FEATURE_NAMES),
    }
    schema_bytes = json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    _write_bytes_atomic(
        artifact_dir / "feature_schema.json",
        schema_bytes,
    )
    _write_model_atomic(model_path, model)
    model_hash = sha256(model_path.read_bytes()).hexdigest()
    schema_hash = sha256(schema_bytes).hexdigest()
    metadata = {
        "model_version": model_version,
        "algorithm": "HistGradientBoostingRegressor",
        "artifact_sha256": model_hash,
        "feature_schema_hash": schema_hash,
        "training_run_id": str(training_run_id),
        "metrics": metrics,
        "parameters": model.get_params(),
        "python_runtime": "3.12",
        "scikit_learn_version": sklearn.__version__,
    }
    _write_bytes_atomic(
        artifact_dir / "metadata.json",
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )
    return TrainingResult(metrics, metadata)


def load_training_rows(conn, run_id):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT features, seconds_to_full, dataset_split
            FROM parking_model_training_data
            WHERE simulation_run_id = %s
            ORDER BY observed_at
            """,
            (run_id,),
        )
        rows = []
        for features, seconds_to_full, dataset_split in cursor.fetchall():
            if isinstance(features, str):
                features = json.loads(features)
            rows.append(
                {
                    "features": features,
                    "seconds_to_full": seconds_to_full,
                    "dataset_split": dataset_split,
                }
            )
    return rows


def register_model(conn, result):
    metadata = result.metadata
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO parking_model_registry (
                model_version, algorithm, artifact_sha256,
                feature_schema_hash, training_run_id,
                metrics, parameters
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (model_version) DO NOTHING
            """,
            (
                metadata["model_version"],
                metadata["algorithm"],
                metadata["artifact_sha256"],
                metadata["feature_schema_hash"],
                UUID(metadata["training_run_id"]),
                json.dumps(metadata["metrics"]),
                json.dumps(metadata["parameters"]),
            ),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description="Train the parking-fill ETA model."
    )
    parser.add_argument("--simulation-run-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument(
        "--output",
        default="models/parking_fill_eta",
    )
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    run_id = UUID(args.simulation_run_id)
    conn = connect_database()
    try:
        rows = load_training_rows(conn, run_id)
        result = train_model(
            rows,
            args.output,
            args.model_version,
            run_id,
            seed=args.seed,
            overwrite=args.overwrite,
        )
        register_model(conn, result)
    finally:
        conn.close()
    print(
        f"model_version={args.model_version} "
        f"mae_seconds={result.metrics['mae_seconds']:.3f} "
        f"baseline_mae_seconds="
        f"{result.metrics['baseline_mae_seconds']:.3f}"
    )


if __name__ == "__main__":
    main()
