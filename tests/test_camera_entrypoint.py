from pathlib import Path


def test_camera_entrypoint_runs_one_integrated_process():
    script = Path("camera_device/entrypoint.sh").read_text("utf-8")

    assert "exec python camera_to_mqtt.py" in script
    assert '--start-timestamp "$START_TS"' in script
    assert "--encrypt-payload" in script
    assert '--encryption-key-file "$KEY_FILE"' in script
    assert "python mjpeg_server.py" not in script
    assert "MQTT_PID" not in script
    assert "MJPEG_PID" not in script
