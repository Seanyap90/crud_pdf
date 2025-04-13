import json
import logging
import docker
import asyncio
from datetime import datetime, timedelta
import sqlite3
from typing import Dict, Any, List, Optional

from .base import BaseWorker
from .state_machine import GatewayStateMachine, EventType, GatewayState, GatewayUpdateType
from .config_state_machine import ConfigUpdateStateMachine, ConfigUpdateState, ConfigEventType
from database import event_store
from iot.config import settings
from .mqtt_client import MQTTClient

logger = logging.getLogger(__name__)

class LocalWorker(BaseWorker):
    """Local worker implementation that uses SQLite database for event sourcing"""
    
    def __init__(self, db_path: str = None):
        """Initialize the local worker
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path or settings.DB_PATH
        self.running = False
        
        # Initialize Docker client for managing gateway containers
        try:
            self.docker_client = docker.from_env()
            logger.info("Docker client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {str(e)}")
            self.docker_client = None
        
        # Initialize MQTT client
        self.mqtt_client = None
        self._init_mqtt_client()
    
    def _init_mqtt_client(self):
        """Initialize MQTT client"""
        try:
            # Create MQTT client with improved implementation
            self.mqtt_client = MQTTClient(
                broker_host="localhost",
                broker_port=settings.MQTT_BROKER_PORT,
                # client_id=settings.MQTT_CLIENT_ID,
                username=settings.MQTT_USERNAME,
                password=settings.MQTT_PASSWORD
            )

            # Set longer timeout for large config messages
            self.mqtt_client.publish_timeout = 10.0  # Increase timeout to 10 seconds
            
            # Attempt connection with explicit result checking
            if not self.mqtt_client.connect():
                logger.warning(f"Initial MQTT connection failed - will retry automatically. Check broker at {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
            else:
                logger.info(f"MQTT client connected successfully to {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
            
            # Always subscribe to topics, the improved client will queue these
            # until connection is established
            if settings.RULES_ENGINE_ENABLED:
                self.mqtt_client.subscribe("config/delivered", self._handle_config_delivered_message)
                logger.info(f"Subscribed to config/delivered topic for configuration updates")
            
            logger.info(f"MQTT client initialized with broker {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
        except Exception as e:
            logger.error(f"MQTT client initialization failed: {str(e)}")
            self.mqtt_client = None
    
    async def start(self) -> None:
        """Start the worker"""
        self.running = True
        logger.debug("Worker started")
        
        # Start heartbeat checker task
        asyncio.create_task(self._heartbeat_checker())   
    
    async def _heartbeat_checker(self):
        """Background task to periodically check for missed heartbeats"""
        while self.running:
            try:
                await self._check_missed_heartbeats()
            except Exception as e:
                logger.error(f"Error in heartbeat checker: {str(e)}")
            
            # Check every 30 seconds
            await asyncio.sleep(30)
    
    async def _check_missed_heartbeats(self):
        """Check for gateways that have missed heartbeats with streamlined error format."""
        current_time = datetime.now()
        logger.debug("Checking for missed heartbeats")
        
        try:
            # Get all connected gateways
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT gateway_id, last_heartbeat 
                FROM gateways 
                WHERE status = ?
            """, (GatewayState.CONNECTED.value,))
            
            connected_gateways = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            # Check each gateway for missed heartbeats
            for gateway in connected_gateways:
                gateway_id = gateway["gateway_id"]
                last_heartbeat_str = gateway.get("last_heartbeat")
                
                if not last_heartbeat_str:
                    continue
                
                # Parse the last heartbeat timestamp
                try:
                    last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
                    
                    # Calculate time since last heartbeat
                    time_since_heartbeat = current_time - last_heartbeat
                    max_allowed_time = timedelta(seconds=settings.HEARTBEAT_INTERVAL_SECONDS * settings.HEARTBEAT_MISS_THRESHOLD)
                    
                    # If it's been too long, mark as disconnected
                    if time_since_heartbeat > max_allowed_time:
                        logger.info(f"Gateway {gateway_id} missed too many heartbeats, marking as disconnected")
                        
                        # Get current version
                        current_version = self.get_current_version(gateway_id)
                        
                        # Create disconnection event with streamlined error
                        disconnection_event_data = {
                            "gateway_id": gateway_id,
                            "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "error": json.dumps({"status": "reported offline", "reason": "missed heartbeats"})
                        }
                        
                        # Create and append the disconnection event
                        event = GatewayStateMachine.create_event(
                            gateway_id,
                            EventType.GATEWAY_DISCONNECTED,
                            disconnection_event_data,
                            current_version + 1
                        )
                        
                        self.append_event(event)
                        self.update_read_model(gateway_id)
                except Exception as e:
                    logger.error(f"Error processing heartbeat for gateway {gateway_id}: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error checking for missed heartbeats: {str(e)}")
    
    async def stop(self) -> None:
        """Stop the worker"""
        # Release resources
        if self.docker_client:
            self.docker_client.close()
        
        self.running = False
        logger.debug("Worker stopped")
    
    def append_event(self, event: Dict[str, Any]) -> None:
        """Append an event to the event store
        
        Args:
            event: The event to append
        """
        try:
            # Log the event being appended for debugging
            logger.debug(f"Appending event: {event['event_type']} for gateway {event['aggregate_id']}")
            
            # Append the event to the event store
            event_store.append_event(
                aggregate_id=event["aggregate_id"],
                aggregate_type=event["aggregate_type"],
                event_type=event["event_type"],
                event_data=event["event_data"],
                version=event["version"],
                db_path=self.db_path
            )
            
            logger.debug(f"Event appended successfully: {event['event_type']} for gateway {event['aggregate_id']}")
        except Exception as e:
            logger.error(f"Error appending event: {str(e)}")
            raise
    
    def read_events(self, aggregate_id: str) -> List[Dict[str, Any]]:
        """Read all events for an aggregate
        
        Args:
            aggregate_id: ID of the aggregate
            
        Returns:
            List of events for the aggregate
        """
        return event_store.read_events(
            aggregate_id=aggregate_id,
            aggregate_type=GatewayStateMachine.AGGREGATE_TYPE,
            db_path=self.db_path
        )
    
    def get_current_version(self, aggregate_id: str) -> int:
        """Get the current version of an aggregate
        
        Args:
            aggregate_id: ID of the aggregate
            
        Returns:
            Current version of the aggregate
        """
        return event_store.get_current_version(
            aggregate_id=aggregate_id,
            aggregate_type=GatewayStateMachine.AGGREGATE_TYPE,
            db_path=self.db_path
        )
    
    def update_read_model(self, gateway_id: str) -> None:
        """Update the read model for a gateway with streamlined fields."""
        try:
            # Reconstruct the state machine from all events
            state_machine = GatewayStateMachine.reconstruct_from_events(
                gateway_id=gateway_id,
                db_path=self.db_path
            )
            
            # Get the current gateway status
            status = state_machine.get_status()
            
            # Log current state for debugging
            logger.info(f"Updating read model for gateway {gateway_id}: state={status['status']}, version={status['version']}")
            
            # Extract certificate info if available
            certificate_info = status.get("certificate_info")
            
            # Extract timestamps
            created_at = None
            connected_at = None
            disconnected_at = None
            deleted_at = None
            
            if status['status'] == GatewayState.CREATED.value:
                created_at = status.get("created_at")
            elif status['status'] == GatewayState.CONNECTED.value:
                connected_at = status.get("connected_at")
            elif status['status'] == GatewayState.DISCONNECTED.value:
                disconnected_at = status.get("disconnected_at")
            elif status['status'] == GatewayState.DELETED.value:
                deleted_at = status.get("deleted_at")
            
            # Format error to be streamlined JSON if needed
            error = status.get("error")
            if error and isinstance(error, str) and not error.startswith("{"):
                if "offline" in error.lower():
                    error = json.dumps({"status": "reported offline"})
                else:
                    error = json.dumps({"message": error})
            
            # Update the read model with streamlined fields
            GatewayStateMachine.update_gateway_read_model(
                gateway_id=status["gateway_id"],
                name=status["name"],
                location=status["location"],
                status=status["status"],
                last_updated=status["last_updated"],
                last_heartbeat=status.get("last_heartbeat"),
                uptime=status.get("uptime"),
                health=status.get("health"),
                error=error,
                created_at=created_at,
                connected_at=connected_at,
                disconnected_at=disconnected_at,
                deleted_at=deleted_at,
                certificate_info=certificate_info,
                db_path=self.db_path
            )
            
            # Verify the update was successful
            updated_status = self.get_gateway_status(gateway_id)
            if updated_status:
                logger.info(f"Gateway {gateway_id} updated to state: {updated_status.get('status')}")
        
        except Exception as e:
            logger.error(f"Error updating read model: {str(e)}")
            raise
    
    def get_gateway_status(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a gateway
        
        Args:
            gateway_id: ID of the gateway
            
        Returns:
            Current status of the gateway
        """
        return GatewayStateMachine.get_gateway_status(
            gateway_id=gateway_id,
            db_path=self.db_path
        )
    
    def list_gateways(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """List all gateways
        
        Args:
            include_deleted: Whether to include deleted gateways
            
        Returns:
            List of all gateways
        """
        return GatewayStateMachine.list_gateways(
            db_path=self.db_path,
            include_deleted=include_deleted
        )
    
    async def check_and_process_timeouts(self) -> None:
        """Check for and process gateway timeouts
        
        No longer needed in the new state machine implementation.
        Kept for compatibility with the BaseWorker interface.
        """
        pass
    
    async def process_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a task
        
        Args:
            task_data: Data for the task to be processed
            
        Returns:
            Result of the task processing
        """
        task_type = task_data.get("type", "")
        gateway_id = task_data.get("gateway_id", "")
        
        logger.info(f"Processing task type: {task_type} with data: {task_data}")
        
        try:
            if task_type == "create_gateway":
                return await self._handle_create_gateway(task_data)
            elif task_type == "delete_gateway":
                return await self._handle_delete_gateway(task_data)
            elif task_type.startswith("mqtt_"):
                # Handle config events differently from regular gateway MQTT events
                if task_type == "mqtt_config_event":
                    return await self._handle_config_mqtt_event(task_data)
                else:
                    return await self._handle_mqtt_event(task_data)
            elif task_type == "config_update":
                return await self._handle_config_update(task_data)
            else:
                logger.warning(f"Unknown task type: {task_type}")
                raise ValueError(f"Unknown task type: {task_type}")
        except Exception as e:
            logger.error(f"Error processing task: {str(e)}")
            raise
    
    async def _handle_create_gateway(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle creating a new gateway with container"""
        gateway_id = task_data.get("gateway_id")
        if not gateway_id:
            raise ValueError("gateway_id is required")
        
        # Check if gateway already exists
        current_version = self.get_current_version(gateway_id)
        if current_version != -1:
            raise ValueError(f"Gateway {gateway_id} already exists")
        
        # Create event data
        event_data = {
            "gateway_id": gateway_id,
            "name": task_data.get("name", "Unnamed Gateway"),
            "location": task_data.get("location", "Unknown"),
            "timestamp": datetime.now().isoformat()
        }
        
        # Create and append event
        event = GatewayStateMachine.create_event(
            gateway_id,
            EventType.GATEWAY_CREATED,
            event_data,
            0
        )
        self.append_event(event)
        
        # Update read model
        self.update_read_model(gateway_id)
        current_version = 0
        
        # Create Docker container for the gateway
        try:
            if self.docker_client:
                container = self.docker_client.containers.run(
                    settings.GATEWAY_IMAGE,
                    detach=True,
                    environment={
                        "GATEWAY_ID": gateway_id,
                        "MQTT_BROKER_ADDRESS": settings.CONTAINER_MQTT_ADDRESS,
                    },
                    network=settings.DOCKER_NETWORK,
                    name=f"gateway-{gateway_id}",
                    labels={"gateway": "true"}
                )
                
                # Update event data with container ID
                container_event_data = {
                    "gateway_id": gateway_id,
                    "container_id": container.id,
                    "timestamp": datetime.now().isoformat(),
                    "update_type": GatewayUpdateType.STATUS,
                    "payload": {"status": "container_created"}
                }
                
                # Create and append event for container creation
                container_event = GatewayStateMachine.create_event(
                    gateway_id,
                    EventType.GATEWAY_UPDATED,
                    container_event_data,
                    current_version + 1
                )
                self.append_event(container_event)
                
                # Update read model again
                self.update_read_model(gateway_id)
                
                logger.info(f"Created container {container.id} for gateway {gateway_id}")
            else:
                logger.warning("Docker client unavailable, skipping container creation")
        except Exception as e:
            logger.error(f"Failed to create container: {str(e)}")
        
        # Return the current gateway status
        return self.get_gateway_status(gateway_id)
    
    async def _handle_delete_gateway(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle deleting a gateway"""
        gateway_id = task_data.get("gateway_id")
        if not gateway_id:
            raise ValueError("gateway_id is required")
        
        # Check if gateway exists
        current_version = self.get_current_version(gateway_id)
        if current_version == -1:
            raise ValueError(f"Gateway {gateway_id} not found")
        
        # Create deletion event
        event_data = {
            "gateway_id": gateway_id,
            "timestamp": datetime.now().isoformat(),
            "reason": task_data.get("reason", "Manual deletion requested")
        }
        
        # Create and append event
        event = GatewayStateMachine.create_event(
            gateway_id,
            EventType.GATEWAY_DELETED,
            event_data,
            current_version + 1
        )
        self.append_event(event)
        
        # Update read model
        self.update_read_model(gateway_id)
        
        # Stop and remove the container
        try:
            if self.docker_client:
                try:
                    container = self.docker_client.containers.get(f"gateway-{gateway_id}")
                    container.stop(timeout=10)
                    container.remove(force=True)
                    logger.info(f"Container for gateway {gateway_id} stopped and removed")
                except docker.errors.NotFound:
                    logger.info(f"No container found for gateway {gateway_id}")
                except Exception as e:
                    logger.error(f"Error stopping container: {str(e)}")
        except Exception as e:
            logger.error(f"Error with Docker client: {str(e)}")
        
        # Return the final gateway status
        return self.get_gateway_status(gateway_id)
    
    async def _handle_mqtt_event(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MQTT events from gateways"""
        gateway_id = task_data.get("gateway_id")
        if not gateway_id:
            raise ValueError("gateway_id is required")
        
        # Extract the event type from task_type (remove "mqtt_" prefix)
        event_type = task_data.get("type", "").replace("mqtt_", "")
        payload = task_data.get("payload", {})
        
        # Get current version or create gateway if it doesn't exist
        current_version = self.get_current_version(gateway_id)
        
        # Auto-register gateway if it doesn't exist
        if current_version == -1 and event_type in ["heartbeat", "acknowledge", "status"]:
            logger.info(f"Auto-registering gateway from MQTT event: {gateway_id}")
            
            # Create basic gateway data
            event_data = {
                "gateway_id": gateway_id,
                "name": f"Auto-registered Gateway {gateway_id}",
                "location": "Unknown (Auto-registered)",
                "timestamp": datetime.now().isoformat(),
                "auto_registered": True
            }
            
            # Create and append event
            event = GatewayStateMachine.create_event(
                gateway_id,
                EventType.GATEWAY_CREATED,
                event_data,
                0
            )
            self.append_event(event)
            current_version = 0
            
            # Update read model
            self.update_read_model(gateway_id)
        
        # Special handling for heartbeat or online status - resend pending configs
        if event_type in ["heartbeat", "status"] and isinstance(payload, dict):
            update_type_str = task_data.get("update_type")
            if (update_type_str == "heartbeat" or 
                (isinstance(update_type_str, str) and update_type_str.upper() == "HEARTBEAT") or 
                payload.get("status") == "online"):
                
                # Check if gateway just reconnected
                state_machine = GatewayStateMachine.reconstruct_from_events(
                    gateway_id=gateway_id,
                    db_path=self.db_path
                )
                
                if state_machine.current_state == GatewayState.CONNECTED:
                    # Look for pending config updates
                    await self._check_pending_configs(gateway_id)
        
        # Process different event types
        if event_type in ["heartbeat", "status"]:
            # Use the consolidated GATEWAY_UPDATED event
            update_type = task_data.get("update_type", event_type)
            
            # Make sure we have a proper enum value
            if isinstance(update_type, str) and update_type in ["heartbeat", "status"]:
                update_type = GatewayUpdateType(update_type)
            
            event_data = {
                "gateway_id": gateway_id,
                "timestamp": datetime.now().isoformat(),
                "update_type": update_type,
                "payload": payload
            }
            
            # Create and append event
            event = GatewayStateMachine.create_event(
                gateway_id,
                EventType.GATEWAY_UPDATED,
                event_data,
                current_version + 1
            )
            self.append_event(event)
            
            # Handle special case for 'online' status - transition to CONNECTED
            if event_type == "status" and isinstance(payload, dict) and payload.get('status') == 'online':
                # Get current state machine
                state_machine = GatewayStateMachine.reconstruct_from_events(
                    gateway_id=gateway_id,
                    db_path=self.db_path
                )
                
                # If we're in CREATED or DISCONNECTED state, check for certificates and transition to CONNECTED
                if state_machine.current_state in [GatewayState.CREATED, GatewayState.DISCONNECTED]:
                    # Check if certificates are installed (either in state machine or in payload)
                    has_certificates = (
                        state_machine.data.get("certificate_info", {}).get("status") == "installed" 
                        or (isinstance(payload, dict) and payload.get("certificate_status") == "installed")
                    )
                    
                    if has_certificates:
                        # Create connection event
                        connection_event_data = {
                            "gateway_id": gateway_id,
                            "timestamp": datetime.now().isoformat(),
                            "certificate_info": {
                                "status": "installed",
                                "installed_at": datetime.now().isoformat()
                            }
                        }
                        
                        # Create and append event
                        connection_event = GatewayStateMachine.create_event(
                            gateway_id,
                            EventType.GATEWAY_CONNECTED,
                            connection_event_data,
                            current_version + 2
                        )
                        self.append_event(connection_event)
                        logger.info(f"Gateway {gateway_id} transitioned to CONNECTED state")
            
            # Handle special case for 'offline' status - transition to DISCONNECTED
            elif event_type == "status" and isinstance(payload, dict) and payload.get('status') == 'offline':
                # Get current state machine
                state_machine = GatewayStateMachine.reconstruct_from_events(
                    gateway_id=gateway_id,
                    db_path=self.db_path
                )
                
                # If we're in CONNECTED state, transition to DISCONNECTED
                if state_machine.current_state == GatewayState.CONNECTED:
                    # Create disconnection event
                    disconnection_event_data = {
                        "gateway_id": gateway_id,
                        "timestamp": datetime.now().isoformat(),
                        "error": "Gateway reported offline status"
                    }
                    
                    # Create and append event
                    disconnection_event = GatewayStateMachine.create_event(
                        gateway_id,
                        EventType.GATEWAY_DISCONNECTED,
                        disconnection_event_data,
                        current_version + 2
                    )
                    self.append_event(disconnection_event)
                    logger.info(f"Gateway {gateway_id} transitioned to DISCONNECTED state")
            
            # Update read model
            self.update_read_model(gateway_id)
        
        # Handle heartbeat events
        elif event_type == "heartbeat":
            # Already covered in the status/heartbeat handling above
            pass
        
        # Handle gateway acknowledgment (for backward compatibility)
        elif event_type == "acknowledge":
            # Get current state machine
            state_machine = GatewayStateMachine.reconstruct_from_events(
                gateway_id=gateway_id,
                db_path=self.db_path
            )
            
            # If we're in CREATED or DISCONNECTED state, check for certificates and transition to CONNECTED
            if state_machine.current_state in [GatewayState.CREATED, GatewayState.DISCONNECTED]:
                # Create status update event with certificate info
                status_event_data = {
                    "gateway_id": gateway_id,
                    "timestamp": datetime.now().isoformat(),
                    "update_type": GatewayUpdateType.STATUS,
                    "payload": {
                        "status": "online",
                        "certificate_status": "installed"
                    }
                }
                
                # Create and append event
                status_event = GatewayStateMachine.create_event(
                    gateway_id,
                    EventType.GATEWAY_UPDATED,
                    status_event_data,
                    current_version + 1
                )
                self.append_event(status_event)
                
                # Create connection event
                connection_event_data = {
                    "gateway_id": gateway_id,
                    "timestamp": datetime.now().isoformat(),
                    "certificate_info": {
                        "status": "installed",
                        "installed_at": datetime.now().isoformat()
                    }
                }
                
                # Create and append event
                connection_event = GatewayStateMachine.create_event(
                    gateway_id,
                    EventType.GATEWAY_CONNECTED,
                    connection_event_data,
                    current_version + 2
                )
                self.append_event(connection_event)
                
                # Update read model
                self.update_read_model(gateway_id)
                logger.info(f"Gateway {gateway_id} acknowledged and transitioned to CONNECTED state")
        
        # Handle delete events
        elif event_type == "delete":
            # Create deletion event
            deletion_event_data = {
                "gateway_id": gateway_id,
                "timestamp": datetime.now().isoformat(),
                "reason": payload.get("reason", "Gateway requested deletion")
            }
            
            # Create and append event
            deletion_event = GatewayStateMachine.create_event(
                gateway_id,
                EventType.GATEWAY_DELETED,
                deletion_event_data,
                current_version + 1
            )
            self.append_event(deletion_event)
            
            # Update read model
            self.update_read_model(gateway_id)
            
            # Stop and remove the container
            try:
                if self.docker_client:
                    try:
                        container = self.docker_client.containers.get(f"gateway-{gateway_id}")
                        container.stop(timeout=10)
                        container.remove(force=True)
                        logger.info(f"Container for gateway {gateway_id} stopped and removed")
                    except docker.errors.NotFound:
                        logger.info(f"No container found for gateway {gateway_id}")
                    except Exception as e:
                        logger.error(f"Error stopping container: {str(e)}")
            except Exception as e:
                logger.error(f"Error with Docker client: {str(e)}")
            
            logger.info(f"Gateway {gateway_id} deleted")
        
        # Return the current gateway status
        return self.get_gateway_status(gateway_id)
    
    def _handle_config_delivered_message(self, topic: str, payload: dict):
        """Handle config delivered messages from MQTT
        
        This is called when a message is received on the config/delivered topic.
        It asynchronously processes the message to update the config state machine.
        """
        try:
            update_id = payload.get("update_id")
            if not update_id:
                logger.warning(f"Received config delivered message without update_id: {payload}")
                return
                
            # Process the message asynchronously
            asyncio.create_task(self.process_config_mqtt_event({
                "type": "mqtt_config_event",
                "topic": topic,
                "payload": payload,
                "update_id": update_id,
                "event_type": "delivered"
            }))
            
        except Exception as e:
            logger.error(f"Error handling config delivered message: {str(e)}")
    
    async def _check_pending_configs(self, gateway_id: str):
        """Check and resend any pending configurations for a gateway"""
        try:
            # Find pending configurations
            pending_configs = self.list_config_updates(
                gateway_id=gateway_id,
                include_completed=False
            )
            
            # Filter to configs in waiting state
            waiting_configs = [cfg for cfg in pending_configs 
                            if cfg.get("state") in ["stored", "waiting"]]
            
            if waiting_configs:
                logger.info(f"Found {len(waiting_configs)} pending configs for gateway {gateway_id}")
                
                # For each pending config, republish to MQTT
                for config in waiting_configs:
                    update_id = config.get("update_id")
                    
                    # Get the full config including YAML
                    full_config = self.get_config_update(update_id, include_config=True)
                    if full_config and "yaml_config" in full_config:
                        logger.info(f"Republishing config {update_id} for gateway {gateway_id}")
                        
                        # Use the improved publish method
                        if self.mqtt_client:
                            mqtt_payload = {
                                "gateway_id": gateway_id,
                                "yaml_config": full_config["yaml_config"],
                                "update_id": update_id,
                                "timestamp": datetime.now().isoformat()
                            }
                            
                            if self.mqtt_client.publish("config/new", mqtt_payload):
                                logger.info(f"Successfully republished config {update_id}")
                            else:
                                logger.warning(f"Failed to republish config {update_id}")
        
        except Exception as e:
            logger.error(f"Error checking pending configs: {str(e)}")
    
    async def stop(self) -> None:
        """Stop the worker and clean up resources
        
        Ensures all resources are properly released, including:
        - MQTT client disconnection
        - Docker client cleanup
        """
        logger.info("Stopping worker and cleaning up resources")

        # Clean up MQTT client
        if self.mqtt_client:
            try:
                self.mqtt_client.disconnect()
                logger.info("MQTT client disconnected successfully")
            except Exception as e:
                logger.error(f"Error disconnecting MQTT client: {str(e)}")
            
        # Existing cleanup code...
        if self.docker_client:
            try:
                self.docker_client.close()
                logger.info("Docker client closed successfully")
            except Exception as e:
                logger.error(f"Error closing Docker client: {str(e)}")
        
        # Set running flag to false
        self.running = False
        logger.info("Worker stopped")
    
    async def _handle_config_update(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle configuration update task"""
        update_id = task_data.get("update_id")
        gateway_id = task_data.get("gateway_id")
        yaml_config = task_data.get("yaml_config")
        config_hash = task_data.get("config_hash")
        
        if not update_id or not gateway_id or not yaml_config:
            raise ValueError("update_id, gateway_id, and yaml_config are required")
        
        # Check if the gateway exists
        gateway_status = self.get_gateway_status(gateway_id)
        if not gateway_status:
            raise ValueError(f"Gateway {gateway_id} not found")
        
        # Check if the gateway is in CONNECTED state
        if gateway_status.get("status") != GatewayState.CONNECTED.value:
            raise ValueError(f"Gateway {gateway_id} is not in CONNECTED state")
        
        # Check if update already exists (idempotency)
        current_version = event_store.get_current_version(
            aggregate_id=update_id,
            aggregate_type=ConfigUpdateStateMachine.AGGREGATE_TYPE,
            db_path=self.db_path
        )
        
        if current_version != -1:
            logger.info(f"Configuration update {update_id} already exists, returning existing state")
            return self.get_config_update(update_id)
        
        # Store the configuration in the configs table
        ConfigUpdateStateMachine.store_config(
            config_hash=config_hash,
            yaml_config=yaml_config,
            created_at=task_data.get("timestamp"),
            db_path=self.db_path
        )
        
        # Create event data
        event_data = {
            "update_id": update_id,
            "gateway_id": gateway_id,
            "config_hash": config_hash,
            "timestamp": task_data.get("timestamp", datetime.now().isoformat())
        }
        
        # Create and append CONFIG_CREATED event
        event = ConfigUpdateStateMachine.create_event(
            update_id,
            ConfigEventType.CONFIG_CREATED,
            event_data,
            0  # Initial version
        )
        self.append_event(event)
        
        # Update read model
        self.update_config_read_model(update_id)

        # Publish configuration to MQTT with improved error handling and retries
        mqtt_publish_success = False
        
        # Ensure MQTT client is available
        if self.mqtt_client is None:
            # Reinitialize MQTT client if needed
            self._init_mqtt_client()
        
        logger.info(f"Publishing configuration to MQTT broker: {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
        logger.info(f"Configuration details - Gateway: {gateway_id}, Update ID: {update_id}")
        
        if self.mqtt_client:
            # Prepare MQTT payload
            mqtt_payload = {
                "gateway_id": gateway_id,
                "yaml_config": yaml_config,
                "update_id": update_id,
                "timestamp": datetime.now().isoformat()
            }
            
            # Multiple attempts to publish with backoff
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    # Use the improved publish method that returns success/failure
                    if self.mqtt_client.publish("config/new", mqtt_payload):
                        logger.info(f"Configuration published to MQTT for update {update_id} on attempt {attempt}")
                        mqtt_publish_success = True
                        break
                    else:
                        logger.warning(f"Failed to publish config to MQTT for update {update_id} on attempt {attempt}")
                        if attempt < max_attempts:
                            # Wait before retrying
                            await asyncio.sleep(1 * attempt)  # Increasing backoff
                except Exception as e:
                    logger.error(f"Error publishing config to MQTT on attempt {attempt}: {str(e)}")
                    if attempt < max_attempts:
                        await asyncio.sleep(1 * attempt)
        else:
            logger.error(f"Cannot publish configuration to MQTT: client not available")
        
        # Only update state if MQTT publish was successful
        if mqtt_publish_success:
            # Create and append CONFIG_PUBLISHED event
            publish_event_data = {
                "update_id": update_id,
                "gateway_id": gateway_id,
                "timestamp": datetime.now().isoformat()
            }
            
            publish_event = ConfigUpdateStateMachine.create_event(
                update_id,
                ConfigEventType.CONFIG_PUBLISHED,
                publish_event_data,
                1  # Next version
            )
            self.append_event(publish_event)
            
            # Update read model again
            self.update_config_read_model(update_id)
            
            logger.info(f"Configuration update {update_id} published to MQTT successfully")
        else:
            # We created the config but couldn't publish it - log this state
            logger.error(f"Failed to publish configuration update {update_id} to MQTT after multiple attempts")
            
            # Create and append CONFIG_FAILED event
            fail_event_data = {
                "update_id": update_id,
                "gateway_id": gateway_id,
                "error": "Failed to publish configuration to MQTT broker",
                "timestamp": datetime.now().isoformat()
            }
            
            fail_event = ConfigUpdateStateMachine.create_event(
                update_id,
                ConfigEventType.CONFIG_FAILED,
                fail_event_data,
                1  # Next version
            )
            self.append_event(fail_event)
            
            # Update read model
            self.update_config_read_model(update_id)
        
        # Return the current status
        return self.get_config_update(update_id)
    
    async def _handle_config_mqtt_event(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle configuration-related MQTT events"""
        update_id = task_data.get("update_id")
        event_type = task_data.get("event_type")
        topic = task_data.get("topic")
        payload = task_data.get("payload", {})
        gateway_id = task_data.get("gateway_id")

        # Extract gateway_id from topic if not provided directly
        if not gateway_id and topic:
            parts = topic.split('/')
            if len(parts) > 1 and topic.startswith("gateway/"):
                gateway_id = parts[1]
        
        # For delivered events with missing or empty update_id, try harder to find the update_id
        if (not update_id or update_id == "") and ("delivered" in topic or event_type == "delivered"):
            try:
                # First check payload for update_id
                if isinstance(payload, dict):
                    if "update_id" in payload and payload["update_id"] and payload["update_id"] != "":
                        update_id = payload["update_id"]
                        logger.info(f"Found update_id in payload: {update_id}")
                        task_data["update_id"] = update_id
                
                # If still not found, get most recent update for this gateway
                if (not update_id or update_id == "") and gateway_id:
                    # Look for any state, not just pending
                    pending_updates = self.list_config_updates(
                        gateway_id=gateway_id,
                        include_completed=False
                    )
                    
                    if pending_updates:
                        # Sort by recency if timestamps available
                        if pending_updates[0].get("last_updated"):
                            pending_updates.sort(
                                key=lambda x: x.get("last_updated", ""),
                                reverse=True
                            )
                        
                        update_id = pending_updates[0].get("update_id")
                        logger.info(f"Using most recent update_id: {update_id}")
                        task_data["update_id"] = update_id
            except Exception as e:
                logger.error(f"Error finding update_id: {str(e)}")

        if not update_id or update_id == "":
            error_msg = f"Could not determine update_id for topic {topic}"
            logger.warning(error_msg)
            return {"status": "ignored", "reason": "No update_id found"}
        
        # Get current version
        current_version = event_store.get_current_version(
            aggregate_id=update_id,
            aggregate_type=ConfigUpdateStateMachine.AGGREGATE_TYPE,
            db_path=self.db_path
        )
        
        if current_version == -1:
            raise ValueError(f"Configuration update {update_id} not found")
        
        # Get current state machine
        state_machine = ConfigUpdateStateMachine.reconstruct_from_events(
            update_id=update_id,
            db_path=self.db_path
        )
        
        # Process the event based on the event type and current state
        if topic.startswith("gateway/") and "request_config" in topic:
            # Gateway requested configuration
            if state_machine.current_state == ConfigUpdateState.WAITING_FOR_REQUEST:
                # Create and append CONFIG_REQUESTED event
                event_data = {
                    "update_id": update_id,
                    "topic": topic,
                    "timestamp": task_data.get("timestamp", datetime.now().isoformat())
                }
                
                event = ConfigUpdateStateMachine.create_event(
                    update_id,
                    ConfigEventType.CONFIG_REQUESTED,
                    event_data,
                    current_version + 1
                )
                self.append_event(event)
                
                # Update read model
                self.update_config_read_model(update_id)
                logger.info(f"Gateway requested configuration for update {update_id}")
            else:
                logger.warning(f"Ignoring config request in state {state_machine.current_state}")
        
        elif event_type == "update" or "config/update" in topic:
            # Configuration was sent to the gateway
            if state_machine.current_state == ConfigUpdateState.NOTIFYING_GATEWAY:
                # Create and append CONFIG_SENT event
                event_data = {
                    "update_id": update_id,
                    "topic": topic,
                    "timestamp": task_data.get("timestamp", datetime.now().isoformat())
                }
                
                event = ConfigUpdateStateMachine.create_event(
                    update_id,
                    ConfigEventType.CONFIG_SENT,
                    event_data,
                    current_version + 1
                )
                self.append_event(event)
                
                # Update read model
                self.update_config_read_model(update_id)
                logger.info(f"Configuration sent to gateway for update {update_id}")
            else:
                logger.warning(f"Ignoring config sent event in state {state_machine.current_state}")
        
        elif event_type == "delivered" or "config/delivered" in topic:
            # Gateway acknowledged delivery
            if state_machine.current_state == ConfigUpdateState.WAITING_FOR_ACK:
                # Create and append CONFIG_DELIVERED event
                event_data = {
                    "update_id": update_id,
                    "topic": topic,
                    "status": payload.get("status", "unknown"),
                    "timestamp": task_data.get("timestamp", datetime.now().isoformat())
                }
                
                event = ConfigUpdateStateMachine.create_event(
                    update_id,
                    ConfigEventType.CONFIG_DELIVERED,
                    event_data,
                    current_version + 1
                )
                self.append_event(event)
                
                # Update read model
                self.update_config_read_model(update_id)
                logger.info(f"Configuration delivery acknowledged for update {update_id}")
                
                # Now send the completion event if delivery was successful
                if payload.get("status") == "success":
                    # Create and append CONFIG_COMPLETED event
                    complete_event_data = {
                        "update_id": update_id,
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    complete_event = ConfigUpdateStateMachine.create_event(
                        update_id,
                        ConfigEventType.CONFIG_COMPLETED,
                        complete_event_data,
                        current_version + 2
                    )
                    self.append_event(complete_event)
                    
                    # Update read model
                    self.update_config_read_model(update_id)
                    logger.info(f"Configuration update {update_id} completed successfully")
                else:
                    # Create and append CONFIG_FAILED event
                    fail_event_data = {
                        "update_id": update_id,
                        "error": f"Delivery failed: {payload.get('status')}",
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    fail_event = ConfigUpdateStateMachine.create_event(
                        update_id,
                        ConfigEventType.CONFIG_FAILED,
                        fail_event_data,
                        current_version + 2
                    )
                    self.append_event(fail_event)
                    
                    # Update read model
                    self.update_config_read_model(update_id)
                    logger.info(f"Configuration update {update_id} failed")
            else:
                logger.warning(f"Ignoring config delivered event in state {state_machine.current_state}")
        
        else:
            logger.warning(f"Unhandled config event type: {event_type} on topic {topic}")
        
        # Return the current status
        return {"status": "processed", "update": self.get_config_update(update_id)}
   
    def update_config_read_model(self, update_id: str) -> None:
        """Update the read model for a configuration update"""
        try:
            # Reconstruct the state machine from events
            state_machine = ConfigUpdateStateMachine.reconstruct_from_events(
                update_id=update_id,
                db_path=self.db_path
            )
            
            # Get the current status
            status = state_machine.get_status()
            
            # Extract fields for the read model
            ConfigUpdateStateMachine.update_config_read_model(
                update_id=status["update_id"],
                gateway_id=status["gateway_id"],
                state=status["state"],
                version=status.get("version"),
                created_at=status.get("created_at"),
                published_at=status.get("published_at"),
                requested_at=status.get("requested_at"),
                sent_at=status.get("sent_at"),
                delivered_at=status.get("delivered_at"),
                completed_at=status.get("completed_at"),
                failed_at=status.get("failed_at"),
                last_updated=status.get("last_updated"),
                delivery_status=status.get("delivery_status"),
                error=status.get("error"),
                config_hash=status.get("config_hash"),
                db_path=self.db_path
            )
            
            logger.info(f"Read model updated for config update {update_id}: state={status['state']}")
        except Exception as e:
            logger.error(f"Error updating config read model: {str(e)}")
            raise
    
    def get_config_update(self, update_id: str, include_config: bool = False) -> Optional[Dict[str, Any]]:
        """Get a configuration update"""
        try:
            # Get the update from the read model
            update = ConfigUpdateStateMachine.get_config_update_status(
                update_id=update_id,
                db_path=self.db_path
            )
            
            if not update:
                return None
            
            # Remove yaml_config if not requested to save bandwidth
            if not include_config and "yaml_config" in update:
                del update["yaml_config"]
            
            return update
        except Exception as e:
            logger.error(f"Error getting config update: {str(e)}")
            return None
    
    def list_config_updates(
        self, 
        gateway_id: Optional[str] = None, 
        include_completed: bool = True
    ) -> List[Dict[str, Any]]:
        """List configuration updates"""
        try:
            return ConfigUpdateStateMachine.list_config_updates(
                gateway_id=gateway_id,
                include_completed=include_completed,
                db_path=self.db_path
            )
        except Exception as e:
            logger.error(f"Error listing config updates: {str(e)}")
            return []
    
    def get_latest_config(self, gateway_id: str, include_config: bool = True) -> Optional[Dict[str, Any]]:
        """Get the latest configuration for a gateway"""
        try:
            update = ConfigUpdateStateMachine.get_latest_config_for_gateway(
                gateway_id=gateway_id,
                db_path=self.db_path
            )
            
            if not update:
                return None
            
            # Remove yaml_config if not requested to save bandwidth
            if not include_config and "yaml_config" in update:
                del update["yaml_config"]
            
            return update
        except Exception as e:
            logger.error(f"Error getting latest config: {str(e)}")
            return None    