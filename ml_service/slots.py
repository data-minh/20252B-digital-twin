ROW_WIDTHS = {"A": 14, "B": 13, "C": 12, "D": 10, "E": 11}
SLOT_IDS = tuple(
    f"{row}{column:02d}"
    for row, width in ROW_WIDTHS.items()
    for column in range(1, width + 1)
)
CAPACITY = len(SLOT_IDS)


def empty_slot_state():
    return {slot_id: 0 for slot_id in SLOT_IDS}


def normalize_slot_state(previous, records):
    state = empty_slot_state()
    state.update(
        {slot_id: int(value) for slot_id, value in previous.items() if slot_id in state}
    )
    unknown = []
    for record in records:
        slot_id = str(record["id"])
        if slot_id not in state:
            unknown.append(slot_id)
            continue
        occupied = int(record["occupied"])
        if occupied not in (0, 1):
            raise ValueError(f"occupied must be 0 or 1 for {slot_id}")
        state[slot_id] = occupied
    return state, unknown
