FROM eclipse-mosquitto:2.0
RUN apk add --no-cache openssl

COPY mqtt_broker/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY mqtt_broker/entrypoint.sh /entrypoint.sh
COPY mqtt_broker/generate_certs.sh /mosquitto/generate_certs.sh
COPY mqtt_broker/certs /mosquitto/certs

RUN sed -i 's/\r$//' /entrypoint.sh \
	&& chmod +x /entrypoint.sh \
	&& chmod +x /mosquitto/generate_certs.sh \
	&& /mosquitto/generate_certs.sh || true \
	&& chown -R mosquitto:mosquitto /mosquitto/certs \
	&& chmod 600 /mosquitto/certs/server.key /mosquitto/certs/client.key \
	&& chmod 644 /mosquitto/certs/ca.crt /mosquitto/certs/server.crt /mosquitto/certs/client.crt

ENTRYPOINT ["/entrypoint.sh"]
