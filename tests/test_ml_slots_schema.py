from ml_service.schema import schema_statements
from ml_service.slots import CAPACITY, SLOT_IDS, empty_slot_state, normalize_slot_state


def test_catalog_has_exactly_60_unique_slots_and_e11():
    assert CAPACITY == 60
    assert len(SLOT_IDS) == len(set(SLOT_IDS)) == 60
    assert SLOT_IDS[0] == "A01"
    assert SLOT_IDS[-1] == "E11"


def test_empty_state_has_every_canonical_slot_marked_empty():
    state = empty_slot_state()

    assert tuple(state) == SLOT_IDS
    assert sum(state.values()) == 0


def test_normalize_keeps_previous_and_rejects_unknown():
    previous = empty_slot_state()
    previous["A01"] = 1

    state, unknown = normalize_slot_state(
        previous,
        [{"id": "A02", "occupied": 1}, {"id": "Z99", "occupied": 1}],
    )

    assert state["A01"] == 1
    assert state["A02"] == 1
    assert state["E11"] == 0
    assert unknown == ["Z99"]


def test_normalize_rejects_non_binary_occupancy():
    try:
        normalize_slot_state(empty_slot_state(), [{"id": "A01", "occupied": 2}])
    except ValueError as exc:
        assert "occupied must be 0 or 1" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_schema_defines_required_tables_and_prediction_constraint():
    sql = "\n".join(schema_statements())

    for table in (
        "parking_slots",
        "parking_simulation_runs",
        "parking_simulated_slot_history",
        "parking_model_training_data",
        "parking_model_registry",
        "parking_fill_predictions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "predicted_seconds_to_full BETWEEN 0 AND 10800" in sql
    assert "UNIQUE (source_event_id, model_version)" in sql
    assert "seconds_to_full BETWEEN 0 AND 10800" in sql
