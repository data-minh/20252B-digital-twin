FROM python:3.12-slim

WORKDIR /app

COPY camera_device/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY stream_clean_to_json.py /app/stream_clean_to_json.py
COPY camera_device/camera_to_mqtt.py /app/camera_to_mqtt.py
COPY data_clean /app/data/content/dataset

ENV PYTHONUNBUFFERED=1
CMD ["python", "camera_to_mqtt.py"]
