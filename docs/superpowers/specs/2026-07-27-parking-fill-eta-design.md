# Realtime Parking-Fill ETA Design

## Goal

Extend the current `camera-device -> MQTT -> PostgreSQL` pipeline with:

1. Reproducible synthetic parking history for a fixed 60-slot car park.
2. A regression model that estimates the next time the car park reaches 60/60 occupancy.
3. Realtime inference after every complete MQTT frame.
4. A PostgreSQL table containing each realtime prediction.
5. A model artifact produced outside the inference service and copied into its Docker image.

The model always returns an ETA between 0 and 10,800 seconds (three hours). The synthetic process is intentionally constructed so every training sample has a next-full event within that range.

## Existing-System Constraints

- The camera publishes one frame per second on `parking/frames`.
- A frame contains a list of slot records with `frame_id`, `id`, `occupied`, and `timestamp`.
- The raw dataset has 766 frames and 59 observed slot IDs.
- The observed layout is:
  - `A01-A14`
  - `B01-B13`
  - `C01-C12`
  - `D01-D10`
  - `E01-E10`
- The fixed 60th slot is `E11`.
- MQTT uses QoS 1, so consumers must tolerate duplicate messages.
- Camera dataset looping reuses frame IDs. Frame ID alone is therefore not a valid event identifier.

## Architecture

```text
camera-device
    |
    | parking/frames
    v
MQTT broker
    |----------------------------------|
    v                                  v
postgres-sink                    prediction-service
    |                                  |
    v                                  v
parking_slot_history          parking_fill_predictions

offline commands:
simulator -> PostgreSQL training tables -> trainer -> models/parking_fill_eta/
                                                        |
                                                        v
                                             Docker build copies model
```

The new `prediction-service` is inference-only. It never generates training data or trains a model at startup.

Offline simulation and training run before the image is built:

```text
simulate and persist data
          ->
train from PostgreSQL
          ->
write model artifacts under models/parking_fill_eta/
          ->
build prediction-service image
```

## Repository Layout

```text
models/
  parking_fill_eta/
    model.joblib
    metadata.json
    feature_schema.json
ml_service/
  Dockerfile
  requirements.txt
  simulator.py
  features.py
  train.py
  prediction_service.py
```

`model.joblib`, `metadata.json`, and `feature_schema.json` are Docker build inputs. The Dockerfile copies them to `/app/models/parking_fill_eta/`.

Training and inference pin identical versions of Python, scikit-learn, NumPy, pandas, and joblib. The model is loaded only from the trusted local build context.

## Event Identity

Every published camera frame receives a `source_event_id` that is unique across camera restarts and dataset cycles. The camera message adds:

```json
{
  "source_event_id": "<camera-session-id>:<cycle>:<frame-id>",
  "frame_id": 1,
  "payload": []
}
```

The PostgreSQL sink uses the source event in deterministic record identity:

```text
unique_id = sha256(source_event_id + ":" + slot_id)
```

This replaces `sha256(frame_id:id)`, which collides when the 766-frame dataset loops. Existing rows remain readable; new columns and indexes are added through idempotent schema migration.

Prediction idempotency uses a unique constraint on:

```text
(source_event_id, model_version)
```

## Fixed Slot State

The canonical slot catalog contains exactly 60 rows. The inference service maintains an in-memory last-known state for all slots:

- A slot present in the frame replaces its previous state.
- A slot missing from the frame retains its last-known state.
- A slot never observed in the current service session starts as empty.
- `E11` starts empty because it is not present in the current 59-ID image dataset.
- Unknown slot IDs are rejected from the state vector and logged.

This normalization produces a stable 60-element vector even when YOLO labels omit slots in a frame.

## Synthetic Data

### Timeline

- Duration: 30 virtual days.
- Internal resolution: one second.
- Training sample interval: ten seconds.
- Expected training samples: approximately 259,200 before filtering.
- Random seed: configurable and persisted with the simulation run.

The simulator writes SCD2 state transitions rather than 60 records for every second. A training feature builder reconstructs ten-second snapshots from those transitions.

### Occupancy Process

Each cycle:

1. Starts between 20 and 45 occupied slots.
2. Applies stochastic arrivals and departures.
3. Gradually increases net arrival pressure.
4. Reaches 60 occupied slots after 15 to 180 minutes.
5. Holds full occupancy for a short random period.
6. Applies a departure phase and begins the next cycle.

Arrival and departure intensities vary by:

- Time of day.
- Day of week.
- Current occupancy.
- Cycle phase.
- Short random bursts.

Vehicle dwell times follow a positively skewed distribution so most visits are moderate while some are substantially longer.

The generator enforces a next-full event within 10,800 seconds for every retained training sample. It does not silently clip training labels; samples without a valid future full event are excluded and counted in run metadata.

### Reproducibility

A simulation run is identified by `simulation_run_id` and records:

- Random seed.
- Start and end timestamps.
- Capacity.
- Simulator version.
- Generation parameters.
- Counts of transitions, samples, and excluded samples.

Running the same simulator version with the same parameters and seed must produce identical transitions.

## Database Tables

### `parking_slots`

Canonical 60-slot dimension:

```text
slot_id             text primary key
row_name            text not null
column_number       integer not null
capacity_member     boolean not null default true
created_at          timestamp not null
```

### `parking_simulation_runs`

Simulation metadata:

```text
simulation_run_id   uuid primary key
seed                integer not null
simulator_version   text not null
started_at          timestamp not null
ended_at            timestamp not null
parameters          jsonb not null
transition_count    integer not null
sample_count        integer not null
excluded_count      integer not null
created_at          timestamp not null
```

### `parking_simulated_slot_history`

Raw synthetic SCD2 transitions:

```text
unique_id           text primary key
simulation_run_id   uuid not null
slot_id             text not null
occupied            integer not null
startdate           timestamp not null
enddate             timestamp null
status              text not null
```

Indexes cover `(simulation_run_id, slot_id, startdate)` and active rows.

### `parking_model_training_data`

Materialized model-ready samples:

```text
sample_id                    bigserial primary key
simulation_run_id            uuid not null
observed_at                  timestamp not null
occupied_count               integer not null
free_count                   integer not null
occupancy_ratio              double precision not null
arrivals_1m                  integer not null
arrivals_5m                  integer not null
arrivals_15m                 integer not null
arrivals_30m                 integer not null
arrivals_60m                 integer not null
departures_1m                integer not null
departures_5m                integer not null
departures_15m               integer not null
departures_30m               integer not null
departures_60m               integer not null
occupancy_slope_1m           double precision not null
occupancy_slope_5m           double precision not null
occupancy_slope_15m          double precision not null
occupancy_slope_30m          double precision not null
occupancy_slope_60m          double precision not null
seconds_since_last_arrival   integer not null
seconds_since_last_departure integer not null
second_of_day_sin            double precision not null
second_of_day_cos            double precision not null
day_of_week_sin              double precision not null
day_of_week_cos              double precision not null
slot_states                  jsonb not null
next_full_at                 timestamp not null
seconds_to_full              integer not null
dataset_split                text not null
created_at                   timestamp not null
```

The flattened 60 slot-state columns used by the estimator are generated in a deterministic order defined by `feature_schema.json`. `slot_states` remains JSONB in PostgreSQL for inspection and regeneration.

### `parking_model_registry`

Model metadata:

```text
model_version        text primary key
algorithm            text not null
artifact_sha256      text not null
feature_schema_hash  text not null
training_run_id      uuid not null
metrics              jsonb not null
parameters           jsonb not null
trained_at           timestamp not null
```

### `parking_fill_predictions`

Realtime results:

```text
prediction_id                 bigserial primary key
source_event_id               text not null
source_frame_id               integer not null
observed_at                   timestamp not null
model_version                 text not null
occupied_count                integer not null
capacity                      integer not null default 60
predicted_seconds_to_full     integer not null
predicted_fill_at             timestamp not null
inference_ms                  double precision not null
created_at                    timestamp not null
unique (source_event_id, model_version)
```

`predicted_seconds_to_full` is constrained to `0 <= value <= 10800`.

## Feature Engineering

Model input includes:

- Current occupied count, free count, and occupancy ratio.
- Current state of all 60 canonical slots.
- Arrival and departure counts over 1, 5, 15, 30, and 60 minutes.
- Occupancy slope over the same windows.
- Seconds since the most recent arrival and departure.
- Cyclic second-of-day features.
- Cyclic day-of-week features.

The training feature builder and realtime feature builder share the same implementation. `feature_schema.json` defines exact feature names and ordering. Inference refuses to start if the schema hash in the image does not match model metadata.

## Model

The primary estimator is `HistGradientBoostingRegressor`.

Target:

```text
seconds_to_full = next_full_at - observed_at
```

Inference output is rounded to integer seconds and bounded to `[0, 10800]`. The predicted timestamp is:

```text
predicted_fill_at = observed_at + predicted_seconds_to_full
```

A simple baseline is also evaluated:

```text
baseline_eta = free_slots / positive_net_arrival_rate
```

The trained model must outperform this baseline on the held-out temporal test set.

## Dataset Split and Evaluation

Samples are ordered by simulated time and split:

- First 70%: training.
- Next 15%: validation.
- Final 15%: test.

Random row splitting is prohibited because adjacent snapshots are strongly correlated.

Recorded metrics:

- Mean absolute error.
- Median absolute error.
- 90th-percentile absolute error.
- Percentage within 5 minutes.
- Percentage within 15 minutes.
- Percentage within 30 minutes.
- Baseline metrics on the identical test rows.

The initial acceptance criterion is:

- Model test MAE is lower than baseline test MAE.
- Every persisted prediction and training target is between 0 and 10,800 seconds.
- Model and feature-schema hashes match.

The synthetic-data metrics demonstrate pipeline correctness, not expected accuracy on real parking behavior. Metrics must be monitored again as real outcomes become available.

## Realtime Inference Flow

For every complete MQTT frame:

1. Validate JSON structure, source event ID, timestamp, slot IDs, and occupied values.
2. Ignore an already-persisted `(source_event_id, model_version)`.
3. Merge frame values into the last-known 60-slot state.
4. Update rolling arrival/departure buffers.
5. Create features using the shared feature implementation.
6. Run the embedded model.
7. Bound and round ETA.
8. Insert one `parking_fill_predictions` row.
9. Log frame ID, model version, ETA, and inference duration without credentials.

Prediction reads directly from MQTT and does not wait for `postgres-sink`. The two subscribers operate independently. PostgreSQL remains the final record of both source history and prediction results.

## Startup and Failure Handling

- The service retries PostgreSQL and MQTT connections with bounded backoff.
- Startup fails fast if model files are absent, corrupt, or schema-incompatible.
- Invalid messages are logged and skipped without terminating the subscriber.
- Duplicate MQTT delivery is harmless because of the database unique constraint.
- Database insert failures are rolled back and logged.
- The MQTT broker currently has persistence disabled, so this prototype provides best-effort processing during broker downtime. Durable replay is outside this feature's initial scope.
- Training refuses to overwrite a model version unless explicitly requested.
- Simulator and feature generation use transactions and mark a run complete only after all rows are committed.

## Docker Build and Operation

The build order is:

```text
1. Run simulator against PostgreSQL.
2. Build materialized training samples.
3. Train and write models/parking_fill_eta/*.
4. Build prediction-service image.
5. Start or restart Docker Compose.
```

The Docker image contains immutable model files. Updating a model requires a new image build and a new `model_version`.

No database credentials, MQTT passwords, or connection strings are stored in model metadata or image layers.

## Testing

### Unit tests

- Canonical catalog contains exactly 60 valid IDs.
- Same seed and parameters produce identical simulated transitions.
- Every retained target is in `[0, 10800]`.
- Feature generation never reads beyond the sample timestamp.
- Offline and realtime feature builders return identical vectors for the same history.
- Model output is bounded correctly.
- Model/schema hash mismatch prevents startup.
- Duplicate source events do not create duplicate predictions.

### Integration tests

- Schema creation and migrations are idempotent.
- Simulator writes run metadata and SCD2 transitions.
- Feature builder writes expected temporal splits.
- Trainer persists all three required artifacts and a registry row.
- One MQTT frame results in one prediction row.
- Restarting prediction service loads the embedded artifact and does not train.
- Camera cycle two produces unique source event IDs and valid history rows.

### End-to-end acceptance

- Build the image only after a successful training run.
- Start broker, PostgreSQL sink, camera, and prediction service.
- Observe one prediction per camera frame.
- Confirm all prediction ETAs are at most three hours.
- Confirm test MAE beats the baseline on the same synthetic test interval.

## Explicit Non-Goals

- Online or incremental retraining inside the inference container.
- Automatically claiming real-world accuracy from synthetic-data metrics.
- Predicting ETA for each individual parking slot.
- Predicting whether the car park will fill beyond the fixed three-hour design horizon.
- A dashboard or external prediction API.
- Durable MQTT replay while broker persistence remains disabled.
