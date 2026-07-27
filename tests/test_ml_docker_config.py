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


def test_build_script_uses_posix_paths_and_builds_prediction_image():
    script = Path("build_image.sh").read_text("utf-8")

    assert r".\mqtt_broker\Dockerfile" not in script
    assert "-f mqtt_broker/Dockerfile ." in script
    assert "-f ml_service/Dockerfile ." in script
    assert "parking-prediction-service:latest" in script


def test_readme_documents_offline_training_and_prediction_query():
    readme = Path("README.md").read_text("utf-8")

    assert "ml_service.simulation_repository" in readme
    assert "ml_service.materialize_training" in readme
    assert "ml_service.train" in readme
    assert "parking_fill_predictions" in readme
