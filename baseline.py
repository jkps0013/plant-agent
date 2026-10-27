import os
import statistics
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")


def _query_points(metric_path: str, hours: int = 48) -> list:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "{metric_path}")
      |> filter(fn: (r) => r._field == "value")
    '''

    result = query_api.query(flux_query)
    values = []
    for table in result:
        for record in table.records:
            values.append(record.get_value())

    client.close()
    return values


def get_baseline(metric_path: str, hours: int = 48) -> dict:
    if not INFLUX_TOKEN or INFLUX_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        return {"normal_low": None, "normal_high": None}

    try:
        values = _query_points(metric_path, hours)
        if len(values) < 2:
            alt_path = (
                metric_path.replace("Line 3", "Line3").replace("Reactor 2", "Reactor2")
                if " " in metric_path
                else metric_path.replace("Line3", "Line 3").replace("Reactor2", "Reactor 2")
            )
            values = _query_points(alt_path, hours)
    except Exception as e:
        print(f"[BASELINE] InfluxDB query failed: {e}")
        return {"normal_low": None, "normal_high": None}

    if len(values) < 2:
        return {"normal_low": None, "normal_high": None}

    mean = statistics.mean(values)
    stdev = statistics.stdev(values)

    baseline = {
        "normal_low": round(mean - (2 * stdev), 2),
        "normal_high": round(mean + (2 * stdev), 2),
        "mean": round(mean, 2)
    }

    print(f"[BASELINE] {metric_path}: {len(values)} points, mean={baseline['mean']}, normal_low={baseline['normal_low']}, normal_high={baseline['normal_high']}")

    return baseline


if __name__ == "__main__":
    metrics = [
        "Bristol/Polymerization/Line 3/Reactor 2/Temperature",
        "Bristol/Polymerization/Line 3/Reactor 2/Pressure",
        "Bristol/Polymerization/Line 3/CC-01/FlowRate",
        "Bristol/Polymerization/Line3/Reactor2/Temperature"
    ]
    print("\n--- Testing Baselines for all metrics with and without spaces ---")
    for m in metrics:
        b = get_baseline(m, hours=48)
        print(f"{m} -> {b}")
