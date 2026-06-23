# Parking Digital Twin MQTT PostgreSQL Pipeline

Du an nay mo phong luong du lieu bai do xe theo chuoi thoi gian:

```text
camera-device -> MQTT broker -> postgres-sink -> PostgreSQL/Neon
```

`camera-device` doc dataset YOLO parking frames, tao payload theo tung frame, day vao MQTT. `postgres-sink` lang nghe MQTT va ghi lich su trang thai slot vao PostgreSQL theo SCD Type 2.

## Kien truc

```text
.
|-- camera_device/
|   |-- Dockerfile
|   |-- camera_to_mqtt.py
|   `-- requirements.txt
|-- mqtt_broker/
|   |-- Dockerfile
|   `-- mosquitto.conf
|-- postgres_sink/
|   |-- Dockerfile
|   |-- postgres_sink.py
|   `-- requirements.txt
|-- stream_clean_to_json.py
|-- docker-compose.yml
|-- output_template.json
`-- test_stream_clean_to_json.py
```

## Du lieu dau vao

Camera image dang doc dataset trong container tai:

```text
/app/data/content/dataset
```

Neu dung image da build san tu Docker Hub, dataset da nam trong image `minh333/parking-camera-device:latest`.

Neu muon build lai image camera tren may local, can co folder:

```text
data_clean/
  train/
    images/
    labels/
  valid/
    images/
    labels/
  test/
    images/
    labels/
```

Folder `data_clean/` bi ignore khi push GitHub de tranh day dataset anh/label nang len repo.

## Format payload

Moi record slot co dang:

```json
{
  "frame_id": 1,
  "id": "A01",
  "occupied": 1,
  "timestamp": 1634567890
}
```

Quy tac:

```text
frame_id  = so thu tu frame
id        = slot id theo hang/cot, vi du A01, A02, B01
occupied  = class YOLO, 1 la occupied, 0 la empty
timestamp = Unix timestamp gia lap
```

Mac dinh moi frame cach nhau 1 giay:

```text
timestamp = start_timestamp + (frame_id - 1) * frame_interval_seconds
```

Trong Docker Compose:

```text
CAMERA_FRAME_INTERVAL_SECONDS=1
CAMERA_PUBLISH_INTERVAL=1
```

`CAMERA_FRAME_INTERVAL_SECONDS` la khoang cach thoi gian gia lap trong payload. `CAMERA_PUBLISH_INTERVAL` la toc do publish MQTT.

## PostgreSQL schema

Sink tao bang:

```text
public.parking_slot_history
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS parking_slot_history (
  unique_id TEXT PRIMARY KEY,
  frame_id INTEGER NOT NULL,
  id TEXT NOT NULL,
  occupied INTEGER NOT NULL,
  "timestamp" TIMESTAMP(6) NOT NULL,
  startdate TIMESTAMP(6) NOT NULL,
  enddate TIMESTAMP(6) NULL,
  status TEXT NOT NULL
);
```

SCD Type 2:

```text
unique_id = sha256("<frame_id>:<id>")
SCD2 key  = id
```

Neu slot dang active va `occupied` thay doi, row cu duoc dong bang:

```text
enddate = timestamp moi
status  = inactive
```

Sau do insert row moi voi:

```text
status = active
```

Neu `occupied` khong doi thi sink bo qua record do de tranh ghi trung lich su.

## Cau hinh moi truong

Tao file `.env` tu template:

```powershell
copy .env.example .env
```

Sua:

```env
DATABASE_URL="postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
```

Khong commit `.env` len GitHub.

## Chay bang Docker Compose

Chay pipeline:

```powershell
docker compose up
```

Chay nen:

```powershell
docker compose up -d
```

Xem log:

```powershell
docker compose logs -f
```

Dung pipeline:

```powershell
docker compose down
```

## Build local images

Mac dinh `docker-compose.yml` dung image da publish:

```text
minh333/parking-mqtt-broker:latest
minh333/parking-camera-device:latest
minh333/parking-postgres-sink:latest
```

Neu muon build local, bo comment cac block `build:` trong `docker-compose.yml`, dam bao co folder `data_clean/`, roi chay:

```powershell
docker compose up --build
```

Build va push image:

```powershell
.\build_image.sh
```

## Kiem tra database

Trong DBeaver hoac SQL client, chay:

```sql
SELECT frame_id, id, occupied, "timestamp", startdate, enddate, status
FROM public.parking_slot_history
ORDER BY startdate DESC
LIMIT 50;
```

Dem active/inactive:

```sql
SELECT status, count(*)
FROM public.parking_slot_history
GROUP BY status;
```

Kiem tra kieu timestamp:

```sql
SELECT column_name, data_type, datetime_precision
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'parking_slot_history'
  AND column_name IN ('timestamp', 'startdate', 'enddate');
```

Ky vong:

```text
timestamp without time zone, precision 6
```

## Chay test

```powershell
python -m pytest -q
```

## Luu y khi push GitHub

Khong push cac file/folder sau:

```text
.env
data/
data_clean/
__pycache__/
.pytest_cache/
*.pyc
```

Neu da tung commit secret nhu `DATABASE_URL`, hay rotate password/API key truoc khi public repo.
