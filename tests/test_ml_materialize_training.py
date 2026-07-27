from datetime import datetime, timedelta
from uuid import UUID

from ml_service.materialize_training import (
    build_training_rows,
    materialize_training_rows,
    reconstruct_timeline,
)
from ml_service.simulator import Transition
from ml_service.slots import SLOT_IDS


START = datetime(2026, 1, 1)
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")


def timeline_transitions():
    items = [Transition(START, slot_id, 1) for slot_id in SLOT_IDS]
    items.extend(
        (
            Transition(START + timedelta(seconds=10), "E11", 0),
            Transition(START + timedelta(seconds=100), "E11", 1),
        )
    )
    return tuple(items)


def test_reconstruct_timeline_labels_next_full_event():
    snapshots, samples = reconstruct_timeline(
        START,
        START + timedelta(seconds=120),
        10,
        timeline_transitions(),
    )

    sample_by_time = {
        item.snapshot.observed_at: item
        for item in samples
    }
    at_20 = sample_by_time[START + timedelta(seconds=20)]
    assert len(snapshots) == 12
    assert at_20.seconds_to_full == 80
    assert at_20.next_full_at == START + timedelta(seconds=100)


def test_build_training_rows_assigns_temporal_splits():
    snapshots, samples = reconstruct_timeline(
        START,
        START + timedelta(seconds=120),
        10,
        timeline_transitions(),
    )

    rows = build_training_rows(snapshots, samples)

    assert rows
    assert {row["dataset_split"] for row in rows} == {
        "train",
        "validation",
        "test",
    }
    assert all(0 <= row["seconds_to_full"] <= 10800 for row in rows)
    assert rows[0]["features"]["slot_E11"] == 1


def test_materialize_training_rows_batches_and_commits():
    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def executemany(self, sql, params):
            self.calls.append((" ".join(sql.split()), list(params)))

        def execute(self, sql, params=None):
            self.calls.append((" ".join(sql.split()), params))

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()
            self.commits = 0

        def cursor(self):
            return self.cursor_value

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("rollback was not expected")

    snapshots, samples = reconstruct_timeline(
        START,
        START + timedelta(seconds=120),
        10,
        timeline_transitions(),
    )
    rows = build_training_rows(snapshots, samples)
    conn = Connection()

    count = materialize_training_rows(conn, RUN_ID, rows, batch_size=4)

    assert count == len(rows)
    assert len(conn.cursor_value.calls) >= 2
    assert "INSERT INTO parking_model_training_data" in conn.cursor_value.calls[0][0]
    assert conn.commits == 1
