import os
import math
import random
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")


def seed_data(hours: int = 48):
    if not INFLUX_TOKEN or INFLUX_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        print("Please paste your real INFLUX_TOKEN into .env before running.")
        return

    if not INFLUX_BUCKET or INFLUX_BUCKET == "PASTE_YOUR_BUCKET_NAME_HERE":
        print("Please paste your real INFLUX_BUCKET into .env before running.")
        return

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    metrics_config = [
        {
            "metric": "Bristol/Polymerization/Line 3/Reactor 2/Temperature",
            "center": 70.0,
            "amp": 3.5,
            "noise": 0.4
        },
        {
            "metric": "Bristol/Polymerization/Line 3/Reactor 2/Pressure",
            "center": 101.3,
            "amp": 2.0,
            "noise": 0.3
        },
        {
            "metric": "Bristol/Polymerization/Line 3/CC-01/FlowRate",
            "center": 12.5,
            "amp": 0.8,
            "noise": 0.15
        },
        {
            "metric": "Bristol/Polymerization/Line3/Reactor2/Temperature",
            "center": 70.0,
            "amp": 3.5,
            "noise": 0.4
        },
        {
            "metric": "Bristol/Polymerization/Line3/Reactor2/Pressure",
            "center": 101.3,
            "amp": 2.0,
            "noise": 0.3
        },
        {
            "metric": "Bristol/Polymerization/Line3/CC-01/FlowRate",
            "center": 12.5,
            "amp": 0.8,
            "noise": 0.15
        }
    ]

    now = datetime.now(timezone.utc)
    interval_minutes = 10
    total_points = (hours * 60) // interval_minutes

    for cfg in metrics_config:
        points = []
        for i in range(total_points):
            point_time = now - timedelta(minutes=(total_points - i) * interval_minutes)
            cycle = (i % 36) / 36.0 * 2 * math.pi
            val = round(cfg["center"] + cfg["amp"] * math.sin(cycle) + random.uniform(-cfg["noise"], cfg["noise"]), 2)

            point = (
                Point(cfg["metric"])
                .field("value", float(val))
                .time(point_time, WritePrecision.S)
            )
            points.append(point)

        write_api.write(bucket=INFLUX_BUCKET, record=points)
        print(f"Seeded {len(points)} points for '{cfg['metric']}'.")

    client.close()
    print(f"Industrial baseline metrics successfully seeded into '{INFLUX_BUCKET}' over the last {hours} hours.")


if __name__ == "__main__":
    seed_data()
