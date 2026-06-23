FROM python:3.12-slim

WORKDIR /app

COPY thingspeak_sink/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY stream_clean_to_json.py /app/stream_clean_to_json.py
COPY thingspeak_sink/mqtt_to_thingspeak.py /app/mqtt_to_thingspeak.py

ENV PYTHONUNBUFFERED=1
CMD ["python", "mqtt_to_thingspeak.py"]
