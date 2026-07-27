#!/bin/sh
set -eu

# Use wall-clock start so SCD2 analytics timestamps match real run time.
START_TS="${CAMERA_START_TIMESTAMP:-$(date +%s)}"
KEY_FILE="${ENCRYPTION_KEY_FILE:-/tmp/mqtt_encryption_key.txt}"
mkdir -p "$(dirname "$KEY_FILE")"

# Create a simple shared key for demo/testing if it does not exist.
printf '%s' '0123456789abcdef' > "$KEY_FILE"

python camera_to_mqtt.py --start-timestamp "$START_TS" --encrypt-payload --encryption-key-file "$KEY_FILE" &
MQTT_PID=$!

python mjpeg_server.py &
MJPEG_PID=$!

term() {
  kill "$MQTT_PID" "$MJPEG_PID" 2>/dev/null || true
  wait "$MQTT_PID" "$MJPEG_PID" 2>/dev/null || true
}

trap term INT TERM

# Exit if either process dies.
while kill -0 "$MQTT_PID" 2>/dev/null && kill -0 "$MJPEG_PID" 2>/dev/null; do
  sleep 1
done

echo "Camera entrypoint: a child process exited" >&2
term
exit 1
