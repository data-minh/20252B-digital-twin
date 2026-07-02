FROM eclipse-mosquitto:2.0

COPY mqtt_broker/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY mqtt_broker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
