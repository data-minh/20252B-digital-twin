import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from ml_service.schema import ensure_ml_schema
from ml_service.simulator import (
    SIMULATOR_VERSION,
    SimulationConfig,
    simulate,
)


def simulated_unique_id(run_id, slot_id, observed_at):
    value = (
        f"{run_id}:{slot_id}:"
        f"{observed_at.isoformat(timespec='microseconds')}"
    )
    return sha256(value.encode("utf-8")).hexdigest()


def transitions_to_scd2(run_id, transitions):
    grouped = defaultdict(list)
    for transition in sorted(
        transitions,
        key=lambda item: (item.slot_id, item.observed_at),
    ):
        grouped[transition.slot_id].append(transition)

    rows = []
    for slot_id, slot_transitions in grouped.items():
        for index, transition in enumerate(slot_transitions):
            next_transition = (
                slot_transitions[index + 1]
                if index + 1 < len(slot_transitions)
                else None
            )
            rows.append(
                {
                    "unique_id": simulated_unique_id(
                        run_id,
                        slot_id,
                        transition.observed_at,
                    ),
                    "simulation_run_id": run_id,
                    "slot_id": slot_id,
                    "occupied": transition.occupied,
                    "startdate": transition.observed_at,
                    "enddate": (
                        next_transition.observed_at
                        if next_transition
                        else None
                    ),
                    "status": "inactive" if next_transition else "active",
                }
            )
    return rows


def _json_parameters(config):
    values = asdict(config)
    values["start"] = config.start.isoformat()
    return json.dumps(values, sort_keys=True)


def persist_simulation(conn, config, result, run_id):
    run_id = UUID(str(run_id))
    rows = transitions_to_scd2(run_id, result.transitions)
    try:
        ensure_ml_schema(conn, commit=False)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO parking_simulation_runs
                    (simulation_run_id, seed, simulator_version,
                     started_at, ended_at, parameters,
                     transition_count, sample_count, excluded_count)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (simulation_run_id) DO NOTHING
                """,
                (
                    run_id,
                    config.seed,
                    SIMULATOR_VERSION,
                    config.start,
                    config.start + timedelta(days=config.days),
                    _json_parameters(config),
                    len(rows),
                    len(result.samples),
                    result.excluded_count,
                ),
            )
            cursor.executemany(
                """
                INSERT INTO parking_simulated_slot_history
                    (unique_id, simulation_run_id, slot_id, occupied,
                     startdate, enddate, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (unique_id) DO NOTHING
                """,
                [
                    (
                        row["unique_id"],
                        row["simulation_run_id"],
                        row["slot_id"],
                        row["occupied"],
                        row["startdate"],
                        row["enddate"],
                        row["status"],
                    )
                    for row in rows
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return run_id


def connect_database():
    try:
        import psycopg2
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 and python-dotenv are required for database commands"
        ) from exc

    load_dotenv(dotenv_path=os.environ.get("DOTENV_PATH", ".env"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(database_url)


def main():
    parser = argparse.ArgumentParser(
        description="Generate and persist synthetic parking history."
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--start", default="2026-01-01T00:00:00")
    args = parser.parse_args()

    run_id = UUID(args.run_id) if args.run_id else uuid4()
    config = SimulationConfig(
        days=args.days,
        seed=args.seed,
        start=datetime.fromisoformat(args.start),
    )
    result = simulate(config)
    conn = connect_database()
    try:
        persist_simulation(conn, config, result, run_id)
    finally:
        conn.close()
    print(
        f"simulation_run_id={run_id} "
        f"transitions={len(result.transitions)} "
        f"samples={len(result.samples)} "
        f"excluded={result.excluded_count}"
    )


if __name__ == "__main__":
    main()
