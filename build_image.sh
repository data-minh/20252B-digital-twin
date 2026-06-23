docker build -t minh333/parking-mqtt-broker:latest -f .\mqtt_broker\Dockerfile .
docker build -t minh333/parking-camera-device:latest -f .\camera_device\Dockerfile .
docker build -t minh333/parking-postgres-sink:latest -f .\postgres_sink\Dockerfile .

docker push minh333/parking-mqtt-broker:latest
docker push minh333/parking-camera-device:latest
docker push minh333/parking-postgres-sink:latest
