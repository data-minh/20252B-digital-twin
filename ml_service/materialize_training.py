import argparse
import json
from datetime import timedelta
from uuid import UUID

from ml_service.features import (
    FEATURE_NAMES,
    SLOT_FEATURE_NAMES,
    assign_temporal_split,
    build_feature_rows,
)
from ml_service.schema import ensure_ml_schema
from ml_service.simulation_repository import connect_database
from ml_service.simulator import (
    LabeledSnapshot,
    MAX_ETA_SECONDS,
    Snapshot,
    Transition,
)
from ml_service.slots import SLOT_IDS, empty_slot_state


SCALAR_FEATURE_COLUMNS = FEATURE_NAMES[: -len(SLOT_FEATURE_NAMES)]


def _label_snapshots(snapshots, full_events):
    samples = []
    event_index = 0
    for snapshot in snapshots:
        if snapshot.occupied_count == len(SLOT_IDS):
            samples.append(
                LabeledSnapshot(snapshot, snapshot.observed_at, 0)
            )
            continue
        while (
            event_index < len(full_events)
            and full_events[event_index] < snapshot.observed_at
        ):
            event_index += 1
        if event_index >= len(full_events):
            continue
        next_full_at = full_events[event_index]
        seconds = int(
            (next_full_at - snapshot.observed_at).total_seconds()
        )
        if 0 <= seconds <= MAX_ETA_SECONDS:
            samples.append(
                LabeledSnapshot(snapshot, next_full_at, seconds)
            )
    return tuple(samples)


def reconstruct_timeline(
    started_at,
    ended_at,
    sample_interval_seconds,
    transitions,
):
    if ended_at <= started_at:
        raise ValueError("ended_at must be after started_at")
    if sample_interval_seconds <= 0:
        raise ValueError("sample interval must be positive")

    ordered = sorted(
        transitions,
        key=lambda item: (item.observed_at, item.slot_id),
    )
    state = empty_slot_state()
    transition_index = 0
    full_events = []
    snapshots = []
    observed_at = started_at

    while observed_at < ended_at:
        while (
            transition_index < len(ordered)
            and ordered[transition_index].observed_at <= observed_at
        ):
            transition = ordered[transition_index]
            was_full = sum(state.values()) == len(SLOT_IDS)
            state[transition.slot_id] = int(transition.occupied)
            is_full = sum(state.values()) == len(SLOT_IDS)
            if is_full and not was_full:
                full_events.append(transition.observed_at)
            transition_index += 1
        snapshots.append(
            Snapshot(
                observed_at,
                tuple(state[slot_id] for slot_id in SLOT_IDS),
            )
        )
        observed_at += timedelta(seconds=sample_interval_seconds)

    return tuple(snapshots), _label_snapshots(snapshots, full_events)


def build_training_rows(snapshots, samples):
    label_by_time = {
        sample.snapshot.observed_at: sample
        for sample in samples
    }
    feature_rows = build_feature_rows(snapshots)
    retained = [
        row
        for row in feature_rows
        if row["observed_at"] in label_by_time
    ]
    result = []
    total = len(retained)
    for index, row in enumerate(retained):
        label = label_by_time[row["observed_at"]]
        result.append(
            {
                **row,
                "next_full_at": label.next_full_at,
                "seconds_to_full": label.seconds_to_full,
                "dataset_split": assign_temporal_split(index, total),
            }
        )
    return result


def _training_params(run_id, row):
    features = row["features"]
    return (
        run_id,
        row["observed_at"],
        *(features[name] for name in SCALAR_FEATURE_COLUMNS),
        json.dumps(dict(features), separators=(",", ":")),
        json.dumps(row["slot_states"], separators=(",", ":")),
        row["next_full_at"],
        row["seconds_to_full"],
        row["dataset_split"],
    )


TRAINING_INSERT_SQL = """
    INSERT INTO parking_model_training_data (
        simulation_run_id, observed_at,
        occupied_count, free_count, occupancy_ratio,
        arrivals_1m, departures_1m, occupancy_slope_1m,
        arrivals_5m, departures_5m, occupancy_slope_5m,
        arrivals_15m, departures_15m, occupancy_slope_15m,
        arrivals_30m, departures_30m, occupancy_slope_30m,
        arrivals_60m, departures_60m, occupancy_slope_60m,
        seconds_since_last_arrival, seconds_since_last_departure,
        second_of_day_sin, second_of_day_cos,
        day_of_week_sin, day_of_week_cos,
        features, slot_states, next_full_at,
        seconds_to_full, dataset_split
    )
    VALUES (
        %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s,
        %s, %s, %s, %s,
        %s::jsonb, %s::jsonb, %s, %s, %s
    )
    ON CONFLICT (simulation_run_id, observed_at) DO UPDATE SET
        occupied_count = EXCLUDED.occupied_count,
        free_count = EXCLUDED.free_count,
        occupancy_ratio = EXCLUDED.occupancy_ratio,
        features = EXCLUDED.features,
        slot_states = EXCLUDED.slot_states,
        next_full_at = EXCLUDED.next_full_at,
        seconds_to_full = EXCLUDED.seconds_to_full,
        dataset_split = EXCLUDED.dataset_split
"""


def materialize_training_rows(
    conn,
    run_id,
    rows,
    batch_size=5000,
):
    try:
        with conn.cursor() as cursor:
            for start in range(0, len(rows), batch_size):
                batch = rows[start : start + batch_size]
                cursor.executemany(
                    TRAINING_INSERT_SQL,
                    [_training_params(run_id, row) for row in batch],
                )
            cursor.execute(
                """
                UPDATE parking_simulation_runs
                SET sample_count = %s
                WHERE simulation_run_id = %s
                """,
                (len(rows), run_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def load_timeline(conn, run_id):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT started_at, ended_at, parameters
            FROM parking_simulation_runs
            WHERE simulation_run_id = %s
            """,
            (run_id,),
        )
        run = cursor.fetchone()
        if run is None:
            raise RuntimeError(f"simulation run not found: {run_id}")
        started_at, ended_at, parameters = run
        if isinstance(parameters, str):
            parameters = json.loads(parameters)
        cursor.execute(
            """
            SELECT startdate, slot_id, occupied
            FROM parking_simulated_slot_history
            WHERE simulation_run_id = %s
            ORDER BY startdate, slot_id
            """,
            (run_id,),
        )
        transitions = tuple(
            Transition(observed_at, slot_id, occupied)
            for observed_at, slot_id, occupied in cursor.fetchall()
        )
    return reconstruct_timeline(
        started_at,
        ended_at,
        int(parameters["sample_interval_seconds"]),
        transitions,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Materialize model-ready parking ETA samples."
    )
    parser.add_argument("--simulation-run-id", required=True)
    args = parser.parse_args()
    run_id = UUID(args.simulation_run_id)

    conn = connect_database()
    try:
        ensure_ml_schema(conn)
        snapshots, samples = load_timeline(conn, run_id)
        rows = build_training_rows(snapshots, samples)
        count = materialize_training_rows(conn, run_id, rows)
    finally:
        conn.close()
    print(f"simulation_run_id={run_id} training_samples={count}")


if __name__ == "__main__":
    main()
