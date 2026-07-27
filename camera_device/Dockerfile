FROM python:3.12-slim

WORKDIR /app

COPY camera_device/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY stream_clean_to_json.py /app/stream_clean_to_json.py
COPY camera_device/camera_to_mqtt.py /app/camera_to_mqtt.py
COPY camera_device/mjpeg_server.py /app/mjpeg_server.py
COPY camera_device/entrypoint.sh /app/entrypoint.sh
COPY data_clean /app/data/content/dataset

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8081

CMD ["/app/entrypoint.sh"]
