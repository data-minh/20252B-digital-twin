# Stream parking data to JSON or ThingSpeak

File chinh:

```text
stream_clean_to_json.py
```

Script nay doc dataset YOLO raw hoac dataset da clean, gan `slot_id` dang so cho tung slot, roi xuat moi frame thanh mot JSON rieng hoac upload len ThingSpeak.

## Cau truc input

Mac dinh script doc tu:

```text
data/content/dataset_clean
```

Co the truyen dataset raw tu Roboflow:

```text
data/content/dataset
```

hoac dataset da clean:

```text
data/content/dataset_clean
```

Folder input can co cau truc:

```text
dataset_clean/
  train/
    images/
      0001.jpg
    labels/
      0001.txt
  valid/
    images/
    labels/
  test/
    images/
    labels/
```

Co the truyen folder cha `dataset` / `dataset_clean` hoac truyen truc tiep mot split folder nhu `dataset/train` / `dataset_clean/train`, mien la folder do co cap `images/` va `labels/`.

Neu input la raw Roboflow dataset, script se tu lam logic clean nhu notebook:

```text
1. Chi giu anh co ten khop pattern video parking lot trong cell 4.
2. Sap xep anh theo ID trong ten file goc, vi du mp4-2 dung truoc mp4-10.
3. Doi frame_id logic thanh 0001, 0002, ... trong luc xu ly.
4. Khong copy anh/label ra dataset_clean; viec clean dien ra trong memory.
```

Moi anh can co label cung ten stem. Vi du:

```text
images/0001.jpg
labels/0001.txt
```

Neu co anh nhung khong co label tuong ung, frame do se bi bo qua.

## Format label

Script ho tro label YOLO bbox:

```text
class_id x_center y_center width height
```

Script cung co xu ly label polygon neu dong label co nhieu hon 4 toa do.

## Quy tac frame_id va slot_id

`frame_id` lay tu ten file anh da clean hoac tu so thu tu sau khi clean raw. Neu frame id la so thi convert thanh integer:

```text
0001.jpg -> "frame_id": 1
0010.jpg -> "frame_id": 10
```

`slot_id` dung quy tac:

```text
slot_id = row_letter + zero_padded_column
```

Vi du:

```text
A01 = hang A, cot 1
A12 = hang A, cot 12
B01 = hang B, cot 1
B03 = hang B, cot 3
```

Muon doc nguoc:

```text
row_letter = slot_id[0]
column_number = int(slot_id[1:])
```

`timestamp` duoc mo phong theo thu tu frame:

```text
timestamp = start_timestamp + (frame_id - 1) * frame_interval_seconds
```

Mac dinh:

```text
start_timestamp = 1634567890
frame_interval_seconds = 1
```

## Output JSON

Moi frame ghi ra mot file JSON rieng trong output folder. Mac dinh:

```text
data/stream_json_outputs
```

Vi du:

```text
data/stream_json_outputs/train/0001.json
```

Noi dung JSON:

```json
[
  {
    "frame_id": 1,
    "id": "A01",
    "occupied": 1,
    "timestamp": 1634567890
  },
  {
    "frame_id": 1,
    "id": "A02",
    "occupied": 0,
    "timestamp": 1634567890
  }
]
```

Trong do:

```text
frame_id: ID frame lay tu ten anh
id      : ID slot dang A01/B02 theo quy tac hang/cot
occupied: class trong label YOLO, thuong 1 = occupied, 0 = empty
timestamp: Unix timestamp dang int, co the convert nguoc ve datetime
```

## Chay mot lan va luu JSON

Tu dataset raw:

```powershell
python stream_clean_to_json.py --input data/content/dataset --output data/stream_json_outputs --storage json --start-timestamp 1634567890 --frame-interval-seconds 1
```

Tu dataset da clean:

```powershell
python stream_clean_to_json.py --input data/content/dataset_clean --output data/stream_json_outputs --storage json --start-timestamp 1634567890 --frame-interval-seconds 1
```

## Chay watch folder lien tuc

Script se poll folder theo chu ky `--interval`. Khi thay anh va label moi, script xu ly frame do.

```powershell
python stream_clean_to_json.py --input data/content/dataset_clean --output data/stream_json_outputs --watch --interval 2 --storage json
```

## Upload ThingSpeak

Script lay URL va API key tu environment variables.

Mac dinh:

```text
THINGSPEAK_URL
THINGSPEAK_API_KEY
THINGSPEAK_CHANNEL_ID
THINGSPEAK_USER_API_KEY
```

Set env tren PowerShell:

```powershell
$env:THINGSPEAK_URL="https://api.thingspeak.com/update"
$env:THINGSPEAK_API_KEY="YOUR_WRITE_API_KEY"
$env:THINGSPEAK_CHANNEL_ID="3410910"
$env:THINGSPEAK_USER_API_KEY="YOUR_USER_API_KEY"
```

Trong do:

```text
THINGSPEAK_API_KEY      = Write API Key cua channel, dung de upload du lieu.
THINGSPEAK_CHANNEL_ID   = ID cua channel ThingSpeak.
THINGSPEAK_USER_API_KEY = User API Key cua tai khoan, dung de xoa feed trong channel.
```

Chi upload ThingSpeak, khong luu JSON:

```powershell
python stream_clean_to_json.py --input data/content/dataset --storage thingspeak --thingspeak-upload-mode slot --thingspeak-delay 16
```

Vua luu JSON vua upload ThingSpeak:

```powershell
python stream_clean_to_json.py --input data/content/dataset --output data/stream_json_outputs --storage both --thingspeak-upload-mode slot --thingspeak-delay 16
```

Neu muon xoa toan bo du lieu cu trong channel truoc khi upload lai:

```powershell
python stream_clean_to_json.py --input data/content/dataset --storage thingspeak --thingspeak-upload-mode slot --thingspeak-delay 16 --clear-thingspeak-before-upload
```

Lenh tren se goi API xoa feed mot lan truoc khi upload:

```text
DELETE https://api.thingspeak.com/channels/<THINGSPEAK_CHANNEL_ID>/feeds.json
```

Sau do script moi bat dau day du lieu moi len channel.

Neu muon chay watch folder lien tuc:

```powershell
python stream_clean_to_json.py --input data/content/dataset --output data/stream_json_outputs --watch --storage both --thingspeak-upload-mode slot --thingspeak-delay 16
```

Neu muon dung ten env khac:

```powershell
$env:MY_TS_URL="https://api.thingspeak.com/update"
$env:MY_TS_KEY="YOUR_WRITE_API_KEY"

python stream_clean_to_json.py --input data/content/dataset_clean --watch --storage thingspeak --thingspeak-url-env MY_TS_URL --thingspeak-api-key-env MY_TS_KEY
```

## Mapping field khi upload ThingSpeak

Mac dinh script upload tung record theo dung template JSON:

```json
{
  "frame_id": 2,
  "id": 101,
  "occupied": 1,
  "timestamp": 1634567890
}
```

Vi ThingSpeak update API nhan du lieu qua field, object nay duoc map nhu sau:

```text
field1 = frame_id
field2 = id
field3 = occupied
field4 = timestamp
```

Neu can gom ca frame thanh mot request ThingSpeak, dung `--thingspeak-upload-mode frame`. Khi do payload aggregate la:

```text
field1 = frame_id
field2 = timestamp
field3 = total_slots
field4 = occupied_count
field5 = empty_count
field6 = compact JSON cua cac slot [{"id":101,"occupied":1},...]
```

`--thingspeak-delay` la so giay nghi sau moi record khi upload theo `slot`. Vi du `--thingspeak-delay 16` nghia la moi 16 giay day 1 record len ThingSpeak.

`--frame-upload-interval` chi dung khi upload theo `frame`.

Luu y: ThingSpeak co gioi han toc do ghi. Neu channel cua ban yeu cau cach nhau 15 giay, hay dat `--thingspeak-delay 16`.

## Cac argument chinh

```text
--input                 Folder dataset raw/clean dau vao
--output                Folder ghi JSON dau ra
--watch                 Doc lien tuc thay vi chay mot lan
--interval              So giay giua moi lan poll khi watch
--storage               json | thingspeak | both
--thingspeak-url-env    Ten env var chua ThingSpeak URL
--thingspeak-api-key-env Ten env var chua ThingSpeak API key
--thingspeak-channel-id-env Ten env var chua ThingSpeak channel ID
--thingspeak-user-api-key-env Ten env var chua ThingSpeak user API key
--clear-thingspeak-before-upload Xoa feed ThingSpeak truoc khi upload
--thingspeak-upload-mode frame | slot
--frame-upload-interval So giay nghi sau moi frame upload
--start-timestamp       Unix timestamp int cho frame dau tien
--frame-interval-seconds Khoang cach timestamp giua 2 frame lien tiep
```
