import json
import urllib3
import os
from datetime import datetime, timezone

# DB_ENDPOINT should include the http:// prefix
DB_ENDPOINT = os.environ.get('DB_ENDPOINT', 'http://172.31.x.x:8080')

http = urllib3.PoolManager()


def _parse_ts(ts_str):
    """Parse ISO timestamp string to UTC datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_stale_disconnect(event_data):
    """
    Return True if this GatewayDisconnected event is a stale MQTT LWT that
    arrived after the gateway already reconnected.

    Queries the current read-model document and compares the event timestamp
    against connected_at. If event_ts <= connected_at the gateway has already
    reconnected and this disconnect should be ignored.
    """
    gateway_id = event_data.get('gateway_id')
    event_ts = _parse_ts(event_data.get('timestamp'))
    if not gateway_id or not event_ts:
        return False

    try:
        resp = http.request(
            'POST',
            f"{DB_ENDPOINT}/query",
            body=json.dumps({
                "query": "SELECT document FROM gateways_docs WHERE gateway_id = ?",
                "params": [gateway_id]
            }),
            headers={'Content-Type': 'application/json'}
        )
        data = json.loads(resp.data.decode('utf-8'))
        results = data.get('results', [])
        if not results:
            return False

        doc = results[0].get('document')
        if isinstance(doc, str):
            doc = json.loads(doc)

        connected_at = _parse_ts(doc.get('connected_at'))
        if connected_at and event_ts <= connected_at:
            return True
    except Exception:
        pass

    return False


def lambda_handler(event, context):
    """
    Write event to event store (matches local event_store.py schema).
    Auto-calculates version for new aggregates or validates for existing ones.

    Stale-disconnect guard: for GatewayDisconnected events, if the event
    timestamp is not newer than the gateway's current connected_at the event
    is skipped. This prevents delayed MQTT LWT messages from overwriting a
    reconnected gateway's status with 'disconnected'.
    """
    aggregate_id = event['aggregate_id']
    aggregate_type = event['aggregate_type']
    event_type = event['event_type']
    event_data = event['event_data']
    expected_version = event.get('version')  # Optional

    # Guard: skip stale disconnect caused by delayed LWT delivery
    if event_type == 'GatewayDisconnected' and _is_stale_disconnect(event_data):
        version_resp = http.request(
            'POST',
            f"{DB_ENDPOINT}/query",
            body=json.dumps({
                "query": """
                    SELECT MAX(version) as current_version
                    FROM events
                    WHERE aggregate_id = ? AND aggregate_type = ?
                """,
                "params": [aggregate_id, aggregate_type]
            }),
            headers={'Content-Type': 'application/json'}
        )
        version_data = json.loads(version_resp.data.decode('utf-8'))
        version_results = version_data.get('results', [{}])
        current_version = version_results[0].get('current_version') or 0
        return {
            "statusCode": 200,
            "version": current_version,
            "event_id": -1,
            "skipped": True,
            "reason": "stale_disconnect"
        }

    # Check current version
    check_response = http.request(
        'POST',
        f"{DB_ENDPOINT}/query",
        body=json.dumps({
            "query": """
                SELECT MAX(version) as current_version
                FROM events
                WHERE aggregate_id = ? AND aggregate_type = ?
            """,
            "params": [aggregate_id, aggregate_type]
        }),
        headers={'Content-Type': 'application/json'}
    )

    data = json.loads(check_response.data.decode('utf-8'))
    results = data.get('results', [{}])
    current_version = results[0].get('current_version') if results else None

    if current_version is None:
        current_version = -1

    # If version provided, validate it (optimistic locking)
    # If not provided, auto-increment (for MQTT-triggered events)
    if expected_version is not None:
        if current_version != expected_version:
            raise Exception(
                f"Concurrency conflict: expected version {expected_version}, "
                f"current version {current_version}"
            )

    new_version = current_version + 1

    # Insert event (matches local schema: id auto-increment, timestamp field)
    insert_response = http.request(
        'POST',
        f"{DB_ENDPOINT}/execute",
        body=json.dumps({
            "command": """
                INSERT INTO events (
                    aggregate_id, aggregate_type, event_type,
                    event_data, version, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            "params": [
                aggregate_id,
                aggregate_type,
                event_type,
                json.dumps(event_data),
                new_version,
                datetime.utcnow().isoformat()
            ]
        }),
        headers={'Content-Type': 'application/json'}
    )

    if insert_response.status != 200:
        raise Exception(f"Failed to insert event: {insert_response.data.decode('utf-8')}")

    response_data = json.loads(insert_response.data.decode('utf-8'))
    event_id = response_data.get('lastrowid', new_version)

    return {
        "statusCode": 200,
        "version": new_version,
        "event_id": event_id
    }
