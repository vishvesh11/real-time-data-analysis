import paho.mqtt.client as mqtt
import json
import pandas as pd
from influxdb_client import InfluxDBClient, Point, WriteOptions
from influxdb_client.client.write_api import SYNCHRONOUS
import os


MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "delhi/gtfs/vehicle_positions")


INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET")
# --- InfluxDB Client Setup ---
influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)

def write_to_influxdb(data):
    try:
        bearing_value = data.get('bearing')
        if bearing_value is None:
            bearing_value = 0.0  # Default to 0.0 for float if it's None
        point = (
            Point("vehicle_position")
            .tag("vehicle_id", data['vehicle_id'])
            .tag("route_id", data.get('route_id', 'unknown'))
            .field("latitude", float(data['latitude']))
            .field("longitude", float(data['longitude']))
            .field("bearing", float(bearing_value))
            .time(data['timestamp'])
        )
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
        # print(f"Wrote to InfluxDB: {data['vehicle_id']} at {data['timestamp']}")
    except Exception as e:
        print(f"Error writing to InfluxDB: {e} for data: {data}")

# --- MQTT Setup ---
client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Processor Connected to MQTT Broker!")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"Processor failed to connect to MQTT, return code {rc}")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        data = json.loads(payload)
        # print(f"Processor received: {data['vehicle_id']} @ {data['timestamp']}")

        if not all(k in data for k in ['vehicle_id', 'timestamp', 'latitude', 'longitude']):
            print(f"Skipping malformed data: {data}")
            return

        data['timestamp'] = pd.to_datetime(data['timestamp'])

        write_to_influxdb(data)

    except json.JSONDecodeError:
        print(f"Processor: Invalid JSON received: {msg.payload.decode()}")
    except Exception as e:
        print(f"Error processing message: {e}")

client.on_connect = on_connect
client.on_message = on_message
print(f"Processor connecting to MQTT Broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
client.loop_forever()