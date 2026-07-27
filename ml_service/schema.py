from ml_service.slots import ROW_WIDTHS


def schema_statements():
    return [
        """
        CREATE TABLE IF NOT EXISTS parking_slots (
            slot_id TEXT PRIMARY KEY,
            row_name TEXT NOT NULL,
            column_number INTEGER NOT NULL,
            capacity_member BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parking_simulation_runs (
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
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parking_simulated_slot_history (
            unique_id TEXT PRIMARY KEY,
            simulation_run_id UUID NOT NULL
                REFERENCES parking_simulation_runs(simulation_run_id),
            slot_id TEXT NOT NULL REFERENCES parking_slots(slot_id),
            occupied INTEGER NOT NULL CHECK (occupied IN (0, 1)),
            startdate TIMESTAMP NOT NULL,
            enddate TIMESTAMP NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parking_model_training_data (
            sample_id BIGSERIAL PRIMARY KEY,
            simulation_run_id UUID NOT NULL
                REFERENCES parking_simulation_runs(simulation_run_id),
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
            seconds_to_full INTEGER NOT NULL
                CHECK (seconds_to_full BETWEEN 0 AND 10800),
            dataset_split TEXT NOT NULL
                CHECK (dataset_split IN ('train', 'validation', 'test')),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (simulation_run_id, observed_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parking_model_registry (
            model_version TEXT PRIMARY KEY,
            algorithm TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            feature_schema_hash TEXT NOT NULL,
            training_run_id UUID NOT NULL
                REFERENCES parking_simulation_runs(simulation_run_id),
            metrics JSONB NOT NULL,
            parameters JSONB NOT NULL,
            trained_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parking_fill_predictions (
            prediction_id BIGSERIAL PRIMARY KEY,
            source_event_id TEXT NOT NULL,
            source_frame_id INTEGER NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            model_version TEXT NOT NULL,
            occupied_count INTEGER NOT NULL
                CHECK (occupied_count BETWEEN 0 AND 60),
            capacity INTEGER NOT NULL DEFAULT 60 CHECK (capacity = 60),
            predicted_seconds_to_full INTEGER NOT NULL
                CHECK (predicted_seconds_to_full BETWEEN 0 AND 10800),
            predicted_fill_at TIMESTAMP NOT NULL,
            inference_ms DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_event_id, model_version)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sim_history_run_slot_time
        ON parking_simulated_slot_history
            (simulation_run_id, slot_id, startdate)
        """,
    ]


def ensure_ml_schema(conn):
    with conn.cursor() as cursor:
        for statement in schema_statements():
            cursor.execute(statement)
        for row_name, width in ROW_WIDTHS.items():
            for column in range(1, width + 1):
                cursor.execute(
                    """
                    INSERT INTO parking_slots (slot_id, row_name, column_number)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (slot_id) DO NOTHING
                    """,
                    (f"{row_name}{column:02d}", row_name, column),
                )
    conn.commit()
