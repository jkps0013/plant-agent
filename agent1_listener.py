import os
import sys
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import paho.mqtt.client as mqtt
import sparkplug_b_pb2 as sparkplug

live_buffer = {}

alias_to_name = {}


def decode_sparkplug_payload(payload_bytes, is_birth, device_id):
    payload = sparkplug.Payload()
    payload.ParseFromString(payload_bytes)
    result = {}

    for metric in payload.metrics:
        name = metric.name if metric.name else alias_to_name.get(metric.alias)

        if is_birth and metric.name and metric.HasField("alias"):
            alias_to_name[metric.alias] = metric.name

        if not name:
            continue

        full_name = f"{device_id}/{name}" if device_id else name

        if metric.HasField("float_value"):
            result[full_name] = metric.float_value
        elif metric.HasField("double_value"):
            result[full_name] = metric.double_value
        elif metric.HasField("int_value"):
            result[full_name] = metric.int_value
        elif metric.HasField("boolean_value"):
            result[full_name] = metric.boolean_value
        elif metric.HasField("string_value"):
            result[full_name] = metric.string_value

    return result


def on_message(client, userdata, msg):
    try:
        is_birth = "BIRTH" in msg.topic
        if "DDATA" in msg.topic or is_birth:
            topic_parts = msg.topic.split("/")
            device_id = topic_parts[4] if len(topic_parts) > 4 else None
            decoded = decode_sparkplug_payload(msg.payload, is_birth, device_id)
            live_buffer.update(decoded)
    except Exception as e:
        print(f"[DECODE ERROR] {e}", file=sys.stderr)


def on_connect(client, userdata, flags, rc):
    client.subscribe("spBv1.0/+/DDATA/#")
    client.subscribe("spBv1.0/+/DBIRTH/#")
    client.subscribe("spBv1.0/Walker/DDATA/BristolEdge1/#")
    client.subscribe("spBv1.0/Walker/DBIRTH/BristolEdge1/#")
    client.subscribe("spBv1.0/JK GmbH/DDATA/BristolEdge1/#")
    client.subscribe("spBv1.0/JK GmbH/DBIRTH/BristolEdge1/#")
    print("Listener connected and subscribed.", file=sys.stderr)


def on_subscribe(client, userdata, mid, granted_qos):
    try:
        rebirth = sparkplug.Payload()
        m = rebirth.metrics.add()
        m.name = "Node Control/Rebirth"
        m.datatype = 11
        m.boolean_value = True
        payload_bytes = rebirth.SerializeToString()
        client.publish("spBv1.0/Walker/NCMD/BristolEdge1", payload_bytes)
        client.publish("spBv1.0/JK GmbH/NCMD/BristolEdge1", payload_bytes)
        client.publish("spBv1.0/JK_GmbH/NCMD/BristolEdge1", payload_bytes)
    except Exception as e:
        print(f"[REBIRTH ERROR] {e}", file=sys.stderr)


def start_listener():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect("localhost", 1883, 60)
        client.loop_start()
    except Exception as e:
        print(f"[MQTT CONNECTION ERROR] {e}", file=sys.stderr)
    return client
