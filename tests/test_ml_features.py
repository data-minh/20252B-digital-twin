from datetime import datetime, timedelta

from ml_service.features import (
    FEATURE_NAMES,
    RollingFeatureBuilder,
    assign_temporal_split,
    build_feature_rows,
)
from ml_service.simulator import Snapshot
from ml_service.slots import SLOT_IDS, empty_slot_state


T0 = datetime(2026, 1, 1, 8, 0, 0)


def state_with(**values):
    state = empty_slot_state()
    state.update(values)
    return state


def snapshot(at, state):
    return Snapshot(at, tuple(state[slot_id] for slot_id in SLOT_IDS))


def test_feature_builder_counts_arrivals_without_future_leakage():
    builder = RollingFeatureBuilder()
    builder.update(T0, state_with())
    before = dict(builder.vector(T0))

    builder.update(T0 + timedelta(seconds=10), state_with(A01=1))

    assert before["occupied_count"] == 0
    assert before["arrivals_1m"] == 0
    assert builder.vector(T0 + timedelta(seconds=10))["arrivals_1m"] == 1


def test_offline_and_realtime_feature_vectors_match():
    snapshots = [
        snapshot(T0, state_with()),
        snapshot(T0 + timedelta(seconds=10), state_with(A01=1)),
        snapshot(T0 + timedelta(seconds=20), state_with(A01=1, A02=1)),
    ]

    offline = build_feature_rows(snapshots)[-1]["features"]
    realtime = RollingFeatureBuilder()
    for item in snapshots:
        realtime.update(
            item.observed_at,
            dict(zip(SLOT_IDS, item.slot_states)),
        )
    online = realtime.vector(snapshots[-1].observed_at)

    assert offline == online
    assert tuple(offline) == FEATURE_NAMES
    assert offline["occupied_count"] == 2
    assert offline["free_count"] == 58
    assert offline["slot_A01"] == 1
    assert offline["slot_E11"] == 0


def test_temporal_split_is_ordered_70_15_15():
    splits = [assign_temporal_split(index, 100) for index in range(100)]

    assert splits[:70] == ["train"] * 70
    assert splits[70:85] == ["validation"] * 15
    assert splits[85:] == ["test"] * 15
