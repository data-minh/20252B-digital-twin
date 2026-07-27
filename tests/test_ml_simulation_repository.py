from datetime import datetime, timedelta
from uuid import UUID

import pytest

from ml_service.simulation_repository import (
    persist_simulation,
    simulated_unique_id,
    transitions_to_scd2,
)
from ml_service.simulator import (
    SimulationConfig,
    SimulationResult,
    Transition,
)


RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
START = datetime(2026, 1, 1)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append(("execute", " ".join(sql.split()), params))

    def executemany(self, sql, params):
        self.conn.calls.append(
            ("executemany", " ".join(sql.split()), list(params))
        )


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def small_result():
    transitions = (
        Transition(START, "A01", 0),
        Transition(START, "A02", 1),
        Transition(START + timedelta(seconds=30), "A01", 1),
    )
    return SimulationResult(transitions, (), ())


def test_simulated_identity_is_stable_and_timestamp_specific():
    first = simulated_unique_id(RUN_ID, "A01", START)

    assert first == simulated_unique_id(RUN_ID, "A01", START)
    assert first != simulated_unique_id(
        RUN_ID,
        "A01",
        START + timedelta(seconds=1),
    )


def test_transitions_are_converted_to_scd2_rows():
    rows = transitions_to_scd2(RUN_ID, small_result().transitions)
    a01 = [row for row in rows if row["slot_id"] == "A01"]

    assert a01[0]["startdate"] == START
    assert a01[0]["enddate"] == START + timedelta(seconds=30)
    assert a01[0]["status"] == "inactive"
    assert a01[1]["enddate"] is None
    assert a01[1]["status"] == "active"


def test_persist_simulation_writes_run_transitions_and_commits_once():
    conn = FakeConnection()
    config = SimulationConfig(days=1, seed=42, start=START)

    persist_simulation(conn, config, small_result(), RUN_ID)

    sql = "\n".join(call[1] for call in conn.calls)
    assert "INSERT INTO parking_simulation_runs" in sql
    assert "INSERT INTO parking_simulated_slot_history" in sql
    assert conn.commit_count == 1
    assert conn.rollback_count == 0


def test_persist_simulation_rolls_back_on_database_error():
    class BrokenCursor(FakeCursor):
        def executemany(self, sql, params):
            raise RuntimeError("database write failed")

    conn = FakeConnection()
    conn.cursor = lambda: BrokenCursor(conn)

    with pytest.raises(RuntimeError, match="database write failed"):
        persist_simulation(
            conn,
            SimulationConfig(days=1, start=START),
            small_result(),
            RUN_ID,
        )

    assert conn.commit_count == 0
    assert conn.rollback_count == 1
