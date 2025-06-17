import time
import json
import requests
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import paho.mqtt.client as mqtt
import os
import pandas as pd # Make sure pandas is imported for pd.to_datetime

# DELHI OTD CONFIG
DELHI_OTD_API_KEY = os.getenv("DELHI_OTD_API_KEY")
GTFS_REALTIME_URL = "https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb?key=" + DELHI_OTD_API_KEY


# Local MQTT Broker
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
FETCH_INTERVAL_SECONDS = 30 # How often to poll API

if not DELHI_OTD_API_KEY in DELHI_OTD_API_KEY:
    print("Error: DELHI_OTD_API_KEY or GTFS_REALTIME_URL not properly configured.")
    exit(1)

# --- MQTT Setup ---
client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Ingestor Connected to MQTT Broker!")
    else:
        print(f"Ingestor failed to connect to MQTT, return code {rc}")

client.on_connect = on_connect
client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
client.loop_start()

def fetch_and_publish_gtfs_data():
    headers = {
        'Accept': 'application/x-protobuf',
        'X-Api-Key': DELHI_OTD_API_KEY
    }
    while True:
        try:
            print(f"Fetching data from {GTFS_REALTIME_URL.split('?')[0]}...")
            response = requests.get(GTFS_REALTIME_URL, headers=headers, timeout=10)
            response.raise_for_status()

            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)

            found_vehicles = 0
            for entity in feed.entity:
                if entity.HasField('vehicle'):
                    vehicle_data = MessageToDict(entity.vehicle, preserving_proto_field_name=True)
                    raw_timestamp = vehicle_data.get('timestamp')
                    #print(f"DEBUG: Raw timestamp from API: {raw_timestamp}")
                    data_to_publish = {
                        'vehicle_id': vehicle_data.get('vehicle', {}).get('id'),
                        'timestamp': pd.to_datetime(vehicle_data.get('timestamp'), unit='s', utc=True).isoformat(),
                        'latitude': vehicle_data.get('position', {}).get('latitude'),
                        'longitude': vehicle_data.get('position', {}).get('longitude'),
                        'bearing': vehicle_data.get('position', {}).get('bearing'),
                        'route_id': vehicle_data.get('trip', {}).get('route_id'),
                        'start_time': vehicle_data.get('trip', {}).get('start_time'),
                        'start_date': vehicle_data.get('trip', {}).get('start_date'),
                    }
                    if all([data_to_publish['vehicle_id'], data_to_publish['latitude'], data_to_publish['longitude']]):
                        json_data = json.dumps(data_to_publish)
                        client.publish(MQTT_TOPIC, json_data)
                        found_vehicles += 1
                        # print(f"Published: {json_data}") #  verbose logging
                    # else:
                        # print(f"Skipping incomplete vehicle data: {vehicle_data}")
            print(f"Fetched and published {found_vehicles} vehicle positions.")

        except requests.exceptions.RequestException as e:
            print(f"Error fetching data from GTFS API: {e}")
        except Exception as e:
            print(f"Error processing GTFS feed: {e}")

        time.sleep(FETCH_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        print(f"Starting Ingestor, fetching from Delhi OTD every {FETCH_INTERVAL_SECONDS} seconds...")
        fetch_and_publish_gtfs_data()
    except KeyboardInterrupt:
        print("Ingestor stopped.")
        client.loop_stop()
        client.disconnect()