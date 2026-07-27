# Camera Schema Image Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phát hành camera và PostgreSQL sink image dùng schema MQTT có `source_event_id`, chạy camera/MJPEG bằng một process tích hợp và rollout pipeline không còn lỗi prediction.

**Architecture:** `camera_to_mqtt.py` là process chính duy nhất của camera container; nó mở MJPEG server trong background và publish cùng frame lên MQTT. Build và inspect camera/sink image trước khi push; chỉ rollout sau khi cả hai tag remote được xác nhận, sau đó kiểm tra log và database theo điều kiện thay vì giả định service đã hoạt động.

**Tech Stack:** POSIX shell, Python 3.12, pytest, Docker Compose, Docker Hub, MQTT, PostgreSQL.

---

### Task 1: Chuyển camera entrypoint thành một process tích hợp

**Files:**
- Modify: `camera_device/entrypoint.sh`
- Test: `tests/test_camera_entrypoint.py`

- [ ] **Step 1: Viết regression test thất bại**

Tạo `tests/test_camera_entrypoint.py`:

```python
from pathlib import Path


def test_camera_entrypoint_runs_one_integrated_process():
    script = Path("camera_device/entrypoint.sh").read_text("utf-8")

    assert "exec python camera_to_mqtt.py" in script
    assert '--start-timestamp "$START_TS"' in script
    assert "--encrypt-payload" in script
    assert '--encryption-key-file "$KEY_FILE"' in script
    assert "python mjpeg_server.py" not in script
    assert "MQTT_PID" not in script
    assert "MJPEG_PID" not in script
```

- [ ] **Step 2: Chạy test và xác nhận RED**

Run:

```powershell
python -m pytest -q tests/test_camera_entrypoint.py
```

Expected: FAIL tại assertion `exec python camera_to_mqtt.py` vì entrypoint hiện dùng hai background process.

- [ ] **Step 3: Sửa entrypoint tối thiểu**

Giữ phần tạo timestamp và key, thay toàn bộ phần sau comment tạo key bằng:

```sh
exec python camera_to_mqtt.py \
  --start-timestamp "$START_TS" \
  --encrypt-payload \
  --encryption-key-file "$KEY_FILE"
```

- [ ] **Step 4: Chạy test focused và toàn bộ suite**

Run:

```powershell
python -m pytest -q tests/test_camera_entrypoint.py
python -m pytest -q
```

Expected: focused test pass; toàn bộ suite báo `67 passed`.

- [ ] **Step 5: Commit sửa lỗi**

Run:

```powershell
git add camera_device/entrypoint.sh tests/test_camera_entrypoint.py
git commit -m "fix: run integrated camera process in container"
```

Expected: commit chỉ chứa entrypoint và regression test.

### Task 2: Build và inspect camera/sink image

**Files:**
- Verify: `camera_device/Dockerfile`
- Verify: `camera_device/camera_to_mqtt.py`
- Verify: `camera_device/entrypoint.sh`
- Verify: `postgres_sink/Dockerfile`
- Verify: `postgres_sink/postgres_sink.py`

- [ ] **Step 1: Kiểm tra Compose trước build**

Run:

```powershell
docker compose --env-file '..\..\.env' config --quiet
```

Expected: exit code `0`.

- [ ] **Step 2: Build đúng hai image**

Run:

```powershell
docker compose --env-file '..\..\.env' build camera-device postgres-sink
```

Expected: build thành công:

```text
minh333/parking-camera-device:latest
minh333/parking-postgres-sink:latest
```

- [ ] **Step 3: Inspect camera image**

Run:

```powershell
docker image inspect minh333/parking-camera-device:latest --format 'ID={{.Id}} CMD={{json .Config.Cmd}}'
docker run --rm --entrypoint sh minh333/parking-camera-device:latest -c "grep -n 'source_event_id' /app/camera_to_mqtt.py && grep -n 'exec python camera_to_mqtt.py' /app/entrypoint.sh && ! grep -n 'python mjpeg_server.py' /app/entrypoint.sh"
```

Expected:

- CMD là `["/app/entrypoint.sh"]`.
- Camera source có `source_event_id`.
- Entry point dùng `exec`.
- Không có standalone MJPEG command.

- [ ] **Step 4: Inspect sink image**

Run:

```powershell
docker image inspect minh333/parking-postgres-sink:latest --format 'ID={{.Id}} CMD={{json .Config.Cmd}}'
docker run --rm --entrypoint sh minh333/parking-postgres-sink:latest -c "grep -n 'source_event_id' /app/postgres_sink.py && grep -n 'record_unique_id(source_event_id' /app/postgres_sink.py"
```

Expected: sink source có schema mới và history identity dùng `source_event_id`.

### Task 3: Push hai image lên Docker Hub

**Files:**
- No source file changes.

- [ ] **Step 1: Ghi lại digest local**

Run:

```powershell
docker image inspect minh333/parking-camera-device:latest --format 'camera={{.Id}}'
docker image inspect minh333/parking-postgres-sink:latest --format 'sink={{.Id}}'
```

Expected: hai SHA256 image ID được in ra.

- [ ] **Step 2: Push camera image**

Run:

```powershell
docker push minh333/parking-camera-device:latest
```

Expected: exit code `0` và Docker in digest của tag `latest`.

- [ ] **Step 3: Push sink image**

Run:

```powershell
docker push minh333/parking-postgres-sink:latest
```

Expected: exit code `0` và Docker in digest của tag `latest`.

- [ ] **Step 4: Inspect manifest remote**

Run:

```powershell
docker buildx imagetools inspect minh333/parking-camera-device:latest
docker buildx imagetools inspect minh333/parking-postgres-sink:latest
```

Expected: cả hai registry manifest tồn tại và có platform `linux/amd64`.

### Task 4: Force recreate và xác minh pipeline

**Files:**
- No source file changes.

- [ ] **Step 1: Pull và recreate từ project root**

Run:

```powershell
docker compose --project-directory '..\..' --env-file '..\..\.env' -f '..\..\docker-compose.yml' pull
docker compose --project-directory '..\..' --env-file '..\..\.env' -f '..\..\docker-compose.yml' up -d --force-recreate
```

Expected: bốn container được recreate bằng tag mới.

- [ ] **Step 2: Xác nhận image ID của container**

Run:

```powershell
docker inspect parking-camera-device parking-postgres-sink --format '{{.Name}} {{.Image}}'
docker image inspect minh333/parking-camera-device:latest minh333/parking-postgres-sink:latest --format '{{.Id}}'
```

Expected: container image ID trùng với local `latest`.

- [ ] **Step 3: Chờ prediction thành công theo điều kiện**

Run:

```powershell
$deadline = (Get-Date).AddSeconds(60)
$predictionSeen = $false
do {
    $logs = docker logs --since 90s parking-prediction-service 2>&1
    if ($logs -match 'inserted=True') {
        $predictionSeen = $true
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if (-not $predictionSeen) {
    docker compose --project-directory '..\..' --env-file '..\..\.env' -f '..\..\docker-compose.yml' ps
    docker logs --tail 100 parking-camera-device
    docker logs --tail 100 parking-prediction-service
    throw "No successful prediction observed within 60 seconds"
}
```

Expected: thấy `inserted=True` trong tối đa 60 giây.

- [ ] **Step 4: Xác nhận lỗi cũ không tái diễn**

Run:

```powershell
$predictionLogs = docker logs --since 90s parking-prediction-service 2>&1
if ($predictionLogs -match "Prediction failed.*'source_event_id'") {
    throw "source_event_id error is still present"
}
$predictionLogs | Select-String -Pattern 'connected|inserted=True|failed'
```

Expected: có log connect/prediction thành công, không có lỗi thiếu field.

- [ ] **Step 5: Xác nhận database có prediction mới**

Run:

```powershell
@'
from dotenv import load_dotenv
import os
import psycopg2

load_dotenv(r"..\..\.env")
with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_event_id, model_version, occupied_count,
                   predicted_seconds_to_full, predicted_fill_at
            FROM parking_fill_predictions
            ORDER BY prediction_id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row is None:
            raise SystemExit("parking_fill_predictions is empty")
        print(
            f"source_event_id={row[0]} model={row[1]} "
            f"occupied={row[2]}/60 seconds_to_full={row[3]} "
            f"predicted_fill_at={row[4]}"
        )
'@ | python -
```

Expected: in một prediction có `source_event_id`, model version và ETA hợp lệ.

- [ ] **Step 6: Final verification**

Run:

```powershell
python -m pytest -q
docker compose --env-file '..\..\.env' config --quiet
git diff --check
git status --short
```

Expected: `67 passed`, Compose exit `0`, không có whitespace error và worktree sạch.
