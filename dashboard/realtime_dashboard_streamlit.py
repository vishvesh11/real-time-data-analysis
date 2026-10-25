import os
import time
from datetime import datetime, timedelta
import pytz
import pandas as pd
import plotly.graph_objects as go
from geopy.distance import geodesic
from influxdb_client import InfluxDBClient
import streamlit as st

# --- Configuration ---
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb-service:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "home")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "delhi-transport-data")

# --- InfluxDB Client Setup ---
influx_client = InfluxDBClient(
    url=INFLUXDB_URL,
    token=INFLUXDB_TOKEN,
    org=INFLUXDB_ORG
)
query_api = influx_client.query_api()


# Function to calculate Haversine distance (geodesic distance on a sphere)
def calculate_distance_km(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).km


@st.cache_data(ttl=5)  # Cache data for 5 seconds
def fetch_live_data_from_influxdb():
    current_utc_time = datetime.now(pytz.utc)
    start_time = current_utc_time - timedelta(minutes=5)
    start_time_str = start_time.isoformat(timespec='seconds')

    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: time(v: "{start_time_str}"))
      |> filter(fn: (r) => r._measurement == "vehicle_position")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
      |> keep(columns: ["_time", "vehicle_id", "route_id", "latitude", "longitude", "bearing"])
      |> sort(columns: ["_time"], desc: false)
    '''

    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        records = []
        for table in tables:
            for record in table.records:
                records.append(record.values)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        # Rename _time to timestamp
        if '_time' in df.columns:
            df = df.rename(columns={'_time': 'timestamp'})

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df = df.dropna(subset=['latitude', 'longitude'])

        if df.empty:
            return pd.DataFrame()

        # Sort by vehicle_id and then by timestamp for speed calculation
        df = df.sort_values(by=['vehicle_id', 'timestamp'], ascending=True)

        df['prev_latitude'] = df.groupby('vehicle_id')['latitude'].shift(1)
        df['prev_longitude'] = df.groupby('vehicle_id')['longitude'].shift(1)
        df['prev_timestamp'] = df.groupby('vehicle_id')['timestamp'].shift(1)

        df['distance_km'] = df.apply(
            lambda row: calculate_distance_km(
                row['prev_latitude'], row['prev_longitude'],
                row['latitude'], row['longitude']
            ) if pd.notna(row['prev_latitude']) else 0.0,
            axis=1
        )
        df['time_diff_seconds'] = (df['timestamp'] - df['prev_timestamp']).dt.total_seconds()

        df['speed_kmh'] = df.apply(
            lambda row: (row['distance_km'] / row['time_diff_seconds']) * 3600.0
            if pd.notna(row['time_diff_seconds']) and row['time_diff_seconds'] > 0 else 0.0,
            axis=1
        )
        df['speed_kmh'] = df['speed_kmh'].clip(lower=0.0, upper=100.0)

        # Get the latest position for each vehicle
        df_latest = df.sort_values('timestamp').drop_duplicates(subset=['vehicle_id'], keep='last')
        return df_latest

    except Exception as e:
        st.error(f"Error fetching data from InfluxDB: {e}")
        return pd.DataFrame()


# --- Streamlit Layout ---
st.set_page_config(page_title="Delhi Transit Live", layout="wide")
st.title("Real-time Delhi Public Transport Monitoring")

# Metrics
col1, col2, col3 = st.columns(3)

# Placeholders for dynamic rendering
map_placeholder = st.empty()
table_placeholder = st.empty()

# Refresh loop
while True:
    df = fetch_live_data_from_influxdb()

    # --- KPI Section ---
    with col1:
        total_active_vehicles = len(df) if not df.empty else 0
        st.metric("Total Active Vehicles", total_active_vehicles)

    with col2:
        average_speed = df['speed_kmh'].mean().round(2) if not df.empty and 'speed_kmh' in df.columns else 0.0
        st.metric("Avg. Speed (km/h)", average_speed)

    with col3:
        if not df.empty:
            latest_timestamp_utc = df['timestamp'].max()
            ist_tz = pytz.timezone('Asia/Kolkata')
            latest_timestamp_ist = latest_timestamp_utc.astimezone(ist_tz)

            time_diff = datetime.now(pytz.utc) - latest_timestamp_utc
            time_diff_seconds = int(time_diff.total_seconds())

            if time_diff_seconds < 60:
                freshness_str = f"{time_diff_seconds} seconds ago"
            else:
                freshness_str = f"{int(time_diff_seconds / 60)} minutes ago"

            st.metric("Latest Data Freshness", freshness_str)
        else:
            st.metric("Latest Data Freshness", "N/A")

    # --- OpenStreetMap Section ---
    with map_placeholder.container():
        if df.empty:
            st.info("Waiting for real-time data from InfluxDB...")
            fig = go.Figure(
                go.Scattermap(
                    lat=[28.7041],
                    lon=[77.1025],
                    mode="markers",
                    marker=dict(size=1)
                )
            )
            fig.update_layout(
                map_style="open-street-map",
                map_center={"lat": 28.7041, "lon": 77.1025},
                map_zoom=9,
                margin={"r": 0, "t": 0, "l": 0, "b": 0}
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.subheader("Live Vehicle Positions")

            df['hover_text'] = df.apply(
                lambda row: f"Vehicle ID: {row['vehicle_id']}<br>"
                            f"Route: {row.get('route_id', 'N/A')}<br>"
                            f"Lat: {row['latitude']:.4f}, Lon: {row['longitude']:.4f}<br>"
                            f"Speed: {row['speed_kmh']:.1f} km/h<br>"
                            f"Time: {row['timestamp'].strftime('%H:%M:%S %Z')}",
                axis=1
            )

            scatter_map = go.Scattermap(
                lat=df['latitude'],
                lon=df['longitude'],
                mode='markers',
                marker=dict(
                    size=10,
                    color=df['speed_kmh'],
                    colorscale="Viridis",
                    colorbar=dict(title="Speed (km/h)"),
                    opacity=0.8
                ),
                text=df['hover_text'],
                hoverinfo='text'
            )

            fig = go.Figure(data=[scatter_map])
            fig.update_layout(
                map_style="open-street-map",
                map_center={"lat": float(df['latitude'].mean()), "lon": float(df['longitude'].mean())},
                map_zoom=10,
                margin={"r": 0, "t": 0, "l": 0, "b": 0}
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Table Section ---
    with table_placeholder.container():
        if not df.empty:
            st.subheader("Latest Readings")
            latest_table_data = df[['vehicle_id', 'route_id', 'latitude', 'longitude', 'speed_kmh', 'timestamp']].round(
                {
                    'latitude': 4,
                    'longitude': 4,
                    'speed_kmh': 1
                }
            ).copy()
            latest_table_data['timestamp'] = latest_table_data['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S %Z')
            latest_table_data = latest_table_data.sort_values(by='timestamp', ascending=False)
            st.dataframe(latest_table_data, use_container_width=True)
        else:
            st.write("No vehicle records found.")

    time.sleep(30)
    st.rerun()
