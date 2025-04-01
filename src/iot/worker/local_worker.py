import json
import logging
import docker
import asyncio
from datetime import datetime, timedelta
import sqlite3
from typing import Dict, Any, List, Optional

from .base import BaseWorker
from .state_machine import GatewayStateMachine, EventType, GatewayState, GatewayUpdateType
from database import event_store
from iot.config import settings

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
                return await self._handle_mqtt_event(task_data)
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
                        "API_URL": settings.CONTAINER_API_URL
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