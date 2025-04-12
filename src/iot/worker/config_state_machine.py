import sqlite3
import json
import logging
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
from database import event_store

logger = logging.getLogger(__name__)

class ConfigUpdateState(str, Enum):
    """Configuration update states"""
    IDLE = "idle"                         # Initial state, no active config update
    CONFIGURATION_STORED = "stored"       # Config stored in DB, not yet published
    WAITING_FOR_REQUEST = "waiting"       # Waiting for gateway to request config
    NOTIFYING_GATEWAY = "notifying"       # Sending config to gateway
    WAITING_FOR_ACK = "waiting_ack"       # Waiting for gateway acknowledgment
    UPDATE_COMPLETED = "completed"        # Config update completed successfully
    UPDATE_FAILED = "failed"              # Config update failed

class ConfigEventType(str, Enum):
    """Event types for the configuration update process"""
    CONFIG_CREATED = "ConfigCreated"           # New configuration created
    CONFIG_PUBLISHED = "ConfigPublished"       # Backend published to config/new
    CONFIG_REQUESTED = "ConfigRequested"       # Gateway requested configuration
    CONFIG_SENT = "ConfigSent"                 # Configuration sent to gateway
    CONFIG_DELIVERED = "ConfigDelivered"       # Gateway acknowledged delivery
    CONFIG_COMPLETED = "ConfigCompleted"       # Rules engine confirmed completion
    CONFIG_FAILED = "ConfigFailed"             # Configuration update failed

class ConfigUpdateStateMachine:
    """State machine for managing end device configuration updates"""
    
    AGGREGATE_TYPE = "config_update"
    
    def __init__(self):
        """Initialize with default state"""
        self.current_state = ConfigUpdateState.IDLE
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
        
        logger.info(f"Applying {event_type} event to config update {event_data.get('update_id', 'unknown')}")
        
        # Update version with each event
        self.version += 1
        
        # Update state based on event type
        if event_type == ConfigEventType.CONFIG_CREATED:
            self._handle_config_created(event_data)
        elif event_type == ConfigEventType.CONFIG_PUBLISHED:
            self._handle_config_published(event_data)
        elif event_type == ConfigEventType.CONFIG_REQUESTED:
            self._handle_config_requested(event_data)
        elif event_type == ConfigEventType.CONFIG_SENT:
            self._handle_config_sent(event_data)
        elif event_type == ConfigEventType.CONFIG_DELIVERED:
            self._handle_config_delivered(event_data)
        elif event_type == ConfigEventType.CONFIG_COMPLETED:
            self._handle_config_completed(event_data)
        elif event_type == ConfigEventType.CONFIG_FAILED:
            self._handle_config_failed(event_data)
        else:
            logger.warning(f"Unknown event type: {event_type}")
    
    def _handle_config_created(self, event_data: Dict[str, Any]) -> None:
        """Handle config creation event"""
        self.data.update(event_data)
        self.current_state = ConfigUpdateState.CONFIGURATION_STORED
        timestamp = event_data.get("timestamp", datetime.now().isoformat())
        self.data["created_at"] = timestamp
        self.data["last_updated"] = timestamp
        logger.info(f"Configuration created for gateway {event_data.get('gateway_id')}, update_id: {event_data.get('update_id')}")
    
    def _handle_config_published(self, event_data: Dict[str, Any]) -> None:
        """Handle config published event"""
        if self.current_state == ConfigUpdateState.CONFIGURATION_STORED:
            self.current_state = ConfigUpdateState.WAITING_FOR_REQUEST
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["published_at"] = timestamp
            self.data["last_updated"] = timestamp
            logger.info(f"Configuration published to MQTT for gateway {self.data.get('gateway_id')}")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to WAITING_FOR_REQUEST")
    
    def _handle_config_requested(self, event_data: Dict[str, Any]) -> None:
        """Handle config requested event"""
        if self.current_state == ConfigUpdateState.WAITING_FOR_REQUEST:
            self.current_state = ConfigUpdateState.NOTIFYING_GATEWAY
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["requested_at"] = timestamp
            self.data["last_updated"] = timestamp
            logger.info(f"Configuration requested by gateway {self.data.get('gateway_id')}")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to NOTIFYING_GATEWAY")
    
    def _handle_config_sent(self, event_data: Dict[str, Any]) -> None:
        """Handle config sent event"""
        if self.current_state == ConfigUpdateState.NOTIFYING_GATEWAY:
            self.current_state = ConfigUpdateState.WAITING_FOR_ACK
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["sent_at"] = timestamp
            self.data["last_updated"] = timestamp
            logger.info(f"Configuration sent to gateway {self.data.get('gateway_id')}")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to WAITING_FOR_ACK")
    
    def _handle_config_delivered(self, event_data: Dict[str, Any]) -> None:
        """Handle config delivered event"""
        if self.current_state == ConfigUpdateState.WAITING_FOR_ACK:
            self.current_state = ConfigUpdateState.UPDATE_COMPLETED
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["delivered_at"] = timestamp
            self.data["last_updated"] = timestamp
            self.data["delivery_status"] = event_data.get("status", "success")
            logger.info(f"Configuration delivery acknowledged by gateway {self.data.get('gateway_id')}")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to UPDATE_COMPLETED")
    
    def _handle_config_completed(self, event_data: Dict[str, Any]) -> None:
        """Handle config completed event"""
        if self.current_state == ConfigUpdateState.UPDATE_COMPLETED:
            # Stay in the same state, just update metadata
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["completed_at"] = timestamp
            self.data["last_updated"] = timestamp
            logger.info(f"Configuration update completed for gateway {self.data.get('gateway_id')}")
        else:
            logger.warning(f"Invalid state transition - completion notification received in state {self.current_state}")
    
    def _handle_config_failed(self, event_data: Dict[str, Any]) -> None:
        """Handle config failed event"""
        # Can transition to FAILED from any state except IDLE and COMPLETED
        if self.current_state not in [ConfigUpdateState.IDLE, ConfigUpdateState.UPDATE_COMPLETED]:
            self.current_state = ConfigUpdateState.UPDATE_FAILED
            self.data.update(event_data)
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            self.data["failed_at"] = timestamp
            self.data["last_updated"] = timestamp
            self.data["error"] = event_data.get("error", "Unknown error")
            logger.info(f"Configuration update failed for gateway {self.data.get('gateway_id')}: {self.data.get('error')}")
        else:
            logger.warning(f"Invalid state transition from {self.current_state} to UPDATE_FAILED")
    
    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the configuration update"""
        status = {
            "update_id": self.data.get("update_id", ""),
            "gateway_id": self.data.get("gateway_id", ""),
            "state": self.current_state.value,
            "version": self.data.get("version", ""),
            "created_at": self.data.get("created_at"),
            "last_updated": self.data.get("last_updated"),
            "yaml_config": self.data.get("yaml_config"),  # This could be large, consider excluding
            "version": self.version
        }
        
        # Add timestamps for state transitions
        for ts_field in ["published_at", "requested_at", "sent_at", "delivered_at", "completed_at", "failed_at"]:
            if ts_field in self.data:
                status[ts_field] = self.data[ts_field]
        
        # Add error info if available
        if "error" in self.data:
            status["error"] = self.data["error"]
        
        # Add delivery status if available
        if "delivery_status" in self.data:
            status["delivery_status"] = self.data["delivery_status"]
        
        return status
    
    @staticmethod
    def create_event(update_id: str, event_type: ConfigEventType, event_data: Dict[str, Any], version: int) -> Dict[str, Any]:
        """Create a new event object
        
        Args:
            update_id: ID of the configuration update
            event_type: Type of event
            event_data: Event data
            version: Version of the aggregate after this event
            
        Returns:
            Event object
        """
        # Ensure update_id is in the event data
        if "update_id" not in event_data:
            event_data["update_id"] = update_id
            
        # Add timestamp if not present
        if "timestamp" not in event_data:
            event_data["timestamp"] = datetime.now().isoformat()
            
        return {
            "aggregate_id": update_id,
            "aggregate_type": ConfigUpdateStateMachine.AGGREGATE_TYPE,
            "event_type": event_type,
            "event_data": event_data,
            "version": version,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def initialize_config_tables(db_path: str = "recycling.db") -> None:
        """Initialize configuration-specific tables in the database."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check if config_updates table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='config_updates'")
            table_exists = cursor.fetchone() is not None
            
            if not table_exists:
                # Create config_updates table with schema
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS config_updates (
                        update_id TEXT PRIMARY KEY,
                        gateway_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        version TEXT,
                        created_at TEXT,
                        published_at TEXT,
                        requested_at TEXT,
                        sent_at TEXT,
                        delivered_at TEXT,
                        completed_at TEXT,
                        failed_at TEXT,
                        last_updated TEXT,
                        delivery_status TEXT,
                        error TEXT,
                        config_hash TEXT
                    )
                ''')
                
                # Create configs table to store actual configuration YAML
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS configs (
                        config_hash TEXT PRIMARY KEY,
                        yaml_config TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                ''')
                
                conn.commit()
                logger.info("Configuration tables initialized")
            
        except Exception as e:
            logger.error(f"Error initializing configuration tables: {str(e)}")
            raise
        finally:
            conn.close()
    
    @classmethod
    def reconstruct_from_events(cls, update_id: str, db_path: str = "recycling.db") -> "ConfigUpdateStateMachine":
        """Reconstruct a configuration update state machine from events.
        
        Args:
            update_id: ID of the configuration update
            db_path: Path to the database
            
        Returns:
            Reconstructed configuration update state machine
        """
        state_machine = cls()
        
        # Read events from the event store
        events = event_store.read_events(
            aggregate_id=update_id,
            aggregate_type=cls.AGGREGATE_TYPE,
            db_path=db_path
        )
        
        # Apply events to the state machine
        for event in events:
            state_machine.apply(event)
        
        return state_machine
    
    @staticmethod
    def update_config_read_model(
        update_id: str,
        gateway_id: str,
        state: str,
        version: Optional[str] = None,
        created_at: Optional[str] = None,
        published_at: Optional[str] = None,
        requested_at: Optional[str] = None,
        sent_at: Optional[str] = None,
        delivered_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        failed_at: Optional[str] = None,
        last_updated: Optional[str] = None,
        delivery_status: Optional[str] = None,
        error: Optional[str] = None,
        config_hash: Optional[str] = None,
        db_path: str = "recycling.db"
    ) -> None:
        """Update the configuration update read model."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            logger.debug(f"Updating read model for config update {update_id} with state={state}")
            
            cursor.execute('''
                INSERT OR REPLACE INTO config_updates
                (update_id, gateway_id, state, version, created_at, published_at, 
                requested_at, sent_at, delivered_at, completed_at, failed_at, 
                last_updated, delivery_status, error, config_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                update_id, gateway_id, state, version, created_at, published_at,
                requested_at, sent_at, delivered_at, completed_at, failed_at,
                last_updated, delivery_status, error, config_hash
            ))
            
            conn.commit()
            logger.info(f"Read model updated for config update {update_id} with state={state}")
        except Exception as e:
            logger.error(f"Error updating read model: {str(e)}")
            raise
        finally:
            conn.close()
    
    @staticmethod
    def store_config(
        config_hash: str,
        yaml_config: str,
        created_at: Optional[str] = None,
        db_path: str = "recycling.db"
    ) -> None:
        """Store configuration YAML in the configs table."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Use current time if not provided
            if created_at is None:
                created_at = datetime.now().isoformat()
            
            cursor.execute('''
                INSERT OR REPLACE INTO configs
                (config_hash, yaml_config, created_at)
                VALUES (?, ?, ?)
            ''', (config_hash, yaml_config, created_at))
            
            conn.commit()
            logger.info(f"Stored configuration with hash {config_hash}")
        except Exception as e:
            logger.error(f"Error storing configuration: {str(e)}")
            raise
        finally:
            conn.close()
    
    @staticmethod
    def get_config(
        config_hash: str,
        db_path: str = "recycling.db"
    ) -> Optional[str]:
        """Get configuration YAML from the configs table."""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT yaml_config 
                FROM configs
                WHERE config_hash = ?
            ''', (config_hash,))
            
            result = cursor.fetchone()
            if result:
                return result[0]
            return None
        except Exception as e:
            logger.error(f"Error getting configuration: {str(e)}")
            return None
        finally:
            conn.close()
    
    @staticmethod
    def get_config_update_status(
        update_id: str,
        db_path: str = "recycling.db"
    ) -> Optional[Dict[str, Any]]:
        """Get the current status of a configuration update from the read model."""
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM config_updates
                WHERE update_id = ?
            ''', (update_id,))
            
            row = cursor.fetchone()
            if row:
                update_dict = dict(row)
                
                # If we have a config_hash, get the actual config
                if update_dict.get('config_hash'):
                    yaml_config = ConfigUpdateStateMachine.get_config(
                        update_dict['config_hash'],
                        db_path
                    )
                    if yaml_config:
                        update_dict['yaml_config'] = yaml_config
                
                return update_dict
            return None
        except Exception as e:
            logger.error(f"Error getting config update status: {str(e)}")
            return None
        finally:
            conn.close()
    
    @staticmethod
    def list_config_updates(
        gateway_id: Optional[str] = None,
        include_completed: bool = True,
        db_path: str = "recycling.db"
    ) -> List[Dict[str, Any]]:
        """List all configuration updates from the read model."""
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Build query based on filters
            query = "SELECT * FROM config_updates"
            params = []
            
            where_clauses = []
            if gateway_id:
                where_clauses.append("gateway_id = ?")
                params.append(gateway_id)
            
            if not include_completed:
                where_clauses.append("state NOT IN (?, ?)")
                params.extend([ConfigUpdateState.UPDATE_COMPLETED.value, ConfigUpdateState.UPDATE_FAILED.value])
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += " ORDER BY last_updated DESC"
            
            cursor.execute(query, params)
            
            updates = []
            for row in cursor.fetchall():
                update_dict = dict(row)
                
                # We don't include the YAML config in listings to save bandwidth
                # Instead, clients can fetch it separately if needed
                updates.append(update_dict)
            
            return updates
        except Exception as e:
            logger.error(f"Error listing config updates: {str(e)}")
            return []
        finally:
            conn.close()
    
    @staticmethod
    def get_latest_config_for_gateway(
        gateway_id: str,
        db_path: str = "recycling.db"
    ) -> Optional[Dict[str, Any]]:
        """Get the latest completed configuration update for a gateway."""
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM config_updates
                WHERE gateway_id = ? AND state = ?
                ORDER BY completed_at DESC
                LIMIT 1
            ''', (gateway_id, ConfigUpdateState.UPDATE_COMPLETED.value))
            
            row = cursor.fetchone()
            if row:
                update_dict = dict(row)
                
                # Get the actual config
                if update_dict.get('config_hash'):
                    yaml_config = ConfigUpdateStateMachine.get_config(
                        update_dict['config_hash'],
                        db_path
                    )
                    if yaml_config:
                        update_dict['yaml_config'] = yaml_config
                
                return update_dict
            return None
        except Exception as e:
            logger.error(f"Error getting latest config: {str(e)}")
            return None
        finally:
            conn.close()