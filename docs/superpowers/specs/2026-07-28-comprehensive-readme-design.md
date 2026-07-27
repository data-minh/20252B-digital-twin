# Thiết kế README toàn diện cho Parking Digital Twin

## Mục tiêu

Viết lại `README.md` bằng tiếng Việt có dấu để hai nhóm người đọc đều sử dụng được:

- Người mới có thể cấu hình `.env`, build image và khởi động pipeline bằng Docker Compose mà không cần đọc source code.
- Developer có thể hiểu kiến trúc, dữ liệu MQTT, SCD Type 2, quy trình giả lập, feature engineering, train model, inference realtime và cách kiểm chứng hệ thống.

README phải phản ánh đúng trạng thái code hiện tại và thay thế các hướng dẫn cũ hoặc không còn chính xác.

## Phương án được chọn

Sử dụng một README đầy đủ với phần Quick Start ở gần đầu tài liệu. Các nội dung nâng cao được đặt sau phần vận hành cơ bản, giúp người mới dừng lại sau khi chạy thành công còn developer có thể đọc tiếp mà không cần chuyển qua nhiều tài liệu khác.

Không tách hướng dẫn chính thành nhiều file. Các tài liệu chuyên biệt hiện có trong `docs/` vẫn được liên kết ở cuối README để tham khảo sâu hơn.

## Đối tượng

### Người mới

README cung cấp:

- Yêu cầu hệ thống.
- Cách tạo và điền `.env`.
- Lệnh build và chạy Docker Compose.
- Cách xem trạng thái container và log.
- SQL kiểm tra dữ liệu lịch sử và kết quả dự đoán.
- Cách dừng, khởi động lại và cập nhật image.

### Developer

README cung cấp:

- Sơ đồ kiến trúc và trách nhiệm từng service.
- Cấu trúc thư mục.
- Format message MQTT và cơ chế AES-GCM.
- Quy tắc `source_event_id` và idempotency.
- SCD Type 2 của `parking_slot_history`.
- Danh sách bảng ML.
- Quy trình simulator → materialize → train → build image → realtime inference.
- Giới hạn dự đoán 0–10800 giây cho bãi 60 vị trí.
- Lệnh test, build và push Docker Hub.
- Troubleshooting theo từng thành phần.

## Cấu trúc README

README mới có các phần theo thứ tự:

1. Tên dự án và mô tả ngắn.
2. Tính năng chính.
3. Kiến trúc và luồng dữ liệu.
4. Vai trò của bốn container.
5. Cấu trúc thư mục.
6. Yêu cầu hệ thống.
7. Quick Start.
8. Điều xảy ra khi chạy `docker compose up`.
9. Cấu hình môi trường.
10. Dữ liệu đầu vào và format MQTT.
11. MQTT authentication, ACL và AES-GCM.
12. PostgreSQL SCD Type 2.
13. Dự đoán realtime.
14. Danh sách bảng database.
15. SQL kiểm tra hệ thống.
16. Quy trình giả lập và train lại model.
17. Quản lý model artifact.
18. Build và push Docker image.
19. MJPEG stream.
20. Chạy test.
21. Lệnh vận hành thường dùng.
22. Troubleshooting.
23. Bảo mật, dữ liệu nhạy cảm và Git.
24. Tài liệu liên quan.

## Nội dung kỹ thuật bắt buộc

README phải nói rõ:

- Sức chứa bãi là 60 vị trí: A01–A14, B01–B13, C01–C12, D01–D10 và E01–E11.
- Camera phát khoảng một frame mỗi giây và lặp dataset khi `CAMERA_LOOP_DATASET=true`.
- Timestamp vẫn tăng đơn điệu khi camera quay lại đầu dataset.
- Mỗi frame có `source_event_id` duy nhất theo camera session, cycle và source frame.
- Camera entrypoint mã hóa JSON bằng AES-GCM trước khi publish.
- PostgreSQL sink và prediction service cùng subscribe topic, cùng giải mã message, nhưng ghi vào các bảng khác nhau.
- `parking_slot_history` chỉ tạo version mới khi trạng thái slot thay đổi.
- Prediction service không train model khi khởi động; nó chỉ load artifact được đóng gói tại `/app/models/parking_fill_eta`.
- Mỗi frame tạo tối đa một prediction cho một model version.
- Kết quả dự đoán luôn nằm trong 0–10800 giây.
- `docker compose up --build` build lại image nhưng không train lại model.
- Train lại model là workflow chủ động bên ngoài container inference.
- PostgreSQL nằm ngoài Compose và dữ liệu không bị xóa bởi `docker compose down`.

## Quick Start

Quick Start dùng PowerShell, vì môi trường dự án hiện tại là Windows:

1. Mở Docker Desktop.
2. Copy `.env.example` thành `.env`.
3. Điền `DATABASE_URL` và MQTT credentials.
4. Chạy `docker compose --env-file .env build`.
5. Chạy `docker compose --env-file .env up -d`.
6. Kiểm tra `docker compose ps`.
7. Theo dõi log camera, sink và prediction.
8. Chạy SQL kiểm tra `parking_fill_predictions`.

README cũng ghi chú rằng PostgreSQL chạy trên máy host phải dùng `host.docker.internal` thay cho `localhost` trong `DATABASE_URL`.

## Train lại model

Workflow được mô tả bằng các lệnh thực tế:

1. Cài `ml_service/requirements.txt`.
2. Tạo UUID cho simulation run.
3. Sinh 30 ngày dữ liệu giả lập.
4. Materialize training rows.
5. Train model với `--sample-stride 3`, `--max-iter 150` và `--overwrite` khi thay artifact hiện có.
6. Build lại `prediction-service`.
7. Recreate container prediction.

README cảnh báo rằng các lệnh simulation, materialization và training ghi dữ liệu vào PostgreSQL được chỉ định bởi `DATABASE_URL`.

## Kiểm chứng tài liệu

Sau khi viết README:

- Đối chiếu tên service bằng `docker compose config --services`.
- Kiểm tra Compose bằng `docker compose --env-file .env config --quiet`.
- Đối chiếu mọi module CLI với phần `main()` tương ứng.
- Kiểm tra các đường dẫn artifact và Dockerfile.
- Chạy `git diff --check`.
- Quét README để không còn thông tin SCD2 cũ dựa trên `frame_id`.
- Không hiển thị hoặc sao chép giá trị bí mật từ `.env`.

## Ngoài phạm vi

- Không thay đổi code, schema, model hoặc Docker Compose.
- Không train lại model.
- Không push image mới.
- Không đưa credential thật vào README.
- Không thay thế các báo cáo chuyên sâu hiện có trong `docs/`.
