from pathlib import Path

import yaml


def test_ml_requirements_pin_training_and_inference_runtime():
    requirements = Path("ml_service/requirements.txt").read_text("utf-8")

    for dependency in (
        "joblib==",
        "numpy==",
        "paho-mqtt==",
        "psycopg2-binary==",
        "python-dotenv==",
        "scikit-learn==",
    ):
        assert dependency in requirements


def test_prediction_image_embeds_external_model_artifacts():
    dockerfile = Path("ml_service/Dockerfile").read_text("utf-8")

    assert "COPY models/parking_fill_eta /app/models/parking_fill_eta" in dockerfile
    assert 'CMD ["python", "-m", "ml_service.prediction_service"]' in dockerfile


def test_compose_defines_prediction_service():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text("utf-8"))
    service = compose["services"]["prediction-service"]

    assert service["build"]["dockerfile"] == "ml_service/Dockerfile"
    assert service["environment"]["MODEL_DIR"] == "/app/models/parking_fill_eta"
    assert service["environment"]["MQTT_USERNAME"] == "${MQTT_SUB_USERNAME}"
