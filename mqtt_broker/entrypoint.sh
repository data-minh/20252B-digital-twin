#!/bin/sh
set -eu

: "${MQTT_PUB_USERNAME:?MQTT_PUB_USERNAME is required}"
: "${MQTT_PUB_PASSWORD:?MQTT_PUB_PASSWORD is required}"
: "${MQTT_SUB_USERNAME:?MQTT_SUB_USERNAME is required}"
: "${MQTT_SUB_PASSWORD:?MQTT_SUB_PASSWORD is required}"

MQTT_TOPIC="${MQTT_TOPIC:-parking/frames}"
PASSWORD_FILE="/mosquitto/config/passwords"
ACL_FILE="/mosquitto/config/aclfile"

rm -f "$PASSWORD_FILE" "$ACL_FILE"
mosquitto_passwd -b -c "$PASSWORD_FILE" "$MQTT_PUB_USERNAME" "$MQTT_PUB_PASSWORD"
mosquitto_passwd -b "$PASSWORD_FILE" "$MQTT_SUB_USERNAME" "$MQTT_SUB_PASSWORD"

{
    printf 'user %s\n' "$MQTT_PUB_USERNAME"
    printf 'topic write %s\n\n' "$MQTT_TOPIC"
    printf 'user %s\n' "$MQTT_SUB_USERNAME"
    printf 'topic read %s\n' "$MQTT_TOPIC"
} > "$ACL_FILE"
chown mosquitto:mosquitto "$PASSWORD_FILE" "$ACL_FILE"
chmod 640 "$PASSWORD_FILE" "$ACL_FILE"

exec mosquitto -c /mosquitto/config/mosquitto.conf
