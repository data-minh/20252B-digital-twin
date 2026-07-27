# Comprehensive README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay thế README hiện tại bằng tài liệu tiếng Việt đầy đủ, giúp người mới chạy pipeline và giúp developer hiểu, train, kiểm thử, build và xử lý lỗi hệ thống.

**Architecture:** Giữ một `README.md` duy nhất làm điểm bắt đầu, đặt Quick Start trước nội dung chuyên sâu và liên kết sang tài liệu trong `docs/` khi cần. Tất cả tên service, biến môi trường, module CLI, bảng database và hành vi runtime được đối chiếu trực tiếp với source code và Docker Compose hiện tại.

**Tech Stack:** Markdown, PowerShell, Docker Compose, Python 3.12, MQTT/Mosquitto, PostgreSQL, scikit-learn.

---

### Task 1: Lập bản đồ nội dung từ source of truth

**Files:**
- Read: `docker-compose.yml`
- Read: `.env.example`
- Read: `camera_device/camera_to_mqtt.py`
- Read: `camera_device/entrypoint.sh`
- Read: `postgres_sink/postgres_sink.py`
- Read: `ml_service/simulation_repository.py`
- Read: `ml_service/materialize_training.py`
- Read: `ml_service/train.py`
- Read: `ml_service/prediction_service.py`
- Read: `ml_service/schema.py`
- Read: `ml_service/slots.py`
- Read: `ml_service/Dockerfile`
- Read: `build_image.sh`

- [ ] **Step 1: Xác nhận service và Compose**

Run:

```powershell
docker compose --env-file ..\..\.env config --services
docker compose --env-file ..\..\.env config --quiet
```

Expected:

```text
mqtt-broker
postgres-sink
camera-device
prediction-service
```

Lệnh thứ hai thoát với exit code `0`.

- [ ] **Step 2: Xác nhận các CLI được dùng trong README**

Run:

```powershell
python -m ml_service.simulation_repository --help
python -m ml_service.materialize_training --help
python -m ml_service.train --help
python -m ml_service.prediction_service --help
```

Expected: cả bốn lệnh thoát với exit code `0`; help hiển thị lần lượt `--days`, `--simulation-run-id`, `--model-version`, `--model-dir`.

- [ ] **Step 3: Xác nhận model artifact**

Run:

```powershell
Get-ChildItem models\parking_fill_eta | Select-Object Name,Length
python -c "from ml_service.predictor import Predictor; print(Predictor.load('models/parking_fill_eta').model_version)"
```

Expected: có `model.joblib`, `metadata.json`, `feature_schema.json`; model version là `parking-fill-eta-v1`.

### Task 2: Viết lại README theo luồng người mới đến developer

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Thay phần mở đầu và kiến trúc**

Viết các phần:

```markdown
# Parking Digital Twin: MQTT, PostgreSQL và dự đoán thời gian đầy bãi

## Tổng quan
## Tính năng chính
## Kiến trúc hệ thống
## Vai trò của từng container
## Cấu trúc thư mục
```

Sơ đồ kiến trúc phải thể hiện một message MQTT được gửi tới cả `postgres-sink` và `prediction-service`. Nêu sức chứa 60 slot theo năm hàng A–E và dự đoán bị chặn trong 0–10800 giây.

- [ ] **Step 2: Viết Quick Start**

Viết các phần:

```markdown
## Yêu cầu hệ thống
## Quick Start
## Điều gì xảy ra khi chạy Docker Compose
## Cấu hình môi trường
```

Quick Start phải chứa chính xác các lệnh:

```powershell
Copy-Item .env.example .env
docker compose --env-file .env build
docker compose --env-file .env up -d
docker compose ps
docker compose logs -f camera-device postgres-sink prediction-service
```

Giải thích `docker compose up` không train lại model; `up --build` chỉ build image bằng artifact hiện có. Ghi chú dùng `host.docker.internal` thay `localhost` khi PostgreSQL chạy trên Windows host.

- [ ] **Step 3: Mô tả dữ liệu, MQTT và SCD2**

Viết các phần:

```markdown
## Dataset và camera
## Format message MQTT
## Xác thực và mã hóa MQTT
## Lưu lịch sử PostgreSQL theo SCD Type 2
```

Message mẫu phải có các trường:

```json
{
  "source_event_id": "camera-session:1:0001",
  "split": "train",
  "frame_id": 1,
  "source_frame_id": "0001",
  "image": "data/content/dataset/train/images/0001.jpg",
  "payload": [
    {
      "frame_id": 1,
      "id": "A01",
      "occupied": 1,
      "timestamp": 1785174061
    }
  ]
}
```

Nêu `unique_id = sha256("<source_event_id>:<slot_id>")`, không dùng `frame_id` làm identity. Mô tả camera entrypoint mã hóa AES-GCM và hai subscriber giải mã bằng shared key.

- [ ] **Step 4: Mô tả prediction realtime và database**

Viết các phần:

```markdown
## Dự đoán realtime
## Các bảng database
## Truy vấn kiểm tra
```

Giải thích prediction service:

1. Load model từ `/app/models/parking_fill_eta`.
2. Nhận và giải mã frame.
3. Cập nhật trạng thái 60 slot.
4. Tính rolling features dùng chung với training.
5. Dự đoán và clamp về 0–10800 giây.
6. Insert idempotent theo `(source_event_id, model_version)`.

Đưa SQL kiểm tra `parking_slot_history`, trạng thái active, `parking_fill_predictions`, model registry và dữ liệu simulation/training.

- [ ] **Step 5: Viết workflow train model**

Viết các phần:

```markdown
## Model artifact
## Tạo dữ liệu giả lập và train lại model
```

Workflow PowerShell phải là:

```powershell
python -m pip install -r ml_service/requirements.txt
$simulationRunId = [guid]::NewGuid().ToString()
python -m ml_service.simulation_repository --days 30 --seed 20260727 --run-id $simulationRunId
python -m ml_service.materialize_training --simulation-run-id $simulationRunId
python -m ml_service.train --simulation-run-id $simulationRunId --model-version parking-fill-eta-v2 --sample-stride 3 --max-iter 150 --overwrite
docker compose build prediction-service
docker compose up -d --force-recreate prediction-service
```

Giải thích simulator tạo target nhỏ hơn hoặc bằng 3 giờ, `--sample-stride 3` giảm thời gian train và `--overwrite` thay artifact hiện tại.

- [ ] **Step 6: Viết phần vận hành và bảo trì**

Viết các phần:

```markdown
## Build và push Docker image
## MJPEG stream
## Chạy test
## Lệnh vận hành thường dùng
## Troubleshooting
## Bảo mật và Git
## Tài liệu liên quan
```

Phần Docker Hub phải có:

```powershell
docker compose build prediction-service
docker push minh333/parking-prediction-service:latest
docker pull minh333/parking-prediction-service:latest
```

Troubleshooting phải bao phủ: Docker daemon chưa chạy, `DATABASE_URL` sai, prediction container thoát khi DB chưa sẵn sàng, MQTT authentication failure, image cũ chưa rebuild/pull, không có prediction, model artifact thiếu và kiểm tra log theo từng service.

- [ ] **Step 7: Commit README**

Run:

```powershell
git add README.md
git commit -m "docs: write comprehensive project readme"
```

Expected: commit chỉ chứa `README.md`.

### Task 3: Kiểm chứng README

**Files:**
- Verify: `README.md`

- [ ] **Step 1: Quét thông tin lỗi thời và placeholder**

Run:

```powershell
$unfinishedMarkers = @('T' + 'BD', 'T' + 'ODO', 'bo comment')
foreach ($marker in $unfinishedMarkers) { rg -n -F $marker README.md }
rg -n "sha256\\(\"<frame_id>|unique_id = sha256\\(\"<frame_id>" README.md
```

Expected: không có kết quả.

- [ ] **Step 2: Xác nhận các nội dung bắt buộc**

Run:

```powershell
rg -n "Quick Start|docker compose up|không.*train|source_event_id|AES-GCM|SCD Type 2|parking_fill_predictions|10800|simulation_repository|materialize_training|host.docker.internal|Troubleshooting" README.md
```

Expected: mọi chủ đề đều xuất hiện trong README.

- [ ] **Step 3: Xác nhận Compose và test suite**

Run:

```powershell
docker compose --env-file ..\..\.env config --quiet
python -m pytest -q
```

Expected: Compose thoát với exit code `0`; pytest báo `66 passed`.

- [ ] **Step 4: Kiểm tra Markdown diff**

Run:

```powershell
git diff HEAD^ --check
git show --stat --oneline HEAD
git status --short
```

Expected: không có lỗi whitespace; commit README xuất hiện; worktree sạch.

- [ ] **Step 5: Commit điều chỉnh kiểm chứng nếu cần**

Chỉ khi kiểm chứng phát hiện lỗi tài liệu, sửa đúng lỗi đó rồi chạy:

```powershell
git add README.md
git commit -m "docs: correct readme verification details"
```

Expected: commit chỉ chứa `README.md`; sau đó chạy lại toàn bộ Task 3.
