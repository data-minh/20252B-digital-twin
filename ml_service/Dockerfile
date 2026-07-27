FROM python:3.12-slim

WORKDIR /app

COPY ml_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ml_service /app/ml_service
COPY models/parking_fill_eta /app/models/parking_fill_eta

ENV PYTHONUNBUFFERED=1
ENV MODEL_DIR=/app/models/parking_fill_eta
CMD ["python", "-m", "ml_service.prediction_service"]
