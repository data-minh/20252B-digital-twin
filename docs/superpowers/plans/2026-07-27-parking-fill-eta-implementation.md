# Realtime Parking-Fill ETA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate reproducible 60-slot synthetic history, train a bounded three-hour parking-fill ETA model outside the inference service, embed that model in a Docker image, and persist one realtime prediction per MQTT frame.

**Architecture:** Offline commands write synthetic SCD2 history and materialized feature rows to PostgreSQL, then train a `HistGradientBoostingRegressor` into `models/parking_fill_eta/`. A new inference-only MQTT subscriber loads the embedded artifact, maintains a canonical 60-slot state, builds the same rolling features used in training, and inserts idempotent predictions into PostgreSQL.

**Tech Stack:** Python 3.12, PostgreSQL, psycopg2, paho-mqtt 2.1, NumPy, pandas, scikit-learn HistGradientBoosting, joblib, Docker Compose, pytest.

---

## File Structure

Create focused modules instead of putting simulation, training, and serving in one file:

```text
ml_service/
  __init__.py                 Package marker.
  slots.py                    Canonical 60-slot catalog and state normalization.
  schema.py                   Idempotent PostgreSQL DDL for ML tables.
  simulator.py                Pure deterministic synthetic transition generator.
  simulation_repository.py    Simulation-run and SCD2 database writes.
  features.py                 Shared offline/realtime rolling feature builder.
  materialize_training.py     Reconstruct snapshots and insert train rows.
  train.py                    Temporal split, model evaluation, artifact creation.
  predictor.py                Pure model-loading and bounded prediction logic.
  prediction_service.py       MQTT lifecycle and prediction database writes.
  requirements.txt            Pinned training/inference dependencies.
  Dockerfile                  Inference image with prebuilt model.
models/
  parking_fill_eta/
    .gitkeep                  Keeps artifact output folder present.
tests/
  test_ml_slots_schema.py
  test_ml_event_identity.py
  test_ml_simulator.py
  test_ml_simulation_repository.py
  test_ml_features.py
  test_ml_training.py
  test_ml_predictor.py
  test_ml_prediction_service.py
```

Modify:

```text
camera_device/camera_to_mqtt.py
postgres_sink/postgres_sink.py
test_stream_clean_to_json.py
docker-compose.yml
.dockerignore
.gitignore
README.md
```

## Task 1: Canonical Slots and ML Schema

**Files:**
- Create: `ml_service/__init__.py`
- Create: `ml_service/slots.py`
- Create: `ml_service/schema.py`
- Create: `tests/test_ml_slots_schema.py`

- [ ] **Step 1: Write failing catalog and schema tests**

```python
# tests/test_ml_slots_schema.py
from ml_service.schema import schema_statements
from ml_service.slots import CAPACITY, SLOT_IDS, normalize_slot_state


def test_catalog_has_exactly_60_unique_slots_and_e11():
    assert CAPACITY == 60
    assert len(SLOT_IDS) == len(set(SLOT_IDS)) == 60
    assert SLOT_IDS[-1] == "E11"


def test_normalize_keeps_previous_and_rejects_unknown():
    previous = {slot_id: 0 for slot_id in SLOT_IDS}
    previous["A01"] = 1
    state, unknown = normalize_slot_state(
        previous,
        [{"id": "A02", "occupied": 1}, {"id": "Z99", "occupied": 1}],
    )
    assert state["A01"] == 1
    assert state["A02"] == 1
    assert state["E11"] == 0
    assert unknown == ["Z99"]


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
```

- [ ] **Step 2: Run the tests and verify the package is missing**

Run:

```powershell
python -m pytest -q tests/test_ml_slots_schema.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'ml_service'`.

- [ ] **Step 3: Implement the canonical catalog and state normalization**

```python
# ml_service/slots.py
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
    state.update({key: int(value) for key, value in previous.items() if key in state})
    unknown = []
    for record in records:
        slot_id = str(record["id"])
        occupied = int(record["occupied"])
        if slot_id not in state:
            unknown.append(slot_id)
            continue
        if occupied not in (0, 1):
            raise ValueError(f"occupied must be 0 or 1 for {slot_id}")
        state[slot_id] = occupied
    return state, unknown
```

Create an empty `ml_service/__init__.py`.

- [ ] **Step 4: Implement idempotent DDL**

In `ml_service/schema.py`, implement:

```python
from ml_service.slots import ROW_WIDTHS


def schema_statements():
    return [
        """CREATE TABLE IF NOT EXISTS parking_slots (
            slot_id TEXT PRIMARY KEY,
            row_name TEXT NOT NULL,
            column_number INTEGER NOT NULL,
            capacity_member BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS parking_simulation_runs (
            simulation_run_id UUID PRIMARY KEY,
            seed INTEGER NOT NULL,
            simulator_version TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP NOT NULL,
            parameters JSONB NOT NULL,
            transition_count INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            excluded_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS parking_simulated_slot_history (
            unique_id TEXT PRIMARY KEY,
            simulation_run_id UUID NOT NULL REFERENCES parking_simulation_runs(simulation_run_id),
            slot_id TEXT NOT NULL REFERENCES parking_slots(slot_id),
            occupied INTEGER NOT NULL CHECK (occupied IN (0, 1)),
            startdate TIMESTAMP NOT NULL,
            enddate TIMESTAMP NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
        )""",
        """CREATE TABLE IF NOT EXISTS parking_model_training_data (
            sample_id BIGSERIAL PRIMARY KEY,
            simulation_run_id UUID NOT NULL REFERENCES parking_simulation_runs(simulation_run_id),
            observed_at TIMESTAMP NOT NULL,
            occupied_count INTEGER NOT NULL,
            free_count INTEGER NOT NULL,
            occupancy_ratio DOUBLE PRECISION NOT NULL,
            arrivals_1m INTEGER NOT NULL,
            arrivals_5m INTEGER NOT NULL,
            arrivals_15m INTEGER NOT NULL,
            arrivals_30m INTEGER NOT NULL,
            arrivals_60m INTEGER NOT NULL,
            departures_1m INTEGER NOT NULL,
            departures_5m INTEGER NOT NULL,
            departures_15m INTEGER NOT NULL,
            departures_30m INTEGER NOT NULL,
            departures_60m INTEGER NOT NULL,
            occupancy_slope_1m DOUBLE PRECISION NOT NULL,
            occupancy_slope_5m DOUBLE PRECISION NOT NULL,
            occupancy_slope_15m DOUBLE PRECISION NOT NULL,
            occupancy_slope_30m DOUBLE PRECISION NOT NULL,
            occupancy_slope_60m DOUBLE PRECISION NOT NULL,
            seconds_since_last_arrival INTEGER NOT NULL,
            seconds_since_last_departure INTEGER NOT NULL,
            second_of_day_sin DOUBLE PRECISION NOT NULL,
            second_of_day_cos DOUBLE PRECISION NOT NULL,
            day_of_week_sin DOUBLE PRECISION NOT NULL,
            day_of_week_cos DOUBLE PRECISION NOT NULL,
            features JSONB NOT NULL,
            slot_states JSONB NOT NULL,
            next_full_at TIMESTAMP NOT NULL,
            seconds_to_full INTEGER NOT NULL CHECK (seconds_to_full BETWEEN 0 AND 10800),
            dataset_split TEXT NOT NULL CHECK (dataset_split IN ('train', 'validation', 'test')),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (simulation_run_id, observed_at)
        )""",
        """CREATE TABLE IF NOT EXISTS parking_model_registry (
            model_version TEXT PRIMARY KEY,
            algorithm TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            feature_schema_hash TEXT NOT NULL,
            training_run_id UUID NOT NULL REFERENCES parking_simulation_runs(simulation_run_id),
            metrics JSONB NOT NULL,
            parameters JSONB NOT NULL,
            trained_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS parking_fill_predictions (
            prediction_id BIGSERIAL PRIMARY KEY,
            source_event_id TEXT NOT NULL,
            source_frame_id INTEGER NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            model_version TEXT NOT NULL,
            occupied_count INTEGER NOT NULL CHECK (occupied_count BETWEEN 0 AND 60),
            capacity INTEGER NOT NULL DEFAULT 60 CHECK (capacity = 60),
            predicted_seconds_to_full INTEGER NOT NULL
                CHECK (predicted_seconds_to_full BETWEEN 0 AND 10800),
            predicted_fill_at TIMESTAMP NOT NULL,
            inference_ms DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_event_id, model_version)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_sim_history_run_slot_time
           ON parking_simulated_slot_history (simulation_run_id, slot_id, startdate)""",
    ]


def ensure_ml_schema(conn):
    with conn.cursor() as cursor:
        for statement in schema_statements():
            cursor.execute(statement)
        for row_name, width in ROW_WIDTHS.items():
            for column in range(1, width + 1):
                cursor.execute(
                    """INSERT INTO parking_slots (slot_id, row_name, column_number)
                       VALUES (%s, %s, %s) ON CONFLICT (slot_id) DO NOTHING""",
                    (f"{row_name}{column:02d}", row_name, column),
                )
    conn.commit()
```

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_slots_schema.py
```

Expected: all tests pass.

Commit:

```powershell
git add ml_service tests/test_ml_slots_schema.py
git commit -m "feat: add canonical parking slots and ML schema"
```

## Task 2: Unique MQTT Event Identity

**Files:**
- Modify: `camera_device/camera_to_mqtt.py`
- Modify: `postgres_sink/postgres_sink.py`
- Modify: `test_stream_clean_to_json.py`
- Create: `tests/test_ml_event_identity.py`

- [ ] **Step 1: Write failing event identity tests**

```python
# tests/test_ml_event_identity.py
from camera_device.camera_to_mqtt import source_event_id
from postgres_sink.postgres_sink import record_unique_id


def test_source_event_id_changes_between_cycles():
    assert source_event_id("session-a", 1, "0001") == "session-a:1:0001"
    assert source_event_id("session-a", 2, "0001") == "session-a:2:0001"


def test_history_identity_uses_source_event_not_reused_frame_id():
    first = record_unique_id("session-a:1:0001", "A01")
    second = record_unique_id("session-a:2:0001", "A01")
    assert first != second
```

Update the existing camera-message test to expect `source_event_id`.

- [ ] **Step 2: Run and verify the missing signatures**

Run:

```powershell
python -m pytest -q tests/test_ml_event_identity.py test_stream_clean_to_json.py::test_camera_mqtt_message_wraps_frame_payload
```

Expected: fail because `source_event_id` does not exist and `record_unique_id` has the old signature.

- [ ] **Step 3: Add session/cycle identity to camera messages**

Add to `camera_device/camera_to_mqtt.py`:

```python
from uuid import uuid4


def source_event_id(camera_session_id: str, cycle: int, frame_id: str):
    return f"{camera_session_id}:{cycle}:{frame_id}"
```

Change `frame_message` to accept and include `event_id`. In `publish_dataset`, create one session ID before the loop:

```python
camera_session_id = os.environ.get("CAMERA_SESSION_ID") or uuid4().hex
event_id = source_event_id(camera_session_id, cycle, frame_id)
message = frame_message(split, frame_id, image_file, payload, event_id)
```

- [ ] **Step 4: Change PostgreSQL history identity and migration**

Change `record_unique_id` and `history_row`:

```python
def record_unique_id(source_event_id, slot_id):
    return sha256(f"{source_event_id}:{slot_id}".encode("utf-8")).hexdigest()


def history_row(record, source_event_id):
    event_time = unix_timestamp_to_datetime(record["timestamp"])
    return {
        "unique_id": record_unique_id(source_event_id, record["id"]),
        "source_event_id": source_event_id,
        "frame_id": int(record["frame_id"]),
        "id": str(record["id"]),
        "occupied": int(record["occupied"]),
        "timestamp": event_time,
        "startdate": event_time,
        "enddate": None,
        "status": "active",
    }
```

Pass `message["source_event_id"]` through `upload_frame_message` and `apply_scd2_records`. Add `source_event_id TEXT` idempotently to schema and include it in new inserts. Keep it nullable for rows created before the migration.

- [ ] **Step 5: Run all existing tests and commit**

Run:

```powershell
python -m pytest -q
```

Expected: all existing and new tests pass.

Commit:

```powershell
git add camera_device/camera_to_mqtt.py postgres_sink/postgres_sink.py test_stream_clean_to_json.py tests/test_ml_event_identity.py
git commit -m "fix: make MQTT frame identity unique across camera cycles"
```

## Task 3: Deterministic Synthetic Transition Generator

**Files:**
- Create: `ml_service/simulator.py`
- Create: `tests/test_ml_simulator.py`

- [ ] **Step 1: Write deterministic and horizon tests**

```python
from datetime import datetime

from ml_service.simulator import SimulationConfig, simulate


def test_simulator_is_reproducible_and_reaches_full_within_three_hours():
    config = SimulationConfig(days=1, seed=42, start=datetime(2026, 1, 1))
    first = simulate(config)
    second = simulate(config)
    assert first == second
    assert any(snapshot.occupied_count == 60 for snapshot in first.snapshots)
    assert max(sample.seconds_to_full for sample in first.samples) <= 10800
    assert min(sample.seconds_to_full for sample in first.samples) >= 0
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_simulator.py
```

Expected: fail because `ml_service.simulator` is missing.

- [ ] **Step 3: Implement simulator data types and deterministic cycle generation**

Implement these public types in `ml_service/simulator.py`:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SimulationConfig:
    days: int = 30
    seed: int = 20260727
    start: datetime = datetime(2026, 1, 1)
    sample_interval_seconds: int = 10
    min_fill_seconds: int = 900
    max_fill_seconds: int = 10800


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
```

Implement `simulate(config)` with a local `random.Random(config.seed)`. Generate cycles with a random initial occupancy of 20–45, a random full deadline of 900–10,800 seconds, stochastic interim arrivals/departures, a forced fill at the deadline, a short full hold, and a departure reset. Record transitions only when a slot changes. Record snapshots every `sample_interval_seconds`. Label snapshots by a reverse scan over full timestamps and exclude snapshots with no next full event or a target above 10,800 seconds.

- [ ] **Step 4: Run simulator tests and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_simulator.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/simulator.py tests/test_ml_simulator.py
git commit -m "feat: add deterministic parking occupancy simulator"
```

## Task 4: Persist Synthetic SCD2 History

**Files:**
- Create: `ml_service/simulation_repository.py`
- Create: `tests/test_ml_simulation_repository.py`

- [ ] **Step 1: Write fake-connection transaction tests**

Test that `persist_simulation(conn, config, result, run_id)`:

```python
def test_persist_simulation_writes_run_transitions_and_commits(fake_conn, small_result):
    persist_simulation(fake_conn, SMALL_CONFIG, small_result, RUN_ID)
    sql = "\n".join(call.sql for call in fake_conn.calls)
    assert "INSERT INTO parking_simulation_runs" in sql
    assert "INSERT INTO parking_simulated_slot_history" in sql
    assert fake_conn.commit_count == 1
    assert fake_conn.rollback_count == 0
```

Also assert transition identities differ for separate timestamps and are stable for the same run, slot, and timestamp.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_simulation_repository.py
```

Expected: fail because the repository module is missing.

- [ ] **Step 3: Implement SCD2 conversion and persistence**

Public functions:

```python
def simulated_unique_id(run_id, slot_id, observed_at):
    value = f"{run_id}:{slot_id}:{observed_at.isoformat(timespec='microseconds')}"
    return sha256(value.encode("utf-8")).hexdigest()


def transitions_to_scd2(run_id, transitions):
    grouped = defaultdict(list)
    for transition in sorted(transitions, key=lambda item: (item.slot_id, item.observed_at)):
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
                        run_id, slot_id, transition.observed_at
                    ),
                    "simulation_run_id": run_id,
                    "slot_id": slot_id,
                    "occupied": transition.occupied,
                    "startdate": transition.observed_at,
                    "enddate": (
                        next_transition.observed_at if next_transition else None
                    ),
                    "status": "inactive" if next_transition else "active",
                }
            )
    return rows
```

`persist_simulation` must call `ensure_ml_schema`, insert the run with JSON parameters, batch insert SCD2 rows with `executemany`, update transition/sample/excluded counts, and commit once. On any exception it rolls back and re-raises.

Add a CLI that loads `.env`, connects through `DATABASE_URL`, accepts `--days`, `--seed`, and optional `--run-id`, runs the simulation, and prints only run ID and counts. When `--run-id` is absent, generate `uuid4()`.

- [ ] **Step 4: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_simulation_repository.py tests/test_ml_simulator.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/simulation_repository.py tests/test_ml_simulation_repository.py
git commit -m "feat: persist simulated parking SCD2 history"
```

## Task 5: Shared Feature Builder and Training Materialization

**Files:**
- Create: `ml_service/features.py`
- Create: `ml_service/materialize_training.py`
- Create: `tests/test_ml_features.py`

- [ ] **Step 1: Write no-future-leakage and feature-parity tests**

```python
def test_feature_builder_ignores_events_after_observed_at():
    builder = RollingFeatureBuilder()
    builder.update(t0, empty_state)
    before = builder.vector(t0)
    builder.update(t0 + timedelta(seconds=1), state_with_a01_occupied)
    assert before == RollingFeatureBuilder.from_history([(t0, empty_state)]).vector(t0)


def test_offline_and_realtime_feature_vectors_match():
    offline = build_feature_rows(snapshots)[-1]["features"]
    realtime = replay_feature_vector(snapshots)
    assert offline == realtime
    assert tuple(offline) == FEATURE_NAMES
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_features.py
```

Expected: fail because `ml_service.features` is missing.

- [ ] **Step 3: Implement one shared rolling feature API**

Define:

```python
WINDOWS = ((60, "1m"), (300, "5m"), (900, "15m"), (1800, "30m"), (3600, "60m"))
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
FEATURE_NAMES = (
    BASE_FEATURE_NAMES
    + WINDOW_FEATURE_NAMES
    + CALENDAR_FEATURE_NAMES
    + tuple(f"slot_{slot_id}" for slot_id in SLOT_IDS)
)


class RollingFeatureBuilder:
    def __init__(self):
        self.history = deque()
        self.previous_state = empty_slot_state()
        self.last_arrival_at = None
        self.last_departure_at = None

    def update(self, observed_at, state):
        if self.history and observed_at < self.history[-1][0]:
            raise ValueError("feature updates must be chronological")
        arrivals = sum(
            self.previous_state[slot_id] == 0 and state[slot_id] == 1
            for slot_id in SLOT_IDS
        )
        departures = sum(
            self.previous_state[slot_id] == 1 and state[slot_id] == 0
            for slot_id in SLOT_IDS
        )
        if arrivals:
            self.last_arrival_at = observed_at
        if departures:
            self.last_departure_at = observed_at
        self.history.append(
            (observed_at, sum(state.values()), arrivals, departures, dict(state))
        )
        cutoff = observed_at - timedelta(seconds=max(seconds for seconds, _ in WINDOWS))
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        self.previous_state = dict(state)

    def vector(self, observed_at):
        if not self.history:
            raise RuntimeError("at least one state update is required")
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
            elapsed = max((observed_at - rows[0][0]).total_seconds(), 1)
            slope = (occupied_count - rows[0][1]) / elapsed
            values[f"arrivals_{label}"] = arrivals
            values[f"departures_{label}"] = departures
            values[f"occupancy_slope_{label}"] = slope
        values["seconds_since_last_arrival"] = (
            min(int((observed_at - self.last_arrival_at).total_seconds()), 3600)
            if self.last_arrival_at else 3600
        )
        values["seconds_since_last_departure"] = (
            min(int((observed_at - self.last_departure_at).total_seconds()), 3600)
            if self.last_departure_at else 3600
        )
        second_of_day = (
            observed_at.hour * 3600 + observed_at.minute * 60 + observed_at.second
        )
        values["second_of_day_sin"] = sin(2 * pi * second_of_day / 86400)
        values["second_of_day_cos"] = cos(2 * pi * second_of_day / 86400)
        values["day_of_week_sin"] = sin(2 * pi * observed_at.weekday() / 7)
        values["day_of_week_cos"] = cos(2 * pi * observed_at.weekday() / 7)
        for slot_id in SLOT_IDS:
            values[f"slot_{slot_id}"] = current_state[slot_id]
        if tuple(values) != FEATURE_NAMES:
            raise RuntimeError("feature ordering does not match FEATURE_NAMES")
        return values
```

Use the same class from offline materialization and realtime prediction. Encode time-of-day and day-of-week with sine/cosine. Use `3600` when no arrival or departure exists in the retained window.

- [ ] **Step 4: Materialize labeled samples into PostgreSQL**

In `ml_service/materialize_training.py`, read one completed simulation run, reconstruct snapshots in timestamp order, calculate ordered features, assign temporal split from row position (`70/15/15`), and batch insert:

```sql
INSERT INTO parking_model_training_data
    (simulation_run_id, observed_at,
     occupied_count, free_count, occupancy_ratio,
     arrivals_1m, arrivals_5m, arrivals_15m, arrivals_30m, arrivals_60m,
     departures_1m, departures_5m, departures_15m, departures_30m, departures_60m,
     occupancy_slope_1m, occupancy_slope_5m, occupancy_slope_15m,
     occupancy_slope_30m, occupancy_slope_60m,
     seconds_since_last_arrival, seconds_since_last_departure,
     second_of_day_sin, second_of_day_cos, day_of_week_sin, day_of_week_cos,
     features, slot_states, next_full_at, seconds_to_full, dataset_split)
VALUES
    (%s, %s,
     %s, %s, %s,
     %s, %s, %s, %s, %s,
     %s, %s, %s, %s, %s,
     %s, %s, %s, %s, %s,
     %s, %s, %s, %s, %s, %s,
     %s::jsonb, %s::jsonb, %s, %s, %s)
ON CONFLICT (simulation_run_id, observed_at) DO UPDATE
SET occupied_count = EXCLUDED.occupied_count,
    free_count = EXCLUDED.free_count,
    occupancy_ratio = EXCLUDED.occupancy_ratio,
    features = EXCLUDED.features,
    slot_states = EXCLUDED.slot_states,
    next_full_at = EXCLUDED.next_full_at,
    seconds_to_full = EXCLUDED.seconds_to_full,
    dataset_split = EXCLUDED.dataset_split
```

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_features.py tests/test_ml_simulator.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/features.py ml_service/materialize_training.py tests/test_ml_features.py
git commit -m "feat: materialize shared parking ETA features"
```

## Task 6: Train and Export Model Artifacts

**Files:**
- Create: `ml_service/train.py`
- Create: `tests/test_ml_training.py`
- Create: `models/parking_fill_eta/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Write artifact and metric tests**

```python
def test_train_writes_versioned_artifacts_and_beats_baseline(tmp_path, training_rows):
    result = train_model(training_rows, tmp_path, model_version="test-v1")
    assert (tmp_path / "model.joblib").exists()
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "feature_schema.json").exists()
    assert result.metrics["mae_seconds"] < result.metrics["baseline_mae_seconds"]
    assert result.metadata["model_version"] == "test-v1"
    assert len(result.metadata["artifact_sha256"]) == 64
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_training.py
```

Expected: fail because `ml_service.train` is missing.

- [ ] **Step 3: Implement training and evaluation**

Load ordered feature values using `FEATURE_NAMES`. Fit only `dataset_split='train'`, report validation metrics as a pre-test sanity check, and report final acceptance metrics only on `dataset_split='test'`:

```python
model = HistGradientBoostingRegressor(
    loss="absolute_error",
    learning_rate=0.05,
    max_iter=300,
    max_leaf_nodes=31,
    min_samples_leaf=30,
    l2_regularization=0.1,
    random_state=seed,
    early_stopping=False,
)
```

Calculate MAE, median absolute error, P90 absolute error, percentages within 300/900/1800 seconds, and baseline MAE. Reject export when test MAE is not lower than baseline MAE.

Write the model atomically through a temporary file, calculate SHA-256, then write `metadata.json` and `feature_schema.json`. Insert the matching `parking_model_registry` row in the same command.

- [ ] **Step 4: Keep generated binary artifacts out of Git**

Add:

```gitignore
models/parking_fill_eta/*.joblib
models/parking_fill_eta/metadata.json
models/parking_fill_eta/feature_schema.json
!models/parking_fill_eta/.gitkeep
```

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_training.py tests/test_ml_features.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/train.py tests/test_ml_training.py models/parking_fill_eta/.gitkeep .gitignore
git commit -m "feat: train and export parking fill ETA model"
```

## Task 7: Pure Prediction Engine

**Files:**
- Create: `ml_service/predictor.py`
- Create: `tests/test_ml_predictor.py`

- [ ] **Step 1: Write load-validation and bounded-output tests**

```python
def test_predictor_bounds_eta_and_calculates_fill_time(fake_artifacts):
    predictor = Predictor.load(fake_artifacts)
    result = predictor.predict(observed_at, feature_vector)
    assert result.seconds_to_full == 10800
    assert result.predicted_fill_at == observed_at + timedelta(seconds=10800)


def test_predictor_rejects_schema_hash_mismatch(fake_artifacts):
    fake_artifacts.write_schema_with_wrong_hash()
    with pytest.raises(RuntimeError, match="feature schema hash"):
        Predictor.load(fake_artifacts.path)
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_predictor.py
```

Expected: fail because `ml_service.predictor` is missing.

- [ ] **Step 3: Implement trusted artifact loading and prediction**

Define:

```python
@dataclass(frozen=True)
class Prediction:
    seconds_to_full: int
    predicted_fill_at: datetime
    inference_ms: float


class Predictor:
    @classmethod
    def load(cls, artifact_dir):
        artifact_dir = Path(artifact_dir)
        model_path = artifact_dir / "model.joblib"
        metadata = json.loads((artifact_dir / "metadata.json").read_text("utf-8"))
        schema_bytes = (artifact_dir / "feature_schema.json").read_bytes()
        schema = json.loads(schema_bytes)
        schema_hash = sha256(schema_bytes).hexdigest()
        model_hash = sha256(model_path.read_bytes()).hexdigest()
        if schema_hash != metadata["feature_schema_hash"]:
            raise RuntimeError("feature schema hash mismatch")
        if model_hash != metadata["artifact_sha256"]:
            raise RuntimeError("model artifact hash mismatch")
        if tuple(schema["feature_names"]) != FEATURE_NAMES:
            raise RuntimeError("embedded feature names do not match runtime")
        return cls(
            model=joblib.load(model_path),
            model_version=metadata["model_version"],
        )

    def predict(self, observed_at, ordered_features):
        started = perf_counter()
        raw = float(self.model.predict([ordered_features])[0])
        seconds = min(10800, max(0, int(round(raw))))
        return Prediction(
            seconds,
            observed_at + timedelta(seconds=seconds),
            (perf_counter() - started) * 1000,
        )
```

- [ ] **Step 4: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_predictor.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/predictor.py tests/test_ml_predictor.py
git commit -m "feat: add bounded parking ETA prediction engine"
```

## Task 8: MQTT Prediction Service

**Files:**
- Create: `ml_service/prediction_service.py`
- Create: `tests/test_ml_prediction_service.py`

- [ ] **Step 1: Write one-frame/one-row and duplicate tests**

```python
def test_frame_creates_one_prediction(service, fake_conn, valid_message):
    result = service.process_message(valid_message)
    assert result.inserted is True
    assert fake_conn.prediction_rows == 1
    assert result.capacity == 60


def test_duplicate_source_event_is_idempotent(service, fake_conn, valid_message):
    service.process_message(valid_message)
    second = service.process_message(valid_message)
    assert second.inserted is False
    assert fake_conn.prediction_rows == 1
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest -q tests/test_ml_prediction_service.py
```

Expected: fail because the service module is missing.

- [ ] **Step 3: Implement message processing**

`PredictionService.process_message` must:

```python
def process_message(self, message):
    event_id = str(message["source_event_id"])
    frame_id = int(message["frame_id"])
    payload = message["payload"]
    observed_at = unix_timestamp_to_datetime(payload[0]["timestamp"])
    state, unknown = normalize_slot_state(self.state, payload)
    self.feature_builder.update(observed_at, state)
    features = self.feature_builder.vector(observed_at)
    prediction = self.predictor.predict(
        observed_at,
        [features[name] for name in FEATURE_NAMES],
    )
    inserted = self.repository.insert_prediction(
        event_id, frame_id, observed_at, self.predictor.model_version,
        sum(state.values()), prediction,
    )
    self.state = state
    return ProcessResult(inserted, unknown, 60)
```

The repository uses `INSERT ... ON CONFLICT (source_event_id, model_version) DO NOTHING RETURNING prediction_id`.

- [ ] **Step 4: Add MQTT lifecycle**

Load `.env`, artifact directory, database settings, MQTT host/port/topic/credentials, and a stable subscriber client ID. Configure MQTT auth, subscribe at QoS 1, retry connections, decode JSON, and log validation/database failures without secrets. Ensure ML schema before subscribing.

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest -q tests/test_ml_prediction_service.py tests/test_ml_predictor.py
```

Expected: pass.

Commit:

```powershell
git add ml_service/prediction_service.py tests/test_ml_prediction_service.py
git commit -m "feat: persist realtime MQTT parking ETA predictions"
```

## Task 9: Docker Image and Compose Service

**Files:**
- Create: `ml_service/requirements.txt`
- Create: `ml_service/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.dockerignore`

- [ ] **Step 1: Pin identical training/inference dependencies**

Create `ml_service/requirements.txt`:

```text
joblib==1.4.2
numpy==2.1.3
paho-mqtt==2.1.0
pandas==2.2.3
psycopg2-binary==2.9.9
python-dotenv==1.0.1
scikit-learn==1.5.2
```

- [ ] **Step 2: Add inference-only Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY ml_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ml_service /app/ml_service
COPY models/parking_fill_eta /app/models/parking_fill_eta

ENV PYTHONUNBUFFERED=1
ENV MODEL_DIR=/app/models/parking_fill_eta
CMD ["python", "-m", "ml_service.prediction_service"]
```

- [ ] **Step 3: Add prediction service to Compose**

Add:

```yaml
  prediction-service:
    image: minh333/parking-prediction-service:latest
    build:
      context: .
      dockerfile: ml_service/Dockerfile
    container_name: parking-prediction-service
    depends_on:
      mqtt-broker:
        condition: service_started
      postgres-sink:
        condition: service_started
    environment:
      DATABASE_URL: ${DATABASE_URL}
      MQTT_HOST: mqtt-broker
      MQTT_PORT: "1883"
      MQTT_TOPIC: ${MQTT_TOPIC:-parking/frames}
      MQTT_USERNAME: ${MQTT_SUB_USERNAME}
      MQTT_PASSWORD: ${MQTT_SUB_PASSWORD}
      MQTT_CLIENT_ID: parking-prediction-service
      MODEL_DIR: /app/models/parking_fill_eta
```

Ensure `.dockerignore` does not exclude `models/parking_fill_eta/`.

- [ ] **Step 4: Validate Compose and Docker build context**

Run:

```powershell
docker compose config --quiet
docker build -f ml_service/Dockerfile .
```

Expected: Compose exits zero. The build exits zero only after Task 6 has generated all three model artifacts; a missing artifact is an intentional build failure.

- [ ] **Step 5: Commit**

```powershell
git add ml_service/requirements.txt ml_service/Dockerfile docker-compose.yml .dockerignore
git commit -m "build: add embedded-model prediction service image"
```

## Task 10: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `build_image.sh`
- Test: all test files

- [ ] **Step 1: Document the exact offline workflow**

Add commands to `README.md`:

```powershell
python -m pip install -r ml_service/requirements.txt
$simulationRunId = [guid]::NewGuid().ToString()
python -m ml_service.simulation_repository --run-id $simulationRunId
python -m ml_service.materialize_training --simulation-run-id $simulationRunId
python -m ml_service.train --simulation-run-id $simulationRunId --model-version v1
docker compose build prediction-service
docker compose up
```

Document the five ML tables, 60-slot catalog, three-hour bounded ETA, model artifact directory, and query:

```sql
SELECT observed_at, occupied_count, predicted_seconds_to_full,
       predicted_fill_at, model_version
FROM parking_fill_predictions
ORDER BY prediction_id DESC
LIMIT 50;
```

- [ ] **Step 2: Add prediction image build/push commands**

Append to `build_image.sh` using POSIX paths:

```sh
docker build -t minh333/parking-prediction-service:latest -f ml_service/Dockerfile .
docker push minh333/parking-prediction-service:latest
```

Also replace existing backslash Dockerfile paths in that shell script with forward slashes.

- [ ] **Step 3: Run the complete automated suite**

Run:

```powershell
python -m pytest -q
docker compose config --quiet
git diff --check
```

Expected: every test passes, Compose validation exits zero, and `git diff --check` prints no errors.

- [ ] **Step 4: Run database integration smoke test**

With a disposable PostgreSQL database configured in `.env`, run:

```powershell
$smokeRunId = [guid]::NewGuid().ToString()
python -m ml_service.simulation_repository --days 1 --seed 42 --run-id $smokeRunId
python -m ml_service.materialize_training --simulation-run-id $smokeRunId
python -m ml_service.train --simulation-run-id $smokeRunId --model-version smoke-v1
```

Verify:

```sql
SELECT count(*) FROM parking_simulated_slot_history;
SELECT dataset_split, count(*) FROM parking_model_training_data GROUP BY dataset_split;
SELECT model_version, metrics FROM parking_model_registry WHERE model_version = 'smoke-v1';
```

Expected: nonzero history rows, all three temporal splits, and one model registry row whose test MAE is lower than baseline MAE.

- [ ] **Step 5: Run MQTT prediction smoke test**

Build and start:

```powershell
docker compose build prediction-service
docker compose up -d
docker compose logs --tail=100 prediction-service
```

After at least two camera frames, verify:

```sql
SELECT count(*) AS predictions,
       min(predicted_seconds_to_full) AS min_eta,
       max(predicted_seconds_to_full) AS max_eta
FROM parking_fill_predictions;
```

Expected: prediction count is at least two and `0 <= min_eta <= max_eta <= 10800`.

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md build_image.sh
git commit -m "docs: document parking ETA training and inference workflow"
```

- [ ] **Step 7: Final verification**

Run:

```powershell
python -m pytest -q
docker compose config --quiet
git status --short
```

Expected: all tests pass, Compose is valid, and the working tree is clean.
