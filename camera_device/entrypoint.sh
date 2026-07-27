#!/bin/sh
set -eu

# Use wall-clock start so SCD2 analytics timestamps match real run time.
START_TS="${CAMERA_START_TIMESTAMP:-$(date +%s)}"
KEY_FILE="${ENCRYPTION_KEY_FILE:-/tmp/mqtt_encryption_key.txt}"
mkdir -p "$(dirname "$KEY_FILE")"

# Create a simple shared key for demo/testing if it does not exist.
printf '%s' '0123456789abcdef' > "$KEY_FILE"

exec python camera_to_mqtt.py \
  --start-timestamp "$START_TS" \
  --encrypt-payload \
  --encryption-key-file "$KEY_FILE"
