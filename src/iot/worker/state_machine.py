import sqlite3
import json
import logging
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
from database import event_store

logger = logging.getLogger(__name__)

class GatewayState(str, Enum):
    """Gateway states for the streamlined state machine"""
    CREATED = "created"       # Initial state when gateway is registered
    CONNECTED = "connected"   # Gateway is online with certificates installed
    DISCONNECTED = "disconnected"  # Gateway is offline but still registered
    DELETED = "deleted"       # Gateway has been deleted (terminal state)

class GatewayUpdateType(str, Enum):
    """Types of gateway updates"""
    HEARTBEAT = "heartbeat"
    STATUS = "status"

class EventType(str, Enum):
    """Event types for the event store"""
    GATEWAY_CREATED = "GatewayCreated"             # Gateway registered in the system
    GATEWAY_CONNECTED = "GatewayConnected"         # Gateway connected to MQTT broker
    GATEWAY_DISCONNECTED = "GatewayDisconnected"   # Gateway disconnected from MQTT broker
    GATEWAY_DELETED = "GatewayDeleted"             # Gateway has been deleted
    GATEWAY_UPDATED = "GatewayUpdated"             # General updates (heartbeats, status)

class GatewayStateError(Exception):
    """Exception raised for invalid state transitions"""
    pass

class GatewayStateMachine:
    """State machine for managing gateway lifecycle using event sourcing"""
    
    AGGREGATE_TYPE = "gateway"
    
    def __init__(self):
        """Initialize with default state"""
        self.current_state = GatewayState.CREATED
        self.data = {}
        self.version = -1
    
    def apply(self, event: Dict[str, Any]) -> None:
        """Apply an event to update the state machine
        
        Args:
            event: The event object containing event_type and event_data
        """
        event_type = event["event_type"]
        
        # Handle the event data, which might be a JSON string or already parsed dict
        event_data = event["event_data"]
        if isinstance(event_data, str):
            event_data = json.loads(event_data)
        
        logger.info(f"Applying {event_type} event to gateway {event_data.get('gateway_id', 'unknown')}")
        
        # Update version with each event
        self.version += 1
        
        # Update state based on event type
        if event_type == EventType.GATEWAY_CREATED:
            self._handle_gateway_created(event_data)
        elif event_type == EventType.GATEWAY_CONNECTED:
            self._handle_gateway_connected(event_data)
        elif event_type == EventType.GATEWAY_DISCONNECTED:
            self._handle_gateway_disconnected(event_data)
        elif event_type == EventType.GATEWAY_DELETED:
            self._handle_gateway_deleted(event_data)
        elif event_type == EventType.GATEWAY_UPDATED:
            self._handle_gateway_update(event_data)
        else:
            logger.warning(f"Unknown event type: {event_type}")
    
    def _handle_gateway_created(self, event_data: Dict[str, Any]) -> None:
        """Handle gateway creation event - initial registration"""
        self.data.update(event_data)
        self.current_state = GatewayState.CREATED
        timestamp = event_data.get("timestamp", datetime.now().isoformat())
        self.data["created_at"] = timestamp
        self.data["last_updated"] = timestamp  # Set last_updated for creation event
        logger.info(f"Gateway {event_data.get('gateway_id')} created in CREATED state")
    
    def _handle_gateway_connected(self, event_data: Dict[str, Any]) -> None:
        """Handle gateway connection event - gateway is online with certificates"""
        if self.current_state in [GatewayState.CREATED, GatewayState.DISCONNECTED]:
            self.current_state = GatewayState.CONNECTED
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["connected_at"] = timestamp
            self.data["last_updated"] = timestamp  # Set last_updated for connection event
            
            # Store certificate info if available
            if "certificate_info" in event_data:
                self.data["certificate_info"] = event_data["certificate_info"]
                
            # Clear any previous error
            self.data.pop("error", None)
            
            logger.info(f"Gateway {event_data.get('gateway_id')} transitioned to CONNECTED state")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to CONNECTED")
    
    def _handle_gateway_disconnected(self, event_data: Dict[str, Any]) -> None:
        """Handle gateway disconnection event with streamlined error format."""
        if self.current_state == GatewayState.CONNECTED:
            self.current_state = GatewayState.DISCONNECTED
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            
            # Format timestamp to not include microseconds
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp)
                    timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except:
                    pass
                    
            # Set both disconnected_at and last_updated timestamps
            self.data["disconnected_at"] = timestamp
            self.data["last_updated"] = timestamp  # Make sure to update this for the latest activity
            
            # Standardize error format
            if "error" in event_data:
                if "offline" in str(event_data["error"]).lower():
                    self.data["error"] = json.dumps({"status": "reported offline"})
                else:
                    self.data["error"] = json.dumps({"message": event_data["error"]})
            else:
                self.data["error"] = json.dumps({"status": "reported offline"})
                    
            logger.info(f"Gateway {event_data.get('gateway_id')} transitioned to DISCONNECTED state")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to DISCONNECTED")
    
    def _handle_gateway_deleted(self, event_data: Dict[str, Any]) -> None:
        """Handle gateway deletion event - gateway is being removed"""
        # Can transition to DELETED from any state except DELETED
        if self.current_state != GatewayState.DELETED:
            self.current_state = GatewayState.DELETED
            self.data.update(event_data)
            self.data["deleted_at"] = event_data.get("timestamp", datetime.now().isoformat())
            
            # Store deletion reason if available
            if "reason" in event_data:
                self.data["deletion_reason"] = event_data["reason"]
                
            logger.info(f"Gateway {event_data.get('gateway_id')} transitioned to DELETED state")
        else:
            logger.warning(f"Gateway already in DELETED state, ignoring delete event")
    
    def _handle_gateway_update(self, event_data: Dict[str, Any]) -> None:
        """Handle gateway update events (heartbeats, status updates)"""
        # Common timestamp update
        timestamp = event_data.get("timestamp", datetime.now().isoformat())
        self.data["last_updated"] = timestamp
        
        update_type = event_data.get("update_type", GatewayUpdateType.STATUS)
        payload = event_data.get("payload", {})
        gateway_id = event_data.get("gateway_id", "unknown")
        
        # Update container ID if present
        if "container_id" in event_data:
            self.data["container_id"] = event_data["container_id"]
        
        # Process metrics from payload regardless of type
        if isinstance(payload, dict):
            # Update basic metrics
            for metric in ["uptime", "memory", "cpu", "health"]:
                if metric in payload:
                    self.data[metric] = payload[metric]
            
            # Update name and location if provided
            if "name" in payload:
                self.data["name"] = payload["name"]
            if "location" in payload:
                self.data["location"] = payload["location"]
        
        # Specific behavior based on update type
        if update_type == GatewayUpdateType.HEARTBEAT:
            logger.info(f"Processing heartbeat for gateway {gateway_id} (current state: {self.current_state})")
            self.data["last_heartbeat"] = timestamp
            
            # Handle reconnection logic
            if self.current_state == GatewayState.DISCONNECTED:
                logger.info(f"Gateway {gateway_id} reconnected based on heartbeat")
                self.current_state = GatewayState.CONNECTED
                self.data.pop("error", None)
                self.data["connected_at"] = timestamp
                logger.info(f"Gateway {gateway_id} transitioned to CONNECTED state from heartbeat")
        
        elif update_type == GatewayUpdateType.STATUS:
            logger.info(f"Processing status update for gateway {gateway_id} (current state: {self.current_state})")
            
            # Handle certificate status
            if isinstance(payload, dict) and "certificate_status" in payload:
                cert_status = payload["certificate_status"]
                
                if cert_status == "installed":
                    logger.info(f"Gateway {gateway_id} has certificates installed")
                    self.data["certificate_info"] = {
                        "status": "installed",
                        "installed_at": timestamp
                    }
                elif cert_status == "removed":
                    logger.info(f"Gateway {gateway_id} certificates removed")
                    self.data["certificate_info"] = {
                        "status": "removed",
                        "removed_at": timestamp
                    }
                    
                    # If certificates are removed and we're connected, disconnect
                    if self.current_state == GatewayState.CONNECTED:
                        self.current_state = GatewayState.DISCONNECTED
                        self.data["error"] = "Certificate removed"
                        self.data["disconnected_at"] = timestamp
                        logger.info(f"Gateway {gateway_id} transitioned to DISCONNECTED state due to certificate removal")
            
            # Handle online/offline status
            if isinstance(payload, dict) and "status" in payload:
                status = payload["status"]
                
                # Handle online status - potential transition to CONNECTED
                if status == "online":
                    # Only transition if we have certificates installed
                    if self.current_state in [GatewayState.CREATED, GatewayState.DISCONNECTED]:
                        has_certificates = (
                            self.data.get("certificate_info", {}).get("status") == "installed" 
                            or (isinstance(payload, dict) and payload.get("certificate_status") == "installed")
                        )
                        
                        if has_certificates:
                            self.current_state = GatewayState.CONNECTED
                            self.data.pop("error", None)
                            self.data["connected_at"] = timestamp
                            logger.info(f"Gateway {gateway_id} transitioned to CONNECTED state from online status")
                        else:
                            logger.warning(f"Gateway {gateway_id} reported online but has no certificates")
                
                # Handle offline status - transition to DISCONNECTED
                elif status == "offline":
                    if self.current_state == GatewayState.CONNECTED:
                        self.current_state = GatewayState.DISCONNECTED
                        self.data["error"] = "Gateway reported offline status"
                        self.data["disconnected_at"] = timestamp
                        logger.info(f"Gateway {gateway_id} transitioned to DISCONNECTED state from offline status")
                
                # Handle deleted status - transition to DELETED
                elif status == "deleted":
                    if self.current_state != GatewayState.DELETED:
                        self.current_state = GatewayState.DELETED
                        self.data["deleted_at"] = timestamp
                        self.data["deletion_reason"] = payload.get("reason", "Gateway reported deleted status")
                        logger.info(f"Gateway {gateway_id} transitioned to DELETED state from status update")
    
    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the gateway"""
        # Use last_updated directly if it exists, otherwise use current time
        # DO NOT use self.data.get("timestamp") as it may not be the latest activity
        last_updated = self.data.get("last_updated", datetime.now().isoformat())
        
        status = {
            "gateway_id": self.data.get("gateway_id", ""),
            "name": self.data.get("name", "Unnamed Gateway"),
            "location": self.data.get("location", "Unknown"),
            "status": self.current_state.value,
            "last_updated": last_updated,  # Use the correct last_updated field
            "last_heartbeat": self.data.get("last_heartbeat"),
            "container_id": self.data.get("container_id"),
            "health": self.data.get("health"),
            "uptime": self.data.get("uptime"),
            "version": self.version,
            "error": self.data.get("error")
        }
        
        # Add certificate information if available
        if "certificate_info" in self.data:
            status["certificate_info"] = self.data["certificate_info"]
        
        # Add timestamps for state transitions
        if self.current_state == GatewayState.CONNECTED and "connected_at" in self.data:
            status["connected_at"] = self.data["connected_at"]
        elif self.current_state == GatewayState.DISCONNECTED and "disconnected_at" in self.data:
            status["disconnected_at"] = self.data["disconnected_at"]
        elif self.current_state == GatewayState.DELETED and "deleted_at" in self.data:
            status["deleted_at"] = self.data["deleted_at"]
            status["deletion_reason"] = self.data.get("deletion_reason")
        
        return status
    
    @staticmethod
    def create_event(gateway_id: str, event_type: EventType, event_data: Dict[str, Any], version: int) -> Dict[str, Any]:
        """Create a new event object
        
        Args:
            gateway_id: ID of the gateway
            event_type: Type of event
            event_data: Event data
            version: Version of the aggregate after this event
            
        Returns:
            Event object
        """
        # Ensure gateway_id is in the event data
        if "gateway_id" not in event_data:
            event_data["gateway_id"] = gateway_id
            
        # Add timestamp if not present
        if "timestamp" not in event_data:
            event_data["timestamp"] = datetime.now().isoformat()
            
        return {
            "aggregate_id": gateway_id,
            "aggregate_type": GatewayStateMachine.AGGREGATE_TYPE,
            "event_type": event_type,
            "event_data": event_data,
            "version": version,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def initialize_gateway_tables(db_path: str = "recycling.db") -> None:
        """Initialize gateway-specific tables in the database or migrate existing tables."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check if gateways table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gateways'")
            table_exists = cursor.fetchone() is not None
            
            if table_exists:
                # Table exists, check columns and add missing ones
                logger.info("Gateways table exists, checking for missing columns...")
                
                # Get current columns
                cursor.execute("PRAGMA table_info(gateways)")
                existing_columns = [row[1] for row in cursor.fetchall()]
                logger.info(f"Existing columns: {existing_columns}")
                
                # Define new columns that might need to be added
                new_columns = {
                    "created_at": "TEXT",
                    "connected_at": "TEXT",
                    "disconnected_at": "TEXT",
                    "deleted_at": "TEXT",
                    "certificate_info": "TEXT"
                }
                
                # Add any missing columns
                for col_name, col_type in new_columns.items():
                    if col_name not in existing_columns:
                        logger.info(f"Adding missing column: {col_name}")
                        cursor.execute(f"ALTER TABLE gateways ADD COLUMN {col_name} {col_type}")
                
                conn.commit()
                logger.info("Gateway table migration completed successfully")
                
            else:
                # Create gateways table with streamlined schema
                logger.info("Creating gateways table with streamlined schema...")
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS gateways (
                        gateway_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        location TEXT NOT NULL,
                        status TEXT NOT NULL,
                        last_updated TEXT,
                        last_heartbeat TEXT,
                        uptime TEXT,
                        health TEXT,
                        error TEXT,
                        created_at TEXT,
                        connected_at TEXT,
                        disconnected_at TEXT,
                        deleted_at TEXT,
                        certificate_info TEXT
                    )
                ''')
                
                conn.commit()
                logger.info("Gateway tables initialized with streamlined schema")
            
        except Exception as e:
            logger.error(f"Error initializing gateway tables: {str(e)}")
            raise
        finally:
            conn.close()
    
    @classmethod
    def reconstruct_from_events(cls, gateway_id: str, db_path: str = "recycling.db") -> "GatewayStateMachine":
        """Reconstruct a gateway state machine from events.
        
        Args:
            gateway_id: ID of the gateway
            db_path: Path to the database
            
        Returns:
            Reconstructed gateway state machine
        """
        state_machine = cls()
        
        # Read events from the event store
        events = event_store.read_events(
            aggregate_id=gateway_id,
            aggregate_type=cls.AGGREGATE_TYPE,
            db_path=db_path
        )
        
        # Apply events to the state machine
        for event in events:
            state_machine.apply(event)
        
        return state_machine
    
    @staticmethod
    def update_gateway_read_model(
        gateway_id: str,
        name: str,
        location: str,
        status: str,
        last_updated: str,
        last_heartbeat: Optional[str] = None,
        uptime: Optional[str] = None,
        health: Optional[str] = None,
        error: Optional[str] = None,
        created_at: Optional[str] = None,
        connected_at: Optional[str] = None,
        disconnected_at: Optional[str] = None,
        deleted_at: Optional[str] = None,
        certificate_info: Optional[Dict[str, Any]] = None,
        db_path: str = "recycling.db"
    ) -> None:
        """Update the gateway read model with the streamlined schema."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            logger.debug(f"Updating read model for gateway {gateway_id} with status={status}")
            
            # Format timestamps to YYYY-MM-DDThh:mm:ss format without microseconds
            def format_timestamp(ts):
                if not ts:
                    return ts
                try:
                    dt = datetime.fromisoformat(ts)
                    return dt.strftime("%Y-%m-%dT%H:%M:%S")
                except:
                    return ts
            
            last_updated = format_timestamp(last_updated)
            last_heartbeat = format_timestamp(last_heartbeat)
            created_at = format_timestamp(created_at)
            connected_at = format_timestamp(connected_at)
            disconnected_at = format_timestamp(disconnected_at)
            deleted_at = format_timestamp(deleted_at)
            
            # Convert certificate_info to JSON string if it's a dict
            cert_info_json = None
            if certificate_info is not None:
                # Update installed_at timestamp format if present
                if isinstance(certificate_info, dict) and "installed_at" in certificate_info:
                    certificate_info["installed_at"] = format_timestamp(certificate_info["installed_at"])
                cert_info_json = json.dumps(certificate_info)
            
            # Format error as JSON if it's not already
            if error and not error.startswith("{"):
                if "offline" in error.lower():
                    error = json.dumps({"status": "reported offline"})
                else:
                    error = json.dumps({"message": error})
            
            cursor.execute('''
                INSERT OR REPLACE INTO gateways
                (gateway_id, name, location, status, last_updated, last_heartbeat,
                uptime, health, error, created_at, connected_at, disconnected_at,
                deleted_at, certificate_info)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                gateway_id, name, location, status, last_updated, last_heartbeat,
                uptime, health, error, created_at, connected_at, disconnected_at,
                deleted_at, cert_info_json
            ))
            
            conn.commit()
            logger.info(f"Read model updated for gateway {gateway_id} with status={status}")
        except Exception as e:
            logger.error(f"Error updating read model: {str(e)}")
            raise
        finally:
            conn.close()
    
    @staticmethod
    def get_gateway_status(
        gateway_id: str,
        db_path: str = "recycling.db"
    ) -> Optional[Dict[str, Any]]:
        """Get the current status of a gateway from the read model.
        
        Args:
            gateway_id: ID of the gateway
            db_path: Path to the database
            
        Returns:
            Current status of the gateway or None if not found
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM gateways
                WHERE gateway_id = ?
            ''', (gateway_id,))
            
            row = cursor.fetchone()
            if row:
                gateway_dict = dict(row)
                
                # Parse certificate_info from JSON if it exists
                if gateway_dict.get('certificate_info'):
                    try:
                        gateway_dict['certificate_info'] = json.loads(gateway_dict['certificate_info'])
                    except:
                        # If there's a parsing error, set to None
                        gateway_dict['certificate_info'] = None
                
                return gateway_dict
            return None
        except Exception as e:
            logger.error(f"Error getting gateway status: {str(e)}")
            return None
        finally:
            conn.close()
    
    @staticmethod
    def list_gateways(
        db_path: str = "recycling.db",
        include_deleted: bool = False
    ) -> List[Dict[str, Any]]:
        """List all gateways from the read model.
        
        Args:
            db_path: Path to the database
            include_deleted: Whether to include deleted gateways
            
        Returns:
            List of all gateways
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if include_deleted:
                cursor.execute("SELECT * FROM gateways")
            else:
                cursor.execute("SELECT * FROM gateways WHERE status != ?", (GatewayState.DELETED.value,))
            
            gateways = []
            for row in cursor.fetchall():
                gateway_dict = dict(row)
                
                # Parse certificate_info from JSON if it exists
                if gateway_dict.get('certificate_info'):
                    try:
                        gateway_dict['certificate_info'] = json.loads(gateway_dict['certificate_info'])
                    except:
                        # If there's a parsing error, set to None
                        gateway_dict['certificate_info'] = None
                
                gateways.append(gateway_dict)
            
            return gateways
        except Exception as e:
            logger.error(f"Error listing gateways: {str(e)}")
            return []
        finally:
            conn.close()