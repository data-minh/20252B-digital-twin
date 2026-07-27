# Thiết kế phát hành đồng bộ camera schema và prediction pipeline

## Mục tiêu

Phát hành lại `camera-device` và `postgres-sink` để toàn bộ pipeline dùng cùng
MQTT message schema có `source_event_id`, đồng thời loại bỏ việc hai process
MJPEG cùng chiếm cổng `8081`.

Kết quả cuối cùng phải cho phép người dùng chạy:

```powershell
docker compose pull
docker compose up -d --force-recreate
```

và nhận prediction realtime trong `parking_fill_predictions` mà không còn lỗi:

```text
Prediction failed on topic=parking/frames: 'source_event_id'
```

## Nguyên nhân hiện tại

Docker Hub đang có các image không đồng bộ:

- `minh333/parking-camera-device:latest` dùng schema cũ, không phát
  `source_event_id`.
- `minh333/parking-postgres-sink:latest` dùng schema cũ.
- `minh333/parking-prediction-service:latest` dùng schema mới và bắt buộc
  `source_event_id`.

Source camera hiện tại đã tạo `source_event_id`, nhưng `entrypoint.sh` khởi động:

1. `camera_to_mqtt.py`, vốn đã tự mở MJPEG server.
2. `mjpeg_server.py`, mở thêm một HTTP server trên cùng cổng `8081`.

Hai process này có thể tranh cổng và làm camera container thoát.

## Phương án được chọn

Camera chạy một process tích hợp duy nhất:

```text
camera_to_mqtt.py
```

Process này chịu trách nhiệm:

- Đọc dataset.
- Cập nhật `latest_frame` cho MJPEG.
- Tạo MQTT frame có `source_event_id`.
- Giữ timestamp tăng đơn điệu qua các dataset cycle.
- Mã hóa JSON bằng AES-GCM.
- Publish lên `parking/frames`.

`entrypoint.sh` không chạy `mjpeg_server.py` độc lập. Nó dùng `exec` để
`camera_to_mqtt.py` trở thành process chính của container và nhận trực tiếp
signal stop/restart từ Docker.

## Thay đổi code

### `camera_device/entrypoint.sh`

Giữ việc:

- Lấy `CAMERA_START_TIMESTAMP` hoặc wall-clock time.
- Chuẩn bị encryption key file.
- Bật `--encrypt-payload`.

Thay phần quản lý hai background process bằng một lệnh:

```sh
exec python camera_to_mqtt.py \
  --start-timestamp "$START_TS" \
  --encrypt-payload \
  --encryption-key-file "$KEY_FILE"
```

### Không thay đổi

- Không thay prediction algorithm.
- Không thay model artifact.
- Không thay PostgreSQL schema.
- Không thay MQTT topic hoặc credential.
- Không thay cách tính `source_event_id`.
- Không thay rolling features.

## Kiểm thử

Thêm regression test đọc `camera_device/entrypoint.sh` và xác nhận:

- Có `exec python camera_to_mqtt.py`.
- Có các flag timestamp và encryption.
- Không có lệnh `python mjpeg_server.py`.
- Không còn logic quản lý hai PID.

Chạy toàn bộ test suite để đảm bảo camera schema, SCD2 và ML vẫn hoạt động.

## Build và kiểm tra image

Build:

```powershell
docker compose build camera-device postgres-sink
```

Kiểm tra camera image:

- CMD là `/app/entrypoint.sh`.
- `/app/camera_to_mqtt.py` có `source_event_id`.
- Entry point không chạy standalone MJPEG process.

Kiểm tra sink image:

- `/app/postgres_sink.py` có `source_event_id`.
- Identity lịch sử dựa trên `source_event_id` và slot.

Chạy smoke test camera container với broker, xác nhận container còn hoạt động,
MJPEG health endpoint trả thành công và camera log có frame publish.

## Push Docker Hub

Push đúng hai tag:

```powershell
docker push minh333/parking-camera-device:latest
docker push minh333/parking-postgres-sink:latest
```

Sau push, dùng registry inspection để xác nhận digest remote trùng image local.

Không push lại prediction image nếu digest hiện tại vẫn là bản đã kiểm tra có
model `parking-fill-eta-v1` và hỗ trợ schema mới.

## Triển khai local

Pull và recreate:

```powershell
docker compose pull
docker compose up -d --force-recreate
```

Kiểm tra:

```powershell
docker compose ps
docker compose logs --since 2m camera-device prediction-service
```

Điều kiện đạt:

- Bốn container đang chạy.
- Camera log có `source_event_id` hoặc frame publish từ code mới.
- Không còn `Prediction failed ... 'source_event_id'`.
- Prediction log có `inserted=True`.
- Database có dòng mới trong `parking_fill_predictions`.

## Xử lý lỗi triển khai

- Nếu build thất bại, không push image.
- Nếu image inspection thiếu `source_event_id`, không push image.
- Nếu một push thất bại, không force recreate pipeline bằng bộ image không đồng
  bộ; hoàn tất push rồi mới triển khai.
- Nếu prediction container thoát do database chưa sẵn sàng, kiểm tra
  `DATABASE_URL`, sau đó khởi động lại service.
- Nếu smoke test phát hiện lỗi khác, dừng triển khai và chẩn đoán nguyên nhân
  trước khi thay đổi thêm.

## Bảo mật

Quy trình này giữ nguyên encryption key demo hiện có để không mở rộng phạm vi.
Key `0123456789abcdef` không phù hợp production. Sau khi pipeline chạy đúng,
nên chuyển key sang Docker secret hoặc secret manager trong một thay đổi riêng.
