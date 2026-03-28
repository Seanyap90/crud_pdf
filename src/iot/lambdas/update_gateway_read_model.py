import json
import urllib3
import os
from datetime import datetime, timezone

# DB_ENDPOINT should include the http:// prefix
DB_ENDPOINT = os.environ.get('DB_ENDPOINT', 'http://172.31.x.x:8080')

http = urllib3.PoolManager()

# Event types that change connection status
STATUS_EVENTS = {"GatewayConnected", "GatewayDisconnected", "GatewayDeleted"}


def _parse_ts(ts_val):
    """Parse timestamp to UTC-aware datetime. Handles ISO strings and Unix ms integers."""
    if not ts_val:
        return None
    try:
        if isinstance(ts_val, (int, float)):
            return datetime.utcfromtimestamp(ts_val / 1000).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(ts_val).replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def lambda_handler(event, context):
    """
    Rebuild gateway read model by replaying all events in version order.

    Stale-event guard: for GatewayDisconnected events, if the event carries a
    session_id that does not match the session_id of the last GatewayConnected
    event, the event is treated as a stale LWT from a prior session and skipped.
    This handles the docker-rm-f + fast-restart race where IoT Core delivers the
    old LWT after the new session has already published its connected message.

    Fallback: events without session_id (old format) fall back to timestamp
    comparison — only applied when broker timestamp >= last status timestamp.
    """
    gateway_id = event['gateway_id']

    events_response = http.request(
        'POST',
        f"{DB_ENDPOINT}/query",
        body=json.dumps({
            "query": """
                SELECT event_type, event_data, version
                FROM events
                WHERE aggregate_id = ? AND aggregate_type = 'gateway'
                ORDER BY version ASC
            """,
            "params": [gateway_id]
        }),
        headers={'Content-Type': 'application/json'}
    )

    data = json.loads(events_response.data.decode('utf-8'))
    events = data.get('results', [])

    if not events:
        return {
            "statusCode": 404,
            "message": "No events found for gateway"
        }

    gateway_doc = {
        "gateway_id": gateway_id,
        "name": None,
        "location": None,
        "status": "unknown",
        "last_updated": None,
        "last_heartbeat": None,
        "uptime": None,
        "health": None,
        "error": None,
        "created_at": None,
        "connected_at": None,
        "disconnected_at": None,
        "deleted_at": None,
        "certificate_info": None,
        "version": 0
    }

    # Tracks the session_id from the last GatewayConnected event.
    # Used to detect stale LWT disconnect events from a prior session.
    last_connected_session_id = None
    # Fallback: timestamp of last applied status event (for events without session_id).
    last_status_ts = None

    for evt in events:
        event_type = evt['event_type']
        event_data = json.loads(evt['event_data']) if isinstance(evt['event_data'], str) else evt['event_data']
        timestamp = event_data.get('timestamp')

        # Stale-event guard for disconnect events
        if event_type == "GatewayDisconnected":
            event_session_id = event_data.get('session_id')
            if event_session_id is not None:
                # New format: session_id present — skip if it doesn't match last connect
                if last_connected_session_id is not None and event_session_id != last_connected_session_id:
                    gateway_doc['version'] = evt['version']
                    continue
            else:
                # Old format: no session_id — fall back to timestamp comparison
                event_ts = _parse_ts(timestamp)
                if last_status_ts is not None and event_ts is not None and event_ts < last_status_ts:
                    gateway_doc['version'] = evt['version']
                    continue
                if event_ts is not None:
                    last_status_ts = event_ts
        elif event_type in STATUS_EVENTS:
            event_ts = _parse_ts(timestamp)
            if event_ts is not None:
                last_status_ts = event_ts

        if event_type == "GatewayCreated":
            gateway_doc.update({
                "name": event_data.get('name'),
                "location": event_data.get('location'),
                "status": "created",
                "created_at": timestamp,
                "last_updated": timestamp
            })

        elif event_type == "GatewayConnected":
            last_connected_session_id = event_data.get('session_id')
            gateway_doc.update({
                "status": "connected",
                "connected_at": timestamp,
                "last_updated": timestamp
            })
            if 'certificate_id' in event_data:
                gateway_doc['certificate_info'] = {
                    "certificate_id": event_data['certificate_id'],
                    "status": "installed"
                }
            if 'certificate_info' in event_data:
                gateway_doc['certificate_info'] = event_data['certificate_info']

        elif event_type == "GatewayDisconnected":
            gateway_doc.update({
                "status": "disconnected",
                "disconnected_at": timestamp,
                "last_updated": timestamp
            })
            if 'error' in event_data:
                error = event_data['error']
                if not isinstance(error, str) or not error.startswith('{'):
                    error = json.dumps({"message": error})
                gateway_doc['error'] = error

        elif event_type == "GatewayDeleted":
            gateway_doc.update({
                "status": "deleted",
                "deleted_at": timestamp,
                "last_updated": timestamp
            })

        elif event_type == "GatewayUpdated":
            gateway_doc['last_updated'] = timestamp
            update_type = event_data.get('update_type', 'status')
            payload = event_data.get('payload', {})

            if update_type in ('heartbeat', 'HEARTBEAT'):
                gateway_doc['last_heartbeat'] = timestamp

            if isinstance(payload, dict):
                for field in ('uptime', 'health', 'cpu', 'memory'):
                    if field in payload:
                        gateway_doc[field] = payload[field]

        gateway_doc['version'] = evt['version']

    document_json = json.dumps(gateway_doc)

    upsert_response = http.request(
        'POST',
        f"{DB_ENDPOINT}/execute",
        body=json.dumps({
            "command": """
                INSERT INTO gateways_docs (gateway_id, document)
                VALUES (?, ?)
                ON CONFLICT(gateway_id) DO UPDATE SET document = excluded.document
            """,
            "params": [gateway_id, document_json]
        }),
        headers={'Content-Type': 'application/json'}
    )

    if upsert_response.status != 200:
        raise Exception(f"Failed to upsert gateway document: {upsert_response.data.decode('utf-8')}")

    return {
        "statusCode": 200,
        "updated": True,
        "gateway_state": gateway_doc
    }
