from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random

from ml_service.slots import CAPACITY, SLOT_IDS


MAX_ETA_SECONDS = 10800
SIMULATOR_VERSION = "1.0"


@dataclass(frozen=True)
class SimulationConfig:
    days: int = 30
    seed: int = 20260727
    start: datetime = datetime(2026, 1, 1)
    sample_interval_seconds: int = 10
    min_fill_seconds: int = 900
    max_fill_seconds: int = MAX_ETA_SECONDS


@dataclass(frozen=True)
class Transition:
    observed_at: datetime
    slot_id: str
    occupied: int


@dataclass(frozen=True)
class Snapshot:
    observed_at: datetime
    slot_states: tuple[int, ...]

    @property
    def occupied_count(self):
        return sum(self.slot_states)


@dataclass(frozen=True)
class LabeledSnapshot:
    snapshot: Snapshot
    next_full_at: datetime
    seconds_to_full: int


@dataclass(frozen=True)
class SimulationResult:
    transitions: tuple[Transition, ...]
    snapshots: tuple[Snapshot, ...]
    samples: tuple[LabeledSnapshot, ...]

    @property
    def excluded_count(self):
        return len(self.snapshots) - len(self.samples)


def _validate_config(config):
    if config.days <= 0:
        raise ValueError("days must be positive")
    if config.sample_interval_seconds <= 0:
        raise ValueError("sample interval must be positive")
    if (
        config.min_fill_seconds <= 0
        or config.min_fill_seconds > config.max_fill_seconds
        or config.max_fill_seconds > MAX_ETA_SECONDS
    ):
        raise ValueError("fill horizon must be within 1..10800 seconds")


def _initial_state(rng):
    occupied_count = rng.randint(20, 45)
    occupied_slots = set(rng.sample(SLOT_IDS, occupied_count))
    return {
        slot_id: int(slot_id in occupied_slots)
        for slot_id in SLOT_IDS
    }


def _record_state_change(transitions, observed_at, state, desired_state):
    for slot_id in SLOT_IDS:
        new_value = int(desired_state[slot_id])
        if state[slot_id] != new_value:
            state[slot_id] = new_value
            transitions.append(Transition(observed_at, slot_id, new_value))


def _fill_seconds_for_time(config, observed_at, rng):
    base = rng.randint(config.min_fill_seconds, config.max_fill_seconds)
    if 7 <= observed_at.hour < 10 or 16 <= observed_at.hour < 20:
        factor = 0.72
    elif 0 <= observed_at.hour < 6:
        factor = 1.15
    else:
        factor = 0.95
    weekday_factor = 1.0 if observed_at.weekday() < 5 else 1.08
    return max(
        config.min_fill_seconds,
        min(config.max_fill_seconds, int(base * factor * weekday_factor)),
    )


def _label_snapshots(snapshots, full_events):
    samples = []
    event_index = 0
    for snapshot in snapshots:
        if snapshot.occupied_count == CAPACITY:
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
        seconds_to_full = int(
            (next_full_at - snapshot.observed_at).total_seconds()
        )
        if 0 <= seconds_to_full <= MAX_ETA_SECONDS:
            samples.append(
                LabeledSnapshot(snapshot, next_full_at, seconds_to_full)
            )
    return tuple(samples)


def simulate(config=SimulationConfig()):
    _validate_config(config)
    rng = Random(config.seed)
    transitions = []
    snapshots = []
    full_events = []
    state = _initial_state(rng)
    for slot_id in SLOT_IDS:
        transitions.append(
            Transition(config.start, slot_id, state[slot_id])
        )

    cycle_start = config.start
    cycle_start_occupied = sum(state.values())
    fill_seconds = _fill_seconds_for_time(config, cycle_start, rng)
    fill_at = cycle_start + timedelta(seconds=fill_seconds)
    full_hold_until = None
    total_seconds = config.days * 86400

    for elapsed_seconds in range(total_seconds):
        observed_at = config.start + timedelta(seconds=elapsed_seconds)

        if full_hold_until is not None:
            if observed_at >= full_hold_until:
                desired_state = _initial_state(rng)
                _record_state_change(
                    transitions,
                    observed_at,
                    state,
                    desired_state,
                )
                cycle_start = observed_at
                cycle_start_occupied = sum(state.values())
                fill_seconds = _fill_seconds_for_time(
                    config,
                    cycle_start,
                    rng,
                )
                fill_at = cycle_start + timedelta(seconds=fill_seconds)
                full_hold_until = None
        elif observed_at >= fill_at:
            desired_state = {slot_id: 1 for slot_id in SLOT_IDS}
            _record_state_change(
                transitions,
                observed_at,
                state,
                desired_state,
            )
            full_events.append(observed_at)
            full_hold_until = observed_at + timedelta(
                seconds=rng.randint(60, 300)
            )
        else:
            elapsed_in_cycle = (observed_at - cycle_start).total_seconds()
            progress = min(elapsed_in_cycle / fill_seconds, 1.0)
            target = round(
                cycle_start_occupied
                + (CAPACITY - cycle_start_occupied) * progress**1.15
            )
            occupied_count = sum(state.values())
            if occupied_count < target:
                free_slots = [
                    slot_id for slot_id in SLOT_IDS if state[slot_id] == 0
                ]
                if free_slots:
                    slot_id = rng.choice(free_slots)
                    state[slot_id] = 1
                    transitions.append(
                        Transition(observed_at, slot_id, 1)
                    )
            elif (
                occupied_count > 20
                and progress < 0.9
                and rng.random() < 0.0015
            ):
                occupied_slots = [
                    slot_id for slot_id in SLOT_IDS if state[slot_id] == 1
                ]
                slot_id = rng.choice(occupied_slots)
                state[slot_id] = 0
                transitions.append(
                    Transition(observed_at, slot_id, 0)
                )

        if elapsed_seconds % config.sample_interval_seconds == 0:
            snapshots.append(
                Snapshot(
                    observed_at,
                    tuple(state[slot_id] for slot_id in SLOT_IDS),
                )
            )

    transitions.sort(key=lambda item: (item.observed_at, item.slot_id))
    samples = _label_snapshots(snapshots, full_events)
    return SimulationResult(
        tuple(transitions),
        tuple(snapshots),
        samples,
    )
