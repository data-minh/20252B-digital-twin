import argparse
import base64
import json
import os
from pathlib import Path

import paho.mqtt.client as mqtt
import ssl
from Crypto.Cipher import AES


def load_encryption_key(path_or_env: str | None) -> bytes | None:
    if not path_or_env:
        return None
    try:
        p = Path(path_or_env)
        if p.exists():
            data = p.read_bytes()
            try:
                return base64.b64decode(data)
            except Exception:
                return data
    except Exception:
        pass
    try:
        return base64.b64decode(path_or_env)
    except Exception:
        return None


def decrypt_aes_gcm(key: bytes, b64payload: bytes) -> bytes:
    data = base64.b64decode(b64payload)
    if len(data) < 28:
        raise ValueError("ciphertext too short for nonce+tag")
    nonce = data[:12]
    tag = data[12:28]
    ciphertext = data[28:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return plaintext


def on_message_factory(encryption_key: bytes | None):
    def _on_message(client, userdata, msg):
        payload = msg.payload
        # Try plain JSON first
        try:
            text = payload.decode('utf-8')
            obj = json.loads(text)
            print("Received plaintext JSON:", json.dumps(obj, ensure_ascii=False))
            return
        except Exception:
            pass

        if not encryption_key:
            print("Received non-JSON payload but no encryption key configured; raw payload:\n", payload)
            return

        try:
            plaintext = decrypt_aes_gcm(encryption_key, payload)
            obj = json.loads(plaintext.decode('utf-8'))
            print("Received decrypted JSON:", json.dumps(obj, ensure_ascii=False))
        except Exception as exc:
            print(f"Failed to decrypt/parse payload: {exc}")

    return _on_message


def configure_tls(client, ca_certs=None, certfile=None, keyfile=None, insecure=False):
    if ca_certs or certfile or keyfile:
        client.tls_set(ca_certs if ca_certs else None, certfile=certfile, keyfile=keyfile, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.tls_insecure_set(insecure)


def main():
    parser = argparse.ArgumentParser(description="MQTT subscriber that can decrypt AES-GCM payloads.")
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "mqtt-broker"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default=os.environ.get("MQTT_TOPIC", "parking/frames"))
    parser.add_argument("--tls", action="store_true", default=os.environ.get("MQTT_TLS") in ("1", "true", "True"))
    parser.add_argument("--ca-certs", default=os.environ.get("MQTT_CA_CERTS"))
    parser.add_argument("--client-cert", default=os.environ.get("MQTT_CLIENT_CERT"))
    parser.add_argument("--client-key", default=os.environ.get("MQTT_CLIENT_KEY"))
    parser.add_argument("--tls-insecure", action="store_true", default=os.environ.get("MQTT_TLS_INSECURE") in ("1", "true", "True"))
    parser.add_argument("--encryption-key-file", default=os.environ.get("ENCRYPTION_KEY_FILE") or os.environ.get("ENCRYPTION_KEY"))
    args = parser.parse_args()

    encryption_key = load_encryption_key(args.encryption_key_file)

    client = mqtt.Client()
    if args.tls:
        configure_tls(client, ca_certs=args.ca_certs, certfile=args.client_cert, keyfile=args.client_key, insecure=args.tls_insecure)

    client.on_message = on_message_factory(encryption_key)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    client.subscribe(args.topic, qos=1)
    print(f"Subscribed to {args.topic} on {args.mqtt_host}:{args.mqtt_port}")
    client.loop_forever()


if __name__ == "__main__":
    main()
