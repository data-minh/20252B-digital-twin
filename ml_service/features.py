from collections import OrderedDict, deque
from datetime import timedelta
from math import cos, pi, sin

from ml_service.slots import CAPACITY, SLOT_IDS, empty_slot_state


WINDOWS = (
    (60, "1m"),
    (300, "5m"),
    (900, "15m"),
    (1800, "30m"),
    (3600, "60m"),
)
BASE_FEATURE_NAMES = (
    "occupied_count",
    "free_count",
    "occupancy_ratio",
)
WINDOW_FEATURE_NAMES = tuple(
    f"{metric}_{label}"
    for _, label in WINDOWS
    for metric in ("arrivals", "departures", "occupancy_slope")
)
CALENDAR_FEATURE_NAMES = (
    "seconds_since_last_arrival",
    "seconds_since_last_departure",
    "second_of_day_sin",
    "second_of_day_cos",
    "day_of_week_sin",
    "day_of_week_cos",
)
SLOT_FEATURE_NAMES = tuple(f"slot_{slot_id}" for slot_id in SLOT_IDS)
FEATURE_NAMES = (
    BASE_FEATURE_NAMES
    + WINDOW_FEATURE_NAMES
    + CALENDAR_FEATURE_NAMES
    + SLOT_FEATURE_NAMES
)


class RollingFeatureBuilder:
    def __init__(self):
        self.history = deque()
        self.previous_state = empty_slot_state()
        self.last_arrival_at = None
        self.last_departure_at = None
        self.initialized = False

    def update(self, observed_at, state):
        if self.history and observed_at < self.history[-1][0]:
            raise ValueError("feature updates must be chronological")
        normalized = {
            slot_id: int(state.get(slot_id, 0))
            for slot_id in SLOT_IDS
        }
        if any(value not in (0, 1) for value in normalized.values()):
            raise ValueError("slot states must be binary")

        if self.initialized:
            arrivals = sum(
                self.previous_state[slot_id] == 0
                and normalized[slot_id] == 1
                for slot_id in SLOT_IDS
            )
            departures = sum(
                self.previous_state[slot_id] == 1
                and normalized[slot_id] == 0
                for slot_id in SLOT_IDS
            )
        else:
            arrivals = 0
            departures = 0
            self.initialized = True

        if arrivals:
            self.last_arrival_at = observed_at
        if departures:
            self.last_departure_at = observed_at

        self.history.append(
            (
                observed_at,
                sum(normalized.values()),
                arrivals,
                departures,
                normalized,
            )
        )
        cutoff = observed_at - timedelta(
            seconds=max(seconds for seconds, _ in WINDOWS)
        )
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        self.previous_state = normalized

    def vector(self, observed_at):
        if not self.history:
            raise RuntimeError("at least one state update is required")
        if observed_at != self.history[-1][0]:
            raise ValueError("features are available for the latest update only")

        current_state = self.history[-1][4]
        occupied_count = sum(current_state.values())
        values = OrderedDict(
            (
                ("occupied_count", occupied_count),
                ("free_count", CAPACITY - occupied_count),
                ("occupancy_ratio", occupied_count / CAPACITY),
            )
        )

        for window_seconds, label in WINDOWS:
            cutoff = observed_at - timedelta(seconds=window_seconds)
            rows = [row for row in self.history if row[0] >= cutoff]
            arrivals = sum(row[2] for row in rows)
            departures = sum(row[3] for row in rows)
            elapsed = max(
                (observed_at - rows[0][0]).total_seconds(),
                1,
            )
            slope_per_minute = (
                (occupied_count - rows[0][1]) / elapsed * 60
            )
            values[f"arrivals_{label}"] = arrivals
            values[f"departures_{label}"] = departures
            values[f"occupancy_slope_{label}"] = slope_per_minute

        values["seconds_since_last_arrival"] = self._seconds_since(
            observed_at,
            self.last_arrival_at,
        )
        values["seconds_since_last_departure"] = self._seconds_since(
            observed_at,
            self.last_departure_at,
        )
        second_of_day = (
            observed_at.hour * 3600
            + observed_at.minute * 60
            + observed_at.second
        )
        values["second_of_day_sin"] = sin(
            2 * pi * second_of_day / 86400
        )
        values["second_of_day_cos"] = cos(
            2 * pi * second_of_day / 86400
        )
        values["day_of_week_sin"] = sin(
            2 * pi * observed_at.weekday() / 7
        )
        values["day_of_week_cos"] = cos(
            2 * pi * observed_at.weekday() / 7
        )
        for slot_id in SLOT_IDS:
            values[f"slot_{slot_id}"] = current_state[slot_id]

        if tuple(values) != FEATURE_NAMES:
            raise RuntimeError("feature ordering does not match schema")
        return values

    @staticmethod
    def _seconds_since(observed_at, event_at):
        if event_at is None:
            return 3600
        return min(
            max(int((observed_at - event_at).total_seconds()), 0),
            3600,
        )


def snapshot_state(snapshot):
    return dict(zip(SLOT_IDS, snapshot.slot_states))


def build_feature_rows(snapshots):
    builder = RollingFeatureBuilder()
    rows = []
    for snapshot in sorted(
        snapshots,
        key=lambda item: item.observed_at,
    ):
        state = snapshot_state(snapshot)
        builder.update(snapshot.observed_at, state)
        rows.append(
            {
                "observed_at": snapshot.observed_at,
                "features": builder.vector(snapshot.observed_at),
                "slot_states": state,
            }
        )
    return rows


def assign_temporal_split(index, total):
    if total <= 0 or not 0 <= index < total:
        raise ValueError("index must identify a row in a non-empty dataset")
    if index < int(total * 0.70):
        return "train"
    if index < int(total * 0.85):
        return "validation"
    return "test"
