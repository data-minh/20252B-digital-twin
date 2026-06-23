import argparse
import json
import os
import time

import paho.mqtt.client as mqtt

import stream_clean_to_json as stream


def upload_frame_message(
    message,
    thingspeak_url_env: str,
    thingspeak_api_key_env: str,
    thingspeak_delay_seconds: float,
    thingspeak_upload_mode: str,
):
    payload = message["payload"]
    print(
        f"Sink received frame_id={message.get('frame_id')} "
        f"source_frame_id={message.get('source_frame_id')} slots={len(payload)}",
        flush=True,
    )
    return stream.upload_to_thingspeak(
        payload,
        thingspeak_url_env=thingspeak_url_env,
        thingspeak_api_key_env=thingspeak_api_key_env,
        delay_seconds=thingspeak_delay_seconds,
        upload_mode=thingspeak_upload_mode,
        log_uploads=True,
    )


def run_sink(
    mqtt_host: str,
    mqtt_port: int,
    topic: str,
    thingspeak_url_env: str,
    thingspeak_api_key_env: str,
    thingspeak_delay_seconds: float,
    thingspeak_upload_mode: str,
    clear_before_upload: bool,
    thingspeak_channel_id_env: str,
    thingspeak_user_api_key_env: str,
):
    stream.load_environment()
    if clear_before_upload:
        stream.clear_thingspeak_channel(
            thingspeak_channel_id_env=thingspeak_channel_id_env,
            thingspeak_user_api_key_env=thingspeak_user_api_key_env,
        )
        print("Sink cleared ThingSpeak channel feeds before upload", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Sink connected to MQTT reason_code={reason_code}; subscribing topic={topic}", flush=True)
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        try:
            message = json.loads(msg.payload.decode("utf-8"))
            upload_frame_message(
                message,
                thingspeak_url_env=thingspeak_url_env,
                thingspeak_api_key_env=thingspeak_api_key_env,
                thingspeak_delay_seconds=thingspeak_delay_seconds,
                thingspeak_upload_mode=thingspeak_upload_mode,
            )
        except Exception as exc:
            print(f"Sink failed to process MQTT message on {msg.topic}: {exc}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(mqtt_host, mqtt_port, keepalive=60)
            break
        except OSError as exc:
            print(f"Sink waiting for MQTT broker {mqtt_host}:{mqtt_port}: {exc}", flush=True)
            time.sleep(2)

    client.loop_forever()


def main():
    parser = argparse.ArgumentParser(description="Subscribe to MQTT parking frames and upload to ThingSpeak.")
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "mqtt-broker"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default=os.environ.get("MQTT_TOPIC", "parking/frames"))
    parser.add_argument("--thingspeak-url-env", default=os.environ.get("THINGSPEAK_URL_ENV", "THINGSPEAK_URL"))
    parser.add_argument("--thingspeak-api-key-env", default=os.environ.get("THINGSPEAK_API_KEY_ENV", "THINGSPEAK_API_KEY"))
    parser.add_argument("--thingspeak-delay", type=float, default=float(os.environ.get("THINGSPEAK_DELAY", "16")))
    parser.add_argument("--thingspeak-upload-mode", default=os.environ.get("THINGSPEAK_UPLOAD_MODE", "slot"))
    parser.add_argument("--clear-thingspeak-before-upload", action="store_true", default=os.environ.get("CLEAR_THINGSPEAK_BEFORE_UPLOAD", "false").lower() == "true")
    parser.add_argument("--thingspeak-channel-id-env", default=os.environ.get("THINGSPEAK_CHANNEL_ID_ENV", stream.DEFAULT_THINGSPEAK_CHANNEL_ID_ENV))
    parser.add_argument("--thingspeak-user-api-key-env", default=os.environ.get("THINGSPEAK_USER_API_KEY_ENV", stream.DEFAULT_THINGSPEAK_USER_API_KEY_ENV))
    args = parser.parse_args()

    run_sink(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        topic=args.topic,
        thingspeak_url_env=args.thingspeak_url_env,
        thingspeak_api_key_env=args.thingspeak_api_key_env,
        thingspeak_delay_seconds=args.thingspeak_delay,
        thingspeak_upload_mode=args.thingspeak_upload_mode,
        clear_before_upload=args.clear_thingspeak_before_upload,
        thingspeak_channel_id_env=args.thingspeak_channel_id_env,
        thingspeak_user_api_key_env=args.thingspeak_user_api_key_env,
    )


if __name__ == "__main__":
    main()
