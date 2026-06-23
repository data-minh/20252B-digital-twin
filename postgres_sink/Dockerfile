FROM python:3.12-slim

WORKDIR /app

COPY postgres_sink/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY postgres_sink/postgres_sink.py /app/postgres_sink.py

ENV PYTHONUNBUFFERED=1
CMD ["python", "postgres_sink.py"]
