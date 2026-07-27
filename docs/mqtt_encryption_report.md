# Báo cáo chi tiết về cơ chế mã hóa MQTT đã áp dụng

## 1. Mục tiêu của cơ chế bảo mật

Trong hệ thống này, dữ liệu từ camera được gửi qua MQTT đến các consumer như PostgreSQL sink và Thingspeak sink. Để tăng bảo mật, hệ thống đã áp dụng hai lớp bảo vệ:

1. Bảo vệ truyền tải bằng TLS/MQTTS
2. Bảo vệ nội dung payload bằng mã hóa ứng dụng AES-GCM

Điều này giúp:
- ngăn nghe lén trên đường truyền,
- ngăn thay đổi nội dung tin nhắn,
- giảm nguy cơ broker hoặc kẻ nghe trộm đọc được nội dung payload.

---

## 2. Tổng quan kiến trúc

Luồng dữ liệu hiện tại gồm 3 bước chính:

1. Publisher gửi dữ liệu từ camera
2. Broker MQTT nhận và chuyển tiếp tin nhắn
3. Consumer nhận tin nhắn, giải mã (nếu cần) và xử lý

Các file liên quan:
- Publisher: [camera_device/camera_to_mqtt.py](../camera_device/camera_to_mqtt.py)
- Consumer mẫu: [camera_device/mqtt_subscriber.py](../camera_device/mqtt_subscriber.py)
- Consumer chính dùng cho PostgreSQL: [postgres_sink/postgres_sink.py](../postgres_sink/postgres_sink.py)
- Cấu hình broker: [mqtt_broker/mosquitto.conf](../mqtt_broker/mosquitto.conf)
- Script tạo chứng chỉ: [mqtt_broker/generate_certs.sh](../mqtt_broker/generate_certs.sh)

---

## 3. Lớp 1: Mã hóa truyền tải bằng TLS

### 3.1 Mục đích
TLS bảo vệ toàn bộ kết nối giữa client và broker khỏi bị nghe lén hoặc giả mạo trên mạng.

### 3.2 Cách hoạt động
Khi client kết nối tới broker bằng TLS:
- client và server thực hiện TLS handshake,
- broker trình server certificate,
- client xác thực certificate thông qua CA certificate,
- sau đó thiết lập một session key để mã hóa dữ liệu truyền trong suốt phiên kết nối.

### 3.3 Trong code

#### Trong [camera_device/camera_to_mqtt.py](../camera_device/camera_to_mqtt.py)
- Hàm `configure_mqtt_tls(...)` dùng `client.tls_set(...)` để cấu hình TLS cho client MQTT.
- Hàm này nhận các tham số:
  - `ca_certs`: file CA cert dùng để xác thực broker
  - `certfile`: client certificate (dùng trong mTLS)
  - `keyfile`: client private key (dùng trong mTLS)
  - `insecure`: bật/tắt kiểm tra hostname

#### Trong [postgres_sink/postgres_sink.py](../postgres_sink/postgres_sink.py)
- Hàm `configure_mqtt_tls(...)` có chức năng tương tự cho consumer.

#### Trong [mqtt_broker/mosquitto.conf](../mqtt_broker/mosquitto.conf)
- Broker đã mở listener 8883 để nhận kết nối TLS.
- Các đường dẫn chứng chỉ được cấu hình bằng:
  - `cafile`
  - `certfile`
  - `keyfile`

### 3.4 Ý nghĩa thực tế
TLS không làm payload “không thể đọc” đối với broker, nhưng nó bảo vệ dữ liệu khỏi bị nghe ở giữa đường truyền. Nếu người dùng muốn bảo mật mạnh hơn, cần kết hợp với mã hóa payload riêng.

---

## 4. Lớp 2: Mã hóa payload bằng AES-GCM

### 4.1 Mục đích
Mã hóa payload ở cấp ứng dụng để ngay cả khi nội dung đi qua broker, broker cũng không cần đọc được giá trị gốc của tin nhắn.

### 4.2 Thuật toán dùng
Hệ thống dùng AES-GCM, một thuật toán mã hóa khối có tính năng:
- mã hóa dữ liệu,
- kiểm tra tính toàn vẹn bằng authentication tag,
- chống giả mạo dữ liệu.

### 4.3 Quy trình mã hóa
Trong [camera_device/camera_to_mqtt.py](../camera_device/camera_to_mqtt.py):

1. Tạo message JSON từ dữ liệu frame
2. Chuyển JSON sang bytes UTF-8
3. Chọn một khoá bí mật (key)
4. Tạo nonce ngẫu nhiên 12 byte
5. Mã hóa bằng AES-GCM
6. Ghép thành chuỗi gồm:
   - nonce
   - tag
   - ciphertext
7. Base64 hóa kết quả để đưa vào MQTT payload

### 4.4 Hàm chính liên quan

#### `load_encryption_key(...)`
- Tải khóa từ file hoặc từ biến môi trường.
- Hỗ trợ đọc:
  - từ đường dẫn file,
  - hoặc từ chuỗi base64.

#### `encrypt_payload_aes_gcm(...)`
- Nhận `key` và `plaintext_bytes`.
- Kiểm tra độ dài cho phép của key: 16, 24 hoặc 32 bytes.
- Tạo nonce bằng `get_random_bytes(12)`.
- Dùng `AES.new(key, AES.MODE_GCM, nonce=nonce)` để mã hóa.
- Tạo `ciphertext` và `tag`.
- Ghép chúng thành `nonce + tag + ciphertext`.
- Base64 hóa để gửi qua MQTT.

---

## 5. Luồng gửi dữ liệu từ camera tới MQTT

### 5.1 Bước 1: Tạo message
Trong [camera_device/camera_to_mqtt.py](../camera_device/camera_to_mqtt.py), hàm `frame_message(...)` tạo cấu trúc JSON gồm:
- `split`
- `frame_id`
- `source_frame_id`
- `image`
- `payload`

Đây là cấu trúc dữ liệu “business payload” của hệ thống.

### 5.2 Bước 2: Chuyển thành JSON
Hàm `publish_dataset(...)` chạy vòng lặp đọc từng frame và tạo message JSON bằng `json.dumps(...)`.

### 5.3 Bước 3: Gắn bảo mật
Nếu bật `--encrypt-payload`, hệ thống sẽ:
- gọi `encrypt_payload_aes_gcm(...)`,
- gửi payload đã mã hóa thay vì JSON plain text.

### 5.4 Bước 4: Publish qua MQTT
Sau đó hàm `client.publish(...)` gửi tin nhắn tới topic MQTT.

### 5.5 Điểm cần lưu ý
- Nếu không bật mã hóa, hệ thống gửi plaintext JSON.
- Nếu bật mã hóa, đối với consumer cần có cùng khóa để giải mã.

---

## 6. Luồng nhận và giải mã ở consumer

### 6.1 Consumer chính: PostgreSQL sink
Trong [postgres_sink/postgres_sink.py](../postgres_sink/postgres_sink.py), hàm `run_sink(...)` thiết lập kết nối MQTT và đăng ký nhận tin nhắn từ topic.

#### Hàm `on_message(...)`
- Đầu tiên thử parse payload như JSON plaintext.
- Nếu thất bại, nghĩa là payload có thể là ciphertext đã mã hóa.
- Nếu đã cung cấp khóa giải mã, consumer gọi `decrypt_aes_gcm(...)`.
- Sau khi giải mã, consumer parse JSON và tiếp tục lưu vào PostgreSQL.

### 6.2 Consumer mẫu: subscriber
Trong [camera_device/mqtt_subscriber.py](../camera_device/mqtt_subscriber.py), logic tương tự nhưng ở dạng đơn giản để kiểm tra luồng nhận và giải mã.

### 6.3 Hàm giải mã
#### `decrypt_aes_gcm(...)`
- Base64 decode payload nhận được.
- Tách nonce, tag và ciphertext.
- Dùng AES-GCM với cùng khóa để giải mã.
- Nếu `decrypt_and_verify(...)` thành công thì có plaintext JSON.

---

## 7. Vai trò của từng hàm trong code

### Trong [camera_device/camera_to_mqtt.py](../camera_device/camera_to_mqtt.py)

- `optional_int(...)`: chuyển giá trị CLI hoặc env sang số nguyên.
- `optional_bool(...)`: chuyển giá trị CLI/env sang boolean.
- `frame_message(...)`: xây dựng cấu trúc JSON của frame trước khi publish.
- `configure_mqtt_auth(...)`: cấu hình username/password cho MQTT.
- `configure_mqtt_tls(...)`: bật TLS cho kết nối.
- `load_encryption_key(...)`: tải khóa mã hóa.
- `encrypt_payload_aes_gcm(...)`: mã hóa nội dung bằng AES-GCM.
- `connect_mqtt_with_retry(...)`: kết nối tới broker và tự retry nếu broker chưa sẵn sàng.
- `publish_dataset(...)`: luồng chính gửi dataset tới MQTT.
- `main(...)`: parse tham số CLI và gọi hàm publish.

### Trong [postgres_sink/postgres_sink.py](../postgres_sink/postgres_sink.py)

- `history_row(...)`: chuẩn hóa dữ liệu thành bản ghi lịch sử SCD2.
- `apply_scd2_record(...)` và `apply_scd2_records(...)`: lưu dữ liệu vào PostgreSQL.
- `configure_mqtt_auth(...)`: cấu hình auth cho consumer.
- `configure_mqtt_tls(...)`: cấu hình TLS cho consumer.
- `load_encryption_key(...)`: tải khóa giải mã.
- `decrypt_aes_gcm(...)`: giải mã payload AES-GCM.
- `run_sink(...)`: thiết lập subscriber MQTT.
- `on_message(...)`: xử lý tin nhắn nhận được từ broker.
- `main(...)`: parse CLI và gọi run_sink.

### Trong [camera_device/mqtt_subscriber.py](../camera_device/mqtt_subscriber.py)

- `load_encryption_key(...)`: tải khóa.
- `decrypt_aes_gcm(...)`: giải mã AES-GCM.
- `on_message_factory(...)`: tạo callback xử lý message.
- `configure_tls(...)`: cấu hình TLS.
- `main(...)`: chạy subscriber để thử luồng nhận.

---

## 8. Điểm mạnh của cơ chế hiện tại

- Tăng bảo mật so với MQTT plaintext thông thường.
- TLS bảo vệ kết nối khỏi bị nghe và giả mạo.
- AES-GCM cung cấp cả mã hóa và kiểm tra toàn vẹn.
- Có thể áp dụng cho bất kỳ consumer nào, không phụ thuộc vào PostgreSQL.

---

## 9. Hạn chế và lưu ý quan trọng

1. Khóa mã hóa phải được chia sẻ an toàn giữa publisher và consumer.
2. Hiện tại, khóa được đọc từ file hoặc biến môi trường, nên trong môi trường production cần dùng hệ thống quản lý khóa (KMS, Vault, HSM).
3. Không nên dùng key chung lâu dài mà nên có rotation định kỳ.
4. Nếu dùng TLS, nên ưu tiên cert từ CA đáng tin cậy thay vì self-signed trong môi trường production.
5. Trong ví dụ hiện tại, khóa mã hóa không phải là TLS private key; nên nên sử dụng một key riêng dành cho payload.

---

## 10. Kết luận

Cơ chế mã hóa hiện tại đã áp dụng đúng hướng tiếp cận hai lớp:
- TLS cho bảo vệ đường truyền,
- AES-GCM cho bảo vệ nội dung payload.

Vai trò của từng phần trong hệ thống như sau:
- Publisher: mã hóa trước khi gửi;
- Broker: chuyển tiếp tin nhắn mà không cần hiểu nội dung;
- Consumer: giải mã và xử lý dữ liệu.

Điều này phù hợp cho một hệ thống IoT cần bảo mật tốt hơn MQTT plain text nhưng vẫn giữ được hiệu năng và tính đơn giản.
