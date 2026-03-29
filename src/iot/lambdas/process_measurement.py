"""
IoT Measurement Processor Lambda
Receives MQTT from IoT Rule, writes directly to measurements_docs via DB /execute endpoint.

Follows same pattern as iot-write-event, iot-update-gateway-read-model, etc.
"""
import json
import os
import urllib3
from datetime import datetime

DB_ENDPOINT = os.environ.get('DB_ENDPOINT')

http = urllib3.PoolManager()


def lambda_handler(event, context):
    """
    Receive MQTT from IoT Rule.

    Actual event structure from IoT Rule:
    {
        "payload": {                          # MQTT message body
            "device_id": "scale-test-gateway-005-1",
            "gateway_id": "test-gateway-005",
            "event_type": "measurement",
            "type": "weight_measurement",
            "timestamp": "2026-02-15T06:41:05Z",
            "measurement_id": "scale-test-gateway-005-1-...",
            "payload": {"weight_kg": 19.34, "batch": "Batch 103"}  # actual measurement
        },
        "timestamp": 1771137667077,
        "topic": "gateway/test-gateway-005/device/scale-test-gateway-005-1/measurement",
        "clientId": "test-gateway-005"
    }
    """
    print(f"Received measurement event: {json.dumps(event)}")

    # MQTT message body is nested under 'payload'
    msg = event.get('payload', event)

    gateway_id = msg.get('gateway_id')
    device_id = msg.get('device_id')
    measurement_type = msg.get('type', 'weight_measurement')
    payload = msg.get('payload', {})
    timestamp = msg.get('timestamp')

    if not device_id or not gateway_id:
        print(f"Missing device_id or gateway_id in event: {event}")
        return {'statusCode': 400, 'error': 'Missing device_id or gateway_id'}

    if not timestamp:
        timestamp = datetime.utcnow().isoformat()

    measurement_doc = {
        "measurement_id": None,
        "device_info": {
            "device_id": device_id,
            "gateway_id": gateway_id,
        },
        "measurement_type": measurement_type,
        "timestamp": timestamp,
        "processed": False,
        "uploaded_to_cloud": False,
        "payload": payload
    }

    response = http.request(
        'POST',
        f"{DB_ENDPOINT}/execute",
        body=json.dumps({
            "command": "INSERT INTO measurements_docs (document) VALUES (?)",
            "params": [json.dumps(measurement_doc)]
        }),
        headers={'Content-Type': 'application/json'}
    )

    if response.status != 200:
        raise Exception(f"DB insert failed ({response.status}): {response.data.decode('utf-8')}")

    result = json.loads(response.data.decode('utf-8'))
    measurement_id = result.get('lastrowid')

    print(f"Stored measurement {measurement_id} for device {device_id} gateway {gateway_id}")

    return {
        'statusCode': 200,
        'measurement_id': measurement_id,
        'device_id': device_id,
        'gateway_id': gateway_id
    }
