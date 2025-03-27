import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def init_event_store(db_path: str = "recycling.db") -> None:
    """Initialize event store tables in the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create events table for event sourcing
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        logger.info(f"Event store initialized in database: {db_path}")
    except Exception as e:
        logger.error(f"Error initializing event store: {str(e)}")
        raise
    finally:
        conn.close()

def append_event(
    aggregate_id: str,
    aggregate_type: str,
    event_type: str,
    event_data: Dict[str, Any],
    version: int,
    db_path: str = "recycling.db"
) -> int:
    """Append an event to the event store.
    
    Args:
        aggregate_id: ID of the aggregate (e.g., gateway_id)
        aggregate_type: Type of the aggregate (e.g., "gateway")
        event_type: Type of the event
        event_data: Data for the event
        version: Version of the aggregate after this event
        db_path: Path to the database
        
    Returns:
        ID of the created event
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Convert event_data to JSON if it's not already a string
        if not isinstance(event_data, str):
            event_data = json.dumps(event_data)
        
        timestamp = datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO events 
            (aggregate_id, aggregate_type, event_type, event_data, version, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (aggregate_id, aggregate_type, event_type, event_data, version, timestamp))
        
        event_id = cursor.lastrowid
        conn.commit()
        
        logger.info(f"Event {event_type} (v{version}) appended for {aggregate_type} {aggregate_id}")
        return event_id
    except Exception as e:
        logger.error(f"Error appending event: {str(e)}")
        raise
    finally:
        conn.close()

def read_events(
    aggregate_id: str,
    aggregate_type: Optional[str] = None,
    db_path: str = "recycling.db"
) -> List[Dict[str, Any]]:
    """Read all events for an aggregate.
    
    Args:
        aggregate_id: ID of the aggregate
        aggregate_type: Type of the aggregate (optional filter)
        db_path: Path to the database
        
    Returns:
        List of events for the aggregate
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if aggregate_type:
            cursor.execute('''
                SELECT id, aggregate_id, aggregate_type, event_type, event_data, version, timestamp
                FROM events
                WHERE aggregate_id = ? AND aggregate_type = ?
                ORDER BY version
            ''', (aggregate_id, aggregate_type))
        else:
            cursor.execute('''
                SELECT id, aggregate_id, aggregate_type, event_type, event_data, version, timestamp
                FROM events
                WHERE aggregate_id = ?
                ORDER BY version
            ''', (aggregate_id,))
        
        events = []
        for row in cursor.fetchall():
            event = dict(row)
            # Parse event_data from JSON
            if isinstance(event['event_data'], str):
                try:
                    event['event_data'] = json.loads(event['event_data'])
                except json.JSONDecodeError:
                    # If it's not valid JSON, keep it as is
                    pass
            events.append(event)
        
        return events
    except Exception as e:
        logger.error(f"Error reading events: {str(e)}")
        return []
    finally:
        conn.close()

def get_current_version(
    aggregate_id: str,
    aggregate_type: Optional[str] = None,
    db_path: str = "recycling.db"
) -> int:
    """Get the current version of an aggregate.
    
    Args:
        aggregate_id: ID of the aggregate
        aggregate_type: Type of the aggregate (optional filter)
        db_path: Path to the database
        
    Returns:
        Current version of the aggregate or -1 if not found
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        if aggregate_type:
            cursor.execute('''
                SELECT MAX(version)
                FROM events
                WHERE aggregate_id = ? AND aggregate_type = ?
            ''', (aggregate_id, aggregate_type))
        else:
            cursor.execute('''
                SELECT MAX(version)
                FROM events
                WHERE aggregate_id = ?
            ''', (aggregate_id,))
        
        result = cursor.fetchone()
        return result[0] if result[0] is not None else -1
    except Exception as e:
        logger.error(f"Error getting current version: {str(e)}")
        return -1
    finally:
        conn.close()