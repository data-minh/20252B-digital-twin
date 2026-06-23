# Docker MQTT PostgreSQL parking pipeline

Pipeline nay tach luong thanh 4 service Docker:

```text
camera-device -> MQTT broker -> postgres-sink -> PostgreSQL
```

## Cau truc

```text
docker-compose.yml
mqtt_broker/
  Dockerfile
  mosquitto.conf
camera_device/
  Dockerfile
  requirements.txt
  camera_to_mqtt.py
postgres_sink/
  Dockerfile
  requirements.txt
  postgres_sink.py
```

`camera-device` build image voi raw dataset trong:

```text
data/content/dataset
```

Khi chay, camera doc dataset, giu logic frame/timestamp hien tai, gan slot ID dang `A01`, `A02`, `B01`, roi publish len MQTT topic:

```text
parking/frames
```

`postgres-sink` subscribe topic nay va ghi lich su SCD Type 2 vao PostgreSQL.

## Bang PostgreSQL

Mac dinh sink tao bang:

```text
parking_slot_history
```

Schema:

```text
unique_id  text primary key
frame_id   integer
id         text
occupied   integer
timestamp  timestamptz
startdate  timestamptz
enddate    timestamptz null
status     text
```

Quy tac:

```text
unique_id = sha256("<frame_id>:<id>")
SCD2 key  = id
```

Neu slot dang active co `occupied` khac record moi, row cu se duoc dong bang `enddate = timestamp moi`, `status = inactive`, va row moi duoc insert voi `status = active`.

## Chay pipeline

Tai root folder:

```powershell
docker compose up --build
```

Neu muon chay nen:

```powershell
docker compose up --build -d
```

Xem log:

```powershell
docker compose logs -f
```

Dung pipeline:

```powershell
docker compose down
```

Xoa database volume neu muon chay lai tu dau:

```powershell
docker compose down -v
```

## Log mong doi

Camera:

```text
Camera published frame_id=1 source_frame_id=0001 slots=53 topic=parking/frames
```

Postgres sink:

```text
Postgres sink received frame_id=1 source_frame_id=0001 slots=53
```

## Kiem tra du lieu

Ket noi vao PostgreSQL container:

```powershell
docker exec -it parking-postgres psql -U parking -d parking
```

Truy van nhanh:

```sql
SELECT frame_id, id, occupied, startdate, enddate, status
FROM parking_slot_history
ORDER BY id, startdate
LIMIT 20;
```

Dem active row:

```sql
SELECT status, count(*)
FROM parking_slot_history
GROUP BY status;
```

## Tuy chinh nhanh

Sua trong `docker-compose.yml`:

```text
CAMERA_PUBLISH_INTERVAL = so giay giua 2 frame camera publish vao MQTT
CAMERA_FRAME_INTERVAL_SECONDS = so giay gia lap giua 2 frame trong timestamp payload
CAMERA_START_DELAY      = so giay camera doi sink subscribe truoc khi publish frame dau
POSTGRES_DB             = database name
POSTGRES_USER           = database user
POSTGRES_PASSWORD       = database password
```
