# Parking Digital Twin: MQTT, PostgreSQL và dự đoán thời gian đầy bãi

## Tổng quan

Dự án mô phỏng một hệ thống digital twin cho bãi đỗ xe. Camera giả lập đọc
dataset ảnh và nhãn YOLO, phát trạng thái chỗ đỗ qua MQTT, lưu lịch sử thay đổi
vào PostgreSQL và dự đoán theo thời gian thực thời điểm toàn bãi đạt 100% công
suất.

Sức chứa được chuẩn hóa cố định là **60 vị trí**:

- A01–A14: 14 vị trí.
- B01–B13: 13 vị trí.
- C01–C12: 12 vị trí.
- D01–D10: 10 vị trí.
- E01–E11: 11 vị trí.

Model trả về số giây còn lại cho đến khi bãi đạt 60/60. Kết quả luôn được giới
hạn trong khoảng **0–10800 giây**, tương đương tối đa 3 giờ.

## Tính năng chính

- Phát dataset parking frame lên MQTT theo thời gian thực.
- Mỗi lần phát có `source_event_id` duy nhất, kể cả khi camera lặp lại dataset.
- Timestamp tiếp tục tăng khi camera quay lại frame đầu tiên.
- Xác thực MQTT bằng tài khoản publisher/subscriber và ACL theo topic.
- Mã hóa payload camera bằng AES-GCM.
- Lưu lịch sử trạng thái slot vào PostgreSQL theo SCD Type 2.
- Sinh 30 ngày dữ liệu giả lập phục vụ huấn luyện.
- Materialize rolling features và target vào một bảng riêng.
- Train `HistGradientBoostingRegressor` ngoài container inference.
- Load model đã train sẵn và dự đoán với mỗi frame MQTT mới.
- Lưu kết quả realtime vào `parking_fill_predictions`.
- Cung cấp MJPEG stream để quan sát ảnh camera.
- Build và chạy toàn bộ hệ thống bằng Docker Compose.

## Kiến trúc hệ thống

```text
                                      ┌──────────────────────────────┐
                                      │ PostgreSQL / Neon            │
                                      │                              │
┌───────────────┐   AES-GCM + MQTT    │ parking_slot_history         │
│ camera-device │ ─────────────────┐  │ parking_fill_predictions     │
│               │                  │  │ các bảng simulation/training │
│ YOLO dataset  │                  │  └──────────────▲───────────────┘
│ MJPEG :8081   │                  │                 │
└───────────────┘                  ▼                 │
                           ┌───────────────┐          │
                           │ mqtt-broker   │          │
                           │ topic:        │          │
                           │ parking/frames│          │
                           └───────┬───────┘          │
                                   │                  │
                    ┌──────────────┴──────────────┐   │
                    ▼                             ▼   │
           ┌─────────────────┐          ┌────────────────────┐
           │ postgres-sink   │          │ prediction-service │
           │ giải mã + SCD2  │          │ giải mã + features │
           └────────┬────────┘          │ + model inference  │
                    │                   └──────────┬─────────┘
                    └──────────────────────────────┘
```

`postgres-sink` và `prediction-service` là hai subscriber độc lập. Cả hai đều
nhận cùng một message từ `parking/frames`; chúng không tranh nhau message.

## Vai trò của từng container

| Service | Image | Trách nhiệm |
|---|---|---|
| `mqtt-broker` | `minh333/parking-mqtt-broker:latest` | Tạo user, password, ACL và chuyển tiếp message MQTT |
| `camera-device` | `minh333/parking-camera-device:latest` | Đọc dataset, tạo frame message, mã hóa AES-GCM, publish MQTT và phục vụ MJPEG |
| `postgres-sink` | `minh333/parking-postgres-sink:latest` | Subscribe MQTT, giải mã và ghi lịch sử SCD2 vào PostgreSQL |
| `prediction-service` | `minh333/parking-prediction-service:latest` | Load model, tạo features realtime, dự đoán và ghi kết quả vào PostgreSQL |

PostgreSQL không nằm trong `docker-compose.yml`. Hệ thống kết nối tới database
bên ngoài thông qua `DATABASE_URL`, ví dụ PostgreSQL local, Neon hoặc một dịch
vụ PostgreSQL khác.

## Cấu trúc thư mục

```text
.
├── camera_device/
│   ├── camera_to_mqtt.py       # Publisher MQTT và đồng bộ frame MJPEG
│   ├── mjpeg_server.py         # HTTP MJPEG server
│   ├── entrypoint.sh
│   └── Dockerfile
├── mqtt_broker/
│   ├── entrypoint.sh           # Tạo password file và ACL
│   ├── mosquitto.conf
│   └── Dockerfile
├── postgres_sink/
│   ├── postgres_sink.py        # MQTT subscriber và SCD Type 2
│   └── Dockerfile
├── ml_service/
│   ├── simulator.py            # Sinh timeline bãi xe giả lập
│   ├── simulation_repository.py
│   ├── materialize_training.py
│   ├── features.py             # Feature engineering dùng chung
│   ├── train.py
│   ├── predictor.py
│   ├── prediction_service.py
│   ├── schema.py
│   ├── slots.py
│   └── Dockerfile
├── models/parking_fill_eta/
│   ├── model.joblib
│   ├── metadata.json
│   └── feature_schema.json
├── tests/
├── data_clean/                 # Dataset được đóng gói vào camera image
├── docker-compose.yml
├── .env.example
└── README.md
```

## Yêu cầu hệ thống

Để chạy pipeline bằng container:

- Docker Desktop hoặc Docker Engine có Compose v2.
- Một PostgreSQL database truy cập được từ container.
- Các cổng local chưa bị chiếm:
  - `1883`: MQTT.
  - `8081`: MJPEG.

Để chạy test, tạo simulation hoặc train lại model:

- Python 3.12 được khuyến nghị.
- Dependency trong `ml_service/requirements.txt`.
- Kết nối PostgreSQL hợp lệ trong `.env`.

## Quick Start

### 1. Chuẩn bị `.env`

Trong PowerShell tại thư mục dự án:

```powershell
Copy-Item .env.example .env
```

Mở `.env` và thay các giá trị mẫu:

```env
DATABASE_URL="postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
MQTT_TOPIC="parking/frames"
MQTT_PUB_USERNAME="parking_pub"
MQTT_PUB_PASSWORD="change-me-pub"
MQTT_SUB_USERNAME="parking_sub"
MQTT_SUB_PASSWORD="change-me-sub"
CAMERA_STREAM_SECRET="change-me-camera-stream"
```

Nếu PostgreSQL chạy trực tiếp trên máy Windows, container không thể dùng
`localhost` để truy cập máy host. Sử dụng:

```env
DATABASE_URL="postgresql://USER:PASSWORD@host.docker.internal:5432/DATABASE"
```

### 2. Build image

```powershell
docker compose --env-file .env build
```

Dataset `data_clean/` phải tồn tại nếu build lại `camera-device`. Model artifact
phải tồn tại trong `models/parking_fill_eta/` nếu build lại
`prediction-service`.

### 3. Khởi động pipeline

```powershell
docker compose --env-file .env up -d
```

### 4. Kiểm tra container

```powershell
docker compose ps
```

Các service cần xuất hiện:

```text
mqtt-broker
postgres-sink
camera-device
prediction-service
```

### 5. Theo dõi log

```powershell
docker compose logs -f camera-device postgres-sink prediction-service
```

Log prediction bình thường có dạng:

```text
Prediction frame_id=1 inserted=True occupied=32/60
```

### 6. Kiểm tra prediction trong database

```sql
SELECT
    prediction_id,
    source_event_id,
    observed_at,
    occupied_count,
    capacity,
    predicted_seconds_to_full,
    predicted_fill_at,
    model_version,
    inference_ms
FROM parking_fill_predictions
ORDER BY prediction_id DESC
LIMIT 50;
```

## Điều gì xảy ra khi chạy Docker Compose

Khi chạy:

```powershell
docker compose up
```

Docker Compose thực hiện các bước sau:

1. Tạo network dùng chung cho các container.
2. Khởi động `mqtt-broker`; broker tạo password file và ACL từ `.env`.
3. Khởi động `postgres-sink`; sink kết nối database, tạo
   `parking_slot_history` nếu cần và subscribe MQTT.
4. Khởi động `prediction-service`; service tạo schema ML, load model và
   subscribe MQTT.
5. Khởi động `camera-device`; camera chờ theo `CAMERA_START_DELAY`, sau đó phát
   frame lên MQTT.
6. Broker chuyển mỗi frame cho cả sink và prediction service.

`depends_on` chỉ chờ container phụ thuộc bắt đầu, không đảm bảo PostgreSQL bên
ngoài đã sẵn sàng. `postgres-sink` có cơ chế retry database; nếu
`prediction-service` thoát do database chưa sẵn sàng, hãy khởi động lại service
sau khi database hoạt động.

### Compose có train lại model không?

**Không.** `docker compose up` chỉ load model đã có trong image.

```text
/app/models/parking_fill_eta
```

Ngay cả lệnh:

```powershell
docker compose up --build
```

cũng chỉ build lại image bằng artifact hiện có trong
`models/parking_fill_eta/`; lệnh này không tạo simulation và không train model.

Muốn train lại, phải chủ động chạy các module Python trong phần
[Tạo dữ liệu giả lập và train lại model](#tạo-dữ-liệu-giả-lập-và-train-lại-model).

## Cấu hình môi trường

| Biến | Service sử dụng | Ý nghĩa |
|---|---|---|
| `DATABASE_URL` | sink, prediction, các CLI ML | PostgreSQL connection string |
| `MQTT_TOPIC` | broker, camera, sink, prediction | Topic frame, mặc định `parking/frames` |
| `MQTT_PUB_USERNAME` | broker, camera | Tài khoản chỉ có quyền publish |
| `MQTT_PUB_PASSWORD` | broker, camera | Mật khẩu publisher |
| `MQTT_SUB_USERNAME` | broker, sink, prediction | Tài khoản chỉ có quyền subscribe |
| `MQTT_SUB_PASSWORD` | broker, sink, prediction | Mật khẩu subscriber |
| `CAMERA_STREAM_SECRET` | camera | Shared secret bảo vệ MJPEG |

Các biến runtime quan trọng đã được đặt trong `docker-compose.yml`:

| Biến | Giá trị hiện tại | Ý nghĩa |
|---|---:|---|
| `CAMERA_PUBLISH_INTERVAL` | `1` | Khoảng nghỉ thực giữa hai lần publish |
| `CAMERA_FRAME_INTERVAL_SECONDS` | `1` | Khoảng tăng timestamp giữa hai frame |
| `CAMERA_LOOP_DATASET` | `true` | Đọc lại dataset sau frame cuối |
| `CAMERA_START_DELAY` | `5` | Chờ trước khi camera bắt đầu |
| `CAMERA_MJPEG_PORT` | `8081` | Cổng HTTP MJPEG |
| `MODEL_DIR` | `/app/models/parking_fill_eta` | Artifact bên trong inference image |

## Dataset và camera

Image camera đọc dataset tại:

```text
/app/data/content/dataset
```

Khi build local, `camera_device/Dockerfile` copy thư mục `data_clean/` vào đường
dẫn trên. Cấu trúc mong đợi:

```text
data_clean/
├── train/
│   ├── images/
│   └── labels/
├── valid/
│   ├── images/
│   └── labels/
└── test/
    ├── images/
    └── labels/
```

Mỗi ảnh phải có label `.txt` tương ứng. Slot được sắp theo vị trí bounding box
và ánh xạ thành A01, A02, ... theo hàng/cột.

Với cấu hình hiện tại:

- Camera publish khoảng một frame mỗi giây.
- Sau khi đọc hết dataset, camera quay lại frame đầu.
- `frame_id` và `source_frame_id` có thể lặp theo ảnh gốc.
- `source_event_id` không lặp vì chứa camera session và cycle.
- Timestamp tiếp tục tăng theo tổng số frame đã publish, không quay về mốc cũ.

## Format message MQTT

Message trước khi mã hóa có dạng:

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

Ý nghĩa:

| Trường | Ý nghĩa |
|---|---|
| `source_event_id` | Identity duy nhất của lần phát: session, cycle và source frame |
| `split` | Nguồn `train`, `valid` hoặc `test` |
| `frame_id` | Số frame sau khi chuẩn hóa |
| `source_frame_id` | Tên frame gốc trong dataset |
| `image` | Đường dẫn ảnh trong camera container |
| `payload` | Danh sách trạng thái slot của frame |
| `occupied` | `1` là có xe, `0` là trống |
| `timestamp` | Unix timestamp theo giây |

## Xác thực và mã hóa MQTT

Broker tạo hai loại tài khoản:

- Publisher chỉ có quyền ghi lên `MQTT_TOPIC`.
- Subscriber chỉ có quyền đọc từ `MQTT_TOPIC`.

Camera entrypoint mã hóa toàn bộ JSON message bằng AES-GCM trước khi publish.
Payload MQTT thực tế là Base64 của:

```text
nonce (12 bytes) + authentication tag (16 bytes) + ciphertext
```

`postgres-sink` và `prediction-service` dùng cùng shared key để giải mã. Giá trị
key hiện được cấu hình phục vụ demo trong Compose/entrypoint. Khi triển khai
thật, không nên giữ key mặc định trong source code; hãy đưa key qua secret
manager hoặc Docker secret và rotate key đã từng công khai.

Broker còn có cấu hình listener TLS `8883`, nhưng Compose hiện chỉ publish cổng
MQTT `1883` ra host. Muốn dùng MQTTS cần cấu hình certificate, port mapping và
các biến TLS tương ứng cho từng client.

## Lưu lịch sử PostgreSQL theo SCD Type 2

`postgres-sink` tạo bảng:

```text
parking_slot_history
```

Các cột chính:

```sql
CREATE TABLE IF NOT EXISTS parking_slot_history (
    unique_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    frame_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    occupied INTEGER NOT NULL,
    "timestamp" TIMESTAMP(6) NOT NULL,
    startdate TIMESTAMP(6) NOT NULL,
    enddate TIMESTAMP(6) NULL,
    status TEXT NOT NULL
);
```

Identity của một record đến từ event và slot:

```text
unique_id = sha256("<source_event_id>:<slot_id>")
```

`frame_id` không được dùng làm identity vì frame ID lặp khi camera lặp dataset.

Quy tắc SCD Type 2:

1. Nếu slot chưa có record active, insert một record `active`.
2. Nếu `occupied` không đổi, bỏ qua để tránh tạo lịch sử dư thừa.
3. Nếu `occupied` thay đổi, đóng record cũ:

```text
enddate = startdate của record mới
status  = inactive
```

4. Insert version mới với `status = active`.

PostgreSQL lưu timestamp dạng `TIMESTAMP(6) WITHOUT TIME ZONE` sau khi chuyển
Unix timestamp sang múi giờ UTC+7.

## Dự đoán realtime

`prediction-service` không truy vấn lại toàn bộ lịch sử với mỗi frame. Service
duy trì state hiện tại của 60 slot trong bộ nhớ và thực hiện:

1. Load model, metadata và feature schema từ
   `/app/models/parking_fill_eta`.
2. Nhận và giải mã MQTT frame.
3. Kiểm tra trùng `(source_event_id, model_version)` trước inference.
4. Cập nhật các slot có trong payload; giữ state trước đó cho slot không xuất
   hiện trong frame.
5. Tính rolling features bằng cùng `RollingFeatureBuilder` được dùng lúc tạo dữ
   liệu train.
6. Chạy model và giới hạn prediction trong `0–10800` giây.
7. Tính `predicted_fill_at = observed_at + predicted_seconds_to_full`.
8. Insert vào `parking_fill_predictions`.

Mỗi frame tạo tối đa một prediction cho mỗi model version nhờ unique constraint:

```text
(source_event_id, model_version)
```

Model hiện tại:

| Thuộc tính | Giá trị |
|---|---|
| Version | `parking-fill-eta-v1` |
| Thuật toán | `HistGradientBoostingRegressor` |
| MAE test | khoảng 379,51 giây |
| Baseline MAE | khoảng 1497,86 giây |
| Test samples | 12942 |
| Tỷ lệ sai số trong 15 phút | khoảng 87,55% |

Đây là chỉ số trên dữ liệu giả lập của training run hiện tại, không phải cam kết
độ chính xác trên dữ liệu thực tế.

## Các bảng database

| Bảng | Nguồn tạo | Mục đích |
|---|---|---|
| `parking_slot_history` | `postgres-sink` | Lịch sử trạng thái camera theo SCD2 |
| `parking_slots` | ML schema | Danh mục 60 slot chuẩn |
| `parking_simulation_runs` | Simulator | Metadata mỗi lần simulation |
| `parking_simulated_slot_history` | Simulator | Lịch sử SCD2 giả lập |
| `parking_model_training_data` | Materializer | Features, target và dataset split |
| `parking_model_registry` | Trainer | Version, hash, metrics và parameters |
| `parking_fill_predictions` | Prediction service | Kết quả inference realtime |

Các module ML dùng PostgreSQL advisory transaction lock khi tạo schema để tránh
nhiều service cùng chạy DDL tại một thời điểm.

## Truy vấn kiểm tra

### Lịch sử mới nhất

```sql
SELECT
    source_event_id,
    frame_id,
    id AS slot_id,
    occupied,
    startdate,
    enddate,
    status
FROM parking_slot_history
ORDER BY startdate DESC
LIMIT 100;
```

### Trạng thái active hiện tại

```sql
SELECT
    id AS slot_id,
    occupied,
    startdate
FROM parking_slot_history
WHERE status = 'active'
ORDER BY id;
```

### Prediction mới nhất

```sql
SELECT
    observed_at,
    occupied_count,
    capacity,
    predicted_seconds_to_full,
    ROUND(predicted_seconds_to_full / 60.0, 2) AS minutes_to_full,
    ROUND(predicted_seconds_to_full / 3600.0, 2) AS hours_to_full,
    predicted_fill_at,
    model_version,
    inference_ms
FROM parking_fill_predictions
ORDER BY prediction_id DESC
LIMIT 50;
```

### Model registry

```sql
SELECT
    model_version,
    algorithm,
    training_run_id,
    metrics,
    parameters,
    trained_at
FROM parking_model_registry
ORDER BY trained_at DESC;
```

### Simulation run

```sql
SELECT
    simulation_run_id,
    seed,
    started_at,
    ended_at,
    transition_count,
    sample_count,
    excluded_count,
    created_at
FROM parking_simulation_runs
ORDER BY created_at DESC;
```

### Số lượng training row theo split

```sql
SELECT
    simulation_run_id,
    dataset_split,
    COUNT(*) AS samples,
    MIN(seconds_to_full) AS min_seconds,
    MAX(seconds_to_full) AS max_seconds
FROM parking_model_training_data
GROUP BY simulation_run_id, dataset_split
ORDER BY simulation_run_id, dataset_split;
```

## Model artifact

Model được train ngoài container và lưu tại:

```text
models/parking_fill_eta/
├── model.joblib
├── metadata.json
└── feature_schema.json
```

- `model.joblib`: estimator đã train.
- `metadata.json`: model version, training run, metrics, parameters và artifact
  hash.
- `feature_schema.json`: thứ tự feature bắt buộc khi inference.

`ml_service/Dockerfile` copy cả thư mục trên vào image:

```text
/app/models/parking_fill_eta
```

Predictor kiểm tra artifact hash và feature schema hash khi load. Nếu artifact
không đồng bộ, service dừng thay vì dự đoán bằng schema sai.

## Tạo dữ liệu giả lập và train lại model

Workflow này là thao tác chủ động và **không chạy khi `docker compose up`**.
Các lệnh sẽ đọc `DATABASE_URL` từ `.env` và ghi dữ liệu vào database đó.

### 1. Cài dependency

```powershell
python -m pip install -r ml_service/requirements.txt
```

### 2. Sinh 30 ngày dữ liệu giả lập

```powershell
$simulationRunId = [guid]::NewGuid().ToString()
python -m ml_service.simulation_repository --days 30 --seed 20260727 --run-id $simulationRunId
```

Simulator chạy với độ phân giải nội bộ một giây, tạo các giai đoạn xe đến/rời
bãi và chỉ giữ sample có lần đầy bãi tiếp theo trong tối đa 10800 giây. Kết quả
được lưu dưới dạng SCD2 trong `parking_simulated_slot_history`.

### 3. Materialize features và target

```powershell
python -m ml_service.materialize_training --simulation-run-id $simulationRunId
```

Các nhóm feature chính:

- Số chỗ có xe, chỗ trống và occupancy ratio.
- Lượt đến/rời trong 1, 5, 15, 30 và 60 phút.
- Occupancy slope trong các cửa sổ trên.
- Thời gian từ lượt đến/rời gần nhất.
- Chu kỳ giờ trong ngày và ngày trong tuần.
- Trạng thái one-hot của 60 slot.

Target:

```text
seconds_to_full = số giây từ observed_at đến lần tiếp theo bãi đạt 60/60
```

Timeline được chia theo thời gian thành `train`, `validation` và `test`, không
shuffle ngẫu nhiên giữa quá khứ và tương lai.

### 4. Train và thay model artifact

```powershell
python -m ml_service.train --simulation-run-id $simulationRunId --model-version parking-fill-eta-v2 --sample-stride 3 --max-iter 150 --overwrite
```

- `--sample-stride 3`: lấy mỗi sample thứ ba trên timeline để giảm thời gian
  train.
- `--max-iter 150`: số vòng boosting tối đa.
- `--overwrite`: cho phép thay ba artifact đang tồn tại.
- `--model-version`: phải giúp phân biệt model mới trong registry và prediction.

Trainer ghi metadata vào `parking_model_registry` và artifact vào
`models/parking_fill_eta/`.

### 5. Đưa model mới vào container

```powershell
docker compose build prediction-service
docker compose up -d --force-recreate prediction-service
```

Kiểm tra version model mới:

```powershell
docker run --rm --entrypoint python minh333/parking-prediction-service:latest -c "from ml_service.predictor import Predictor; print(Predictor.load('/app/models/parking_fill_eta').model_version)"
```

## Build và push Docker image

### Build toàn bộ

```powershell
docker compose --env-file .env build
```

### Build riêng prediction service

```powershell
docker compose build prediction-service
```

Tag được khai báo sẵn trong Compose:

```text
minh333/parking-prediction-service:latest
```

### Push prediction image lên Docker Hub

Đăng nhập trước:

```powershell
docker login
```

Sau đó:

```powershell
docker push minh333/parking-prediction-service:latest
```

### Dùng image trên máy khác

```powershell
docker pull minh333/parking-prediction-service:latest
docker compose up -d
```

Script `build_image.sh` build và push cả bốn image. Trên Windows, chạy script
trong Git Bash hoặc WSL:

```bash
./build_image.sh
```

## MJPEG stream

Camera public cổng `8081`.

Health check:

```text
http://localhost:8081/health
```

Nếu `CAMERA_STREAM_SECRET` có giá trị, mở stream bằng query token:

```text
http://localhost:8081/stream/mjpeg?token=<CAMERA_STREAM_SECRET>
```

Hoặc gửi header:

```text
X-API-Key: <CAMERA_STREAM_SECRET>
```

Nếu secret rỗng, stream không yêu cầu xác thực. Không đưa secret thật vào URL
được ghi log hoặc chia sẻ công khai.

## Chạy test

Cài dependency nếu máy chưa có:

```powershell
python -m pip install -r ml_service/requirements.txt
python -m pip install -r camera_device/requirements.txt
python -m pip install -r postgres_sink/requirements.txt
```

Chạy toàn bộ test:

```powershell
python -m pytest -q
```

Kiểm tra Compose:

```powershell
docker compose --env-file .env config --quiet
```

Kiểm tra model local:

```powershell
python -c "from ml_service.predictor import Predictor; print(Predictor.load('models/parking_fill_eta').model_version)"
```

## Lệnh vận hành thường dùng

### Chạy foreground

```powershell
docker compose up
```

Nhấn `Ctrl+C` để dừng các container đang attach.

### Chạy background

```powershell
docker compose up -d
```

### Xem trạng thái

```powershell
docker compose ps
```

### Xem toàn bộ log

```powershell
docker compose logs -f
```

### Xem log từng service

```powershell
docker compose logs -f mqtt-broker
docker compose logs -f camera-device
docker compose logs -f postgres-sink
docker compose logs -f prediction-service
```

### Khởi động lại prediction service

```powershell
docker compose restart prediction-service
```

### Build lại code local

```powershell
docker compose up -d --build
```

### Lấy image mới từ Docker Hub

```powershell
docker compose pull
docker compose up -d --force-recreate
```

### Dừng pipeline

```powershell
docker compose down
```

`docker compose down` xóa container và network Compose nhưng không xóa dữ liệu
trong PostgreSQL bên ngoài.

## Troubleshooting

### Docker daemon chưa chạy

Triệu chứng:

```text
Cannot connect to the Docker daemon
```

Cách xử lý:

1. Mở Docker Desktop.
2. Chờ Docker Engine ở trạng thái running.
3. Kiểm tra:

```powershell
docker version
docker compose version
```

### `DATABASE_URL` không kết nối được

Kiểm tra:

- Username, password, hostname, port và database name.
- `sslmode=require` nếu nhà cung cấp yêu cầu SSL.
- Dùng `host.docker.internal`, không dùng `localhost`, nếu DB chạy trên Windows
  host.
- Firewall và IP allowlist của dịch vụ database.

Xem log:

```powershell
docker compose logs postgres-sink
docker compose logs prediction-service
```

### Prediction container thoát khi khởi động

`prediction-service` tạo schema và load model trước khi bắt đầu MQTT loop. Nếu
database chưa sẵn sàng, container có thể thoát.

Sau khi sửa kết nối:

```powershell
docker compose up -d prediction-service
```

### MQTT báo `not authorised`

Đảm bảo bốn biến sau giống nhau giữa broker và client:

```text
MQTT_PUB_USERNAME
MQTT_PUB_PASSWORD
MQTT_SUB_USERNAME
MQTT_SUB_PASSWORD
```

Recreate container sau khi đổi credential:

```powershell
docker compose down
docker compose up -d
```

### Code đã sửa nhưng container vẫn chạy code cũ

`docker compose up` có thể dùng image local đã tồn tại. Build và recreate:

```powershell
docker compose build prediction-service
docker compose up -d --force-recreate prediction-service
```

Nếu muốn dùng bản mới trên Docker Hub:

```powershell
docker compose pull prediction-service
docker compose up -d --force-recreate prediction-service
```

### Không có dòng mới trong `parking_fill_predictions`

Kiểm tra theo thứ tự:

```powershell
docker compose ps
docker compose logs mqtt-broker
docker compose logs camera-device
docker compose logs prediction-service
```

Sau đó kiểm tra:

- Camera có log publish frame hay không.
- Prediction có kết nối và subscribe đúng `MQTT_TOPIC` hay không.
- Shared encryption key giữa camera và prediction có giống nhau không.
- Message có `source_event_id`, payload không rỗng và một timestamp thống nhất
  trong frame hay không.
- Model artifact có load thành công không.

### Thiếu model artifact

Image build cần ba file:

```text
models/parking_fill_eta/model.joblib
models/parking_fill_eta/metadata.json
models/parking_fill_eta/feature_schema.json
```

Nếu thiếu, train lại hoặc khôi phục artifact rồi:

```powershell
docker compose build prediction-service
```

### Không truy cập được MJPEG

Kiểm tra:

```powershell
docker compose ps camera-device
docker compose logs camera-device
```

Đảm bảo cổng `8081` chưa bị ứng dụng khác chiếm và token khớp
`CAMERA_STREAM_SECRET`.

## Bảo mật và Git

- Không commit `.env`.
- Không ghi credential thật vào README, issue, log hoặc ảnh chụp màn hình.
- Rotate ngay password/key đã từng được commit hoặc chia sẻ.
- Không dùng key AES demo trong production.
- Dùng secret manager hoặc Docker secret cho database password và encryption
  key.
- Chỉ mở cổng MQTT/MJPEG ra ngoài khi cần.
- Ưu tiên MQTTS/TLS khi truyền qua mạng không tin cậy.
- Giới hạn quyền database của application user.

Các đường dẫn đang được ignore:

```text
.env
data/
models/parking_fill_eta/model.joblib
models/parking_fill_eta/metadata.json
models/parking_fill_eta/feature_schema.json
__pycache__/
.pytest_cache/
*.pyc
```

Model artifact bị ignore khỏi Git nhưng vẫn được copy vào Docker build context.
Hãy lưu artifact trong model registry hoặc artifact storage phù hợp nếu làm việc
theo nhóm. `data_clean/` hiện đang được theo dõi trong repository để có thể build
camera image; nếu thay bằng dataset nhạy cảm hoặc dung lượng lớn, hãy chuyển nó
sang object storage và cập nhật quy trình build trước khi bỏ khỏi Git.

## Tài liệu liên quan

- `docs/superpowers/specs/2026-07-27-parking-fill-eta-design.md`: thiết kế
  pipeline dự đoán.
- `docs/superpowers/plans/2026-07-27-parking-fill-eta-implementation.md`: kế
  hoạch triển khai ML.
- `docs/mqtt_encryption_report.md`: ghi chú về MQTT encryption.
- `README_stream_clean_to_json.md`: xử lý dataset và JSON.
- `README_docker_mqtt_pipeline.md`: hướng dẫn pipeline Docker/MQTT cũ để tham
  khảo lịch sử.
