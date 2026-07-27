from camera_device.camera_to_mqtt import source_event_id
from postgres_sink.postgres_sink import record_unique_id


def test_source_event_id_changes_between_cycles():
    assert source_event_id("session-a", 1, "0001") == "session-a:1:0001"
    assert source_event_id("session-a", 2, "0001") == "session-a:2:0001"


def test_history_identity_uses_source_event_not_reused_frame_id():
    first = record_unique_id("session-a:1:0001", "A01")
    second = record_unique_id("session-a:2:0001", "A01")

    assert first != second
    assert first == record_unique_id("session-a:1:0001", "A01")
