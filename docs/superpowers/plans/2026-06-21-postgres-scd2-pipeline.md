# PostgreSQL SCD2 Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ThingSpeak Docker sink with a PostgreSQL sink that stores parking slot history using SCD Type 2, while changing slot IDs to notebook-style labels such as `A01` and `B02`.

**Architecture:** Keep `camera-device -> MQTT broker -> sink` as the runtime shape. The camera continues to reuse `stream_clean_to_json.py` for frame parsing and publishes MQTT messages; the sink subscribes to those messages and writes SCD2 rows into PostgreSQL. PostgreSQL runs as a Docker Compose service with a persistent volume.

**Tech Stack:** Python 3.12, paho-mqtt, psycopg2-binary, PostgreSQL 16, Docker Compose, pytest.

---

### Task 1: Notebook-Style Slot IDs

**Files:**
- Modify: `stream_clean_to_json.py`
- Modify: `test_stream_clean_to_json.py`
- Modify: `output_template.json`

- [ ] Write failing tests that expect `id` values like `A01`, `A02`, `B01`.
- [ ] Run `python -m pytest -q test_stream_clean_to_json.py::test_payload_uses_notebook_style_slot_ids`.
- [ ] Change slot assignment to use row letters and zero-padded two-digit columns.
- [ ] Run `python -m pytest -q test_stream_clean_to_json.py`.

### Task 2: PostgreSQL SCD2 Sink Logic

**Files:**
- Create: `postgres_sink/postgres_sink.py`
- Create: `postgres_sink/requirements.txt`
- Create: `postgres_sink/Dockerfile`
- Modify: `test_stream_clean_to_json.py`

- [ ] Write failing tests for deterministic `unique_id = sha256(frame_id:id)` and SCD2 close/insert behavior by slot `id`.
- [ ] Run the new tests and verify they fail because `postgres_sink` does not exist.
- [ ] Implement PostgreSQL schema creation, timestamp conversion, deterministic hash, and SCD2 write logic.
- [ ] Run all tests and verify they pass.

### Task 3: Docker Compose PostgreSQL Runtime

**Files:**
- Modify: `docker-compose.yml`
- Modify: `build_image.sh`
- Modify: `README_docker_mqtt_pipeline.md`

- [ ] Replace `thingspeak-sink` with `postgres-sink`.
- [ ] Add `postgres` service, credentials, healthcheck, and persistent volume.
- [ ] Update build/push commands and docs for PostgreSQL.
- [ ] Run tests again after config changes.
