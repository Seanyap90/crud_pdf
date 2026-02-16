"""
Check Gateway Status Lambda
Reads the 'status' named shadow for a gateway and returns whether it is connected.
Used by ConfigUpdateStateMachine to decide whether to skip WaitForConfigRequest.

Returns: {"status": "connected" | "disconnected" | "unknown"}
"""
import json
import boto3
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

iot_data = boto3.client('iot-data')


def lambda_handler(event, context):
    gateway_id = event.get('gateway_id')
    if not gateway_id:
        return {'status': 'unknown', 'reason': 'missing gateway_id'}

    try:
        response = iot_data.get_thing_shadow(
            thingName=gateway_id,
            shadowName='status'
        )
        shadow = json.loads(response['payload'].read())
        reported_status = (
            shadow.get('state', {})
                  .get('reported', {})
                  .get('status', 'unknown')
        )
        logger.info(f"Gateway {gateway_id} shadow reported status: {reported_status}")
        return {'status': reported_status}

    except ClientError as e:
        code = e.response['Error']['Code']
        if code == 'ResourceNotFoundException':
            logger.info(f"No status shadow found for gateway {gateway_id} - treating as disconnected")
            return {'status': 'unknown', 'reason': 'shadow_not_found'}
        logger.error(f"Error reading shadow for {gateway_id}: {e}")
        return {'status': 'unknown', 'reason': str(e)}