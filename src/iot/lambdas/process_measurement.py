"""
IoT Measurement Processor - MQTT Proxy Lambda
Receives MQTT from IoT Rule, forwards to FastAPI Lambda
"""
import json
import os
import urllib3
from datetime import datetime

FASTAPI_FUNCTION_URL = os.environ.get('FASTAPI_FUNCTION_URL')

http = urllib3.PoolManager()


def lambda_handler(event, context):
    """
    Receive MQTT from gateway/{id}/measurement
    Forward to FastAPI Lambda /api/measurements

    Event from IoT Rule:
    {
        "gateway_id": "gateway-001",
        "device_id": "scale-001-1",
        "measurement_type": "weight_measurement",
        "payload": {"weight_kg": 12.5, "batch": "B101"},
        "timestamp": 1234567890000
    }
    """
    print(f"Received measurement: {json.dumps(event)}")

    try:
        device_id = event.get('device_id')
        gateway_id = event.get('gateway_id')
        measurement_type = event.get('measurement_type', 'weight_measurement')
        payload = event.get('payload', {})
        timestamp = event.get('timestamp')

        if not device_id or not gateway_id:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing device_id or gateway_id'})}

        # Convert epoch timestamp to ISO format if needed
        if isinstance(timestamp, (int, float)):
            timestamp = datetime.utcfromtimestamp(timestamp / 1000).isoformat()

        # Forward to FastAPI Lambda
        response = http.request(
            'POST',
            f"{FASTAPI_FUNCTION_URL}/api/measurements",
            fields={
                'device_id': device_id,
                'gateway_id': gateway_id,
                'measurement_type': measurement_type,
                'payload': json.dumps(payload),
                'timestamp': timestamp
            },
            timeout=30.0
        )

        if response.status >= 400:
            error_msg = response.data.decode('utf-8')
            print(f"FastAPI error ({response.status}): {error_msg}")
            return {'statusCode': response.status, 'body': error_msg}

        result = json.loads(response.data.decode('utf-8'))
        print(f"Stored measurement: {result}")

        return {'statusCode': 200, 'body': json.dumps(result)}

    except Exception as e:
        print(f"Error: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
