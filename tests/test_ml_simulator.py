from datetime import datetime

import pytest

from ml_service.simulator import SimulationConfig, simulate
from ml_service.slots import SLOT_IDS


def test_simulator_is_reproducible_and_reaches_full_within_three_hours():
    config = SimulationConfig(days=1, seed=42, start=datetime(2026, 1, 1))

    first = simulate(config)
    second = simulate(config)

    assert first == second
    assert any(snapshot.occupied_count == 60 for snapshot in first.snapshots)
    assert first.samples
    assert max(sample.seconds_to_full for sample in first.samples) <= 10800
    assert min(sample.seconds_to_full for sample in first.samples) >= 0


def test_simulator_records_only_valid_slot_transitions():
    result = simulate(
        SimulationConfig(days=1, seed=7, start=datetime(2026, 2, 1))
    )

    assert result.transitions
    assert all(item.slot_id in SLOT_IDS for item in result.transitions)
    assert all(item.occupied in (0, 1) for item in result.transitions)
    assert list(result.transitions) == sorted(
        result.transitions,
        key=lambda item: (item.observed_at, item.slot_id),
    )


def test_simulator_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="days must be positive"):
        simulate(SimulationConfig(days=0))

    with pytest.raises(ValueError, match="fill horizon"):
        simulate(
            SimulationConfig(
                days=1,
                min_fill_seconds=100,
                max_fill_seconds=10900,
            )
        )
