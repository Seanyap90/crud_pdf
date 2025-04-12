from fastapi import APIRouter, HTTPException, Depends, status, File, Form, UploadFile
from datetime import datetime
import logging
import hashlib
import json
import yaml
from typing import Dict, Any, List, Optional
from .models import (
    CreateGatewayRequest, 
    MQTTEventRequest, 
    GatewayStatus, 
    EventResponse, 
    GatewayList,
    GatewayUpdateType,
    GatewayState,
    ConfigUpdateRequest,
    ConfigUpdateStatus,
    ConfigUpdateResponse,
    ConfigUpdateList,
    ConfigMQTTEventRequest
)
from .worker.base import BaseWorker
from .worker.local_worker import LocalWorker

logger = logging.getLogger(__name__)

# Create a router instance
router = APIRouter()

# Helper function to get the worker
async def get_worker() -> BaseWorker:
    """Default worker provider - will be overridden in main.py"""
    worker = LocalWorker()  # Always return a concrete implementation
    await worker.start()
    return worker

@router.get("/")
async def root():
    """Root endpoint to confirm API is running"""
    return {"message": "IoT Gateway Management API"}

@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "healthy"}

@router.post("/api/gateways", response_model=GatewayStatus, status_code=status.HTTP_201_CREATED)
async def create_gateway(
    request: CreateGatewayRequest,
    worker: BaseWorker = Depends(get_worker)
):
    """Create a new gateway"""
    try:
        # Log the raw request data
        request_dict = request.model_dump()
        logger.info(f"Received gateway creation request: {request_dict}")

        if not request.gateway_id:
            request.gateway_id = f"gateway-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            logger.info(f"Generated gateway_id: {request.gateway_id}")

        task_data = {
            "type": "create_gateway",
            "gateway_id": request.gateway_id,
            "name": request.name,
            "location": request.location,
        }

        # Log the task data being sent to the worker
        logger.info(f"Sending task to worker: {task_data}")

        result = await worker.process_task(task_data)
        logger.info(f"Gateway creation successful. Result: {result}")
        return result

    except ValueError as e:
        logger.error(f"Invalid gateway data: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating gateway: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/gateways/{gateway_id}", response_model=EventResponse)
async def delete_gateway(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Delete a gateway
    
    Deletes a gateway with the specified ID.
    This transitions the gateway to the DELETED state and stops/removes the container.
    """
    try:
        task_data = {
            "type": "delete_gateway",
            "gateway_id": gateway_id,
            "reason": "API deletion request"
        }
        logger.info(f"Deleting gateway: {gateway_id}")
        result = await worker.process_task(task_data)
        return {"status": "deleted", "gateway": result}
    except ValueError as e:
        logger.error(f"Invalid gateway deletion: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting gateway: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/gateways/{gateway_id}", response_model=GatewayStatus)
async def update_gateway_info(
    gateway_id: str,
    request: CreateGatewayRequest,
    worker: BaseWorker = Depends(get_worker)
):
    """Update gateway information
    
    Updates the name and location of a gateway.
    """
    try:
        # Check if gateway exists
        status = worker.get_gateway_status(gateway_id)
        if not status:
            raise HTTPException(status_code=404, detail="Gateway not found")
        
        # Create a simulated status MQTT event with updated info
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload={
                "name": request.name,
                "location": request.location,
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        result = await process_mqtt_event(mqtt_event, worker)
        return result["gateway"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating gateway info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/gateways/{gateway_id}", response_model=GatewayStatus)
async def get_gateway_status(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Get gateway status
    
    Returns the current status of the gateway with the specified ID.
    """
    logger.info(f"Getting status for gateway: {gateway_id}")
    status = worker.get_gateway_status(gateway_id)
    if not status:
        raise HTTPException(status_code=404, detail="Gateway not found")
    return status

@router.get("/api/gateways", response_model=GatewayList)
async def list_gateways(
    worker: BaseWorker = Depends(get_worker),
    include_deleted: bool = False
):
    """List all gateways
    
    Returns a list of all gateways in the system.
    
    Args:
        include_deleted: Whether to include deleted gateways in the list
    """
    logger.info("Retrieving list of all gateways")
    try:
        gateways = worker.list_gateways(include_deleted=include_deleted)
        return {"gateways": gateways, "total": len(gateways)}
    except Exception as e:
        logger.error(f"Error listing gateways: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/mqtt/events", response_model=EventResponse)
async def process_mqtt_event(
    request: MQTTEventRequest,
    worker: BaseWorker = Depends(get_worker)
):
    """Process an MQTT event
    
    Processes an MQTT event received from the rules engine.
    The event may be a heartbeat or status update.
    """
    try:
        logger.info(f"Received MQTT event: {request.model_dump()}")
        
        # Handle messages from rules engine that may have a different format
        # Check if this is coming directly from rules engine with topic info
        if hasattr(request, 'topic') and request.topic:
            logger.info(f"Processing message from rules engine with topic: {request.topic}")
            # Extract gateway_id and event_type from topic if needed
            topic_parts = request.topic.split('/')
            if len(topic_parts) >= 3 and topic_parts[0] == 'gateway':
                if not request.gateway_id:
                    request.gateway_id = topic_parts[1]
                if not request.event_type and len(topic_parts) >= 3:
                    request.event_type = topic_parts[2]
        
        # Create task data with type from event_type
        task_data = {
            "type": f"mqtt_{request.event_type}",
            "gateway_id": request.gateway_id,
            "payload": request.payload,
            "timestamp": request.timestamp
        }
        
        # For consolidated gateway updates, add update_type
        if request.event_type in ["heartbeat", "status"]:
            # Default to event_type if update_type not provided
            update_type = request.update_type or request.event_type
            task_data["update_type"] = update_type
        
        logger.debug(f"Processed MQTT event into task data: {task_data}")
        result = await worker.process_task(task_data)
        return {"status": "processed", "gateway": result}
    
    except ValueError as e:
        logger.error(f"Invalid MQTT event: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing MQTT event: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Certificate management endpoint
@router.post("/api/gateways/{gateway_id}/certificate", response_model=EventResponse)
async def update_certificate_status(
    gateway_id: str,
    status: bool = True,
    worker: BaseWorker = Depends(get_worker)
):
    """Update certificate status for a gateway
    
    Updates the certificate status for a gateway.
    This simulates certificate installation or removal.
    
    Args:
        status: True for installed, False for removed
    """
    try:
        # Create a simulated status MQTT event
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload={
                "certificate_status": "installed" if status else "removed",
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error updating certificate status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Connection management endpoints
@router.post("/api/gateways/{gateway_id}/connect", response_model=EventResponse)
async def connect_gateway(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Connect a gateway
    
    Simulates a gateway connecting to MQTT broker.
    This transitions the gateway to the CONNECTED state if it has certificates.
    """
    try:
        # Create a simulated status MQTT event for connection
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload={
                "status": "online",
                "certificate_status": "installed",
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error connecting gateway: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/gateways/{gateway_id}/disconnect", response_model=EventResponse)
async def disconnect_gateway(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Disconnect a gateway
    
    Simulates a gateway disconnecting from MQTT broker.
    This transitions the gateway to the DISCONNECTED state.
    """
    try:
        # Create a simulated status MQTT event for disconnection
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload={
                "status": "offline",
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error disconnecting gateway: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Simplified endpoint for sending heartbeats
@router.post("/api/gateways/{gateway_id}/heartbeat", response_model=EventResponse)
async def send_heartbeat(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Send a heartbeat for a gateway
    
    Simulates a heartbeat event from the gateway.
    This is a convenience endpoint for testing.
    """
    try:
        # Create a simulated heartbeat MQTT event
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="heartbeat",
            update_type=GatewayUpdateType.HEARTBEAT,
            payload={
                "uptime": "3600s",
                "memory": "64MB",
                "cpu": "5%",
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error sending heartbeat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Status update endpoint (deprecated but kept for backward compatibility)
@router.post("/api/gateways/{gateway_id}/status/{status}", response_model=EventResponse)
async def update_status(
    gateway_id: str,
    status: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Update the status of a gateway
    
    Simulates a status update event from the gateway.
    This is a convenience endpoint for testing.
    
    Note: This endpoint is deprecated, use /connect and /disconnect instead.
    """
    try:
        # Create a simulated status MQTT event
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload={
                "status": status,
                "health": "good" if status == "online" else "warning",
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error updating status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Metrics update endpoint
@router.post("/api/gateways/{gateway_id}/metrics", response_model=EventResponse)
async def update_metrics(
    gateway_id: str,
    uptime: Optional[str] = None,
    memory: Optional[str] = None,
    cpu: Optional[str] = None,
    worker: BaseWorker = Depends(get_worker)
):
    """Update metrics for a gateway
    
    Simulates a metrics update event from the gateway.
    This is a convenience endpoint for testing.
    """
    try:
        # Build metrics payload
        payload = {
            "timestamp": datetime.now().isoformat()
        }
        
        # Add optional metrics
        if uptime:
            payload["uptime"] = uptime
        if memory:
            payload["memory"] = memory
        if cpu:
            payload["cpu"] = cpu
        
        # Create a simulated status MQTT event
        mqtt_event = MQTTEventRequest(
            gateway_id=gateway_id,
            event_type="status",
            update_type=GatewayUpdateType.STATUS,
            payload=payload
        )
        
        # Process using the same logic as MQTT events
        return await process_mqtt_event(mqtt_event, worker)
    except Exception as e:
        logger.error(f"Error updating metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Gateway reset endpoint
@router.post("/api/gateways/{gateway_id}/reset", response_model=EventResponse)
async def reset_gateway(
    gateway_id: str,
    worker: BaseWorker = Depends(get_worker)
):
    """Reset a gateway
    
    Resets a gateway by disconnecting it and then reconnecting it.
    This is useful for testing or recovering from error states.
    """
    try:
        # First disconnect
        disconnect_result = await disconnect_gateway(gateway_id, worker)
        
        # Then wait a moment (in a real system)
        # In this API call, we'll just proceed immediately
        
        # Then connect again
        connect_result = await connect_gateway(gateway_id, worker)
        
        # Return the final result
        return connect_result
    except Exception as e:
        logger.error(f"Error resetting gateway: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Configuration management endpoints
@router.post("/api/config", status_code=status.HTTP_201_CREATED)
async def create_config_update(
    gateway_id: str = Form(...),
    yaml_config: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    worker: BaseWorker = Depends(get_worker)
):
    """Create a new configuration update for a gateway
    
    This endpoint accepts either a text configuration or a file upload.
    At least one of yaml_config or file must be provided.
    
    Form Parameters:
        gateway_id: ID of the gateway to configure
        yaml_config: YAML configuration as text (optional)
        file: Uploaded YAML file (optional)
    """
    try:
        # Check that at least one config source is provided
        if not yaml_config and not file:
            raise HTTPException(
                status_code=400,
                detail="Either yaml_config parameter or file upload is required"
            )
        
        # If file is provided, read its contents
        if file:
            logger.info(f"Reading configuration from uploaded file {file.filename}")
            file_content = await file.read()
            yaml_config = file_content.decode('utf-8')
        
        # Create a unique update ID
        update_id = f"config-{gateway_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Create a hash of the config for storage and comparison
        hash_obj = hashlib.sha256(yaml_config.encode('utf-8'))
        config_hash = hash_obj.hexdigest()
        
        # Validate YAML
        try:
            yaml.safe_load(yaml_config)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML configuration: {str(e)}")
        
        # Create task data for the worker
        task_data = {
            "type": "config_update",
            "update_id": update_id,
            "gateway_id": gateway_id,
            "yaml_config": yaml_config,
            "config_hash": config_hash,
            "timestamp": datetime.now().isoformat()
        }
        
        # Process the task
        logger.info(f"Creating configuration update {update_id} for gateway {gateway_id}")
        result = await worker.process_task(task_data)
        
        return {
            "status": "created",
            "update_id": update_id,
            "gateway_id": gateway_id,
            "config_hash": config_hash
        }
    
    except ValueError as e:
        logger.error(f"Invalid config data: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating config update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/config", response_model=ConfigUpdateList)
async def list_config_updates(
    gateway_id: Optional[str] = None,
    include_completed: bool = True,
    worker: BaseWorker = Depends(get_worker)
):
    """List configuration updates"""
    try:
        updates = worker.list_config_updates(gateway_id, include_completed)
        return {"updates": updates, "total": len(updates)}
    except Exception as e:
        logger.error(f"Error listing config updates: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/config/{update_id}", response_model=ConfigUpdateStatus)
async def get_config_update(
    update_id: str,
    include_config: bool = False,
    worker: BaseWorker = Depends(get_worker)
):
    """Get details of a specific configuration update"""
    try:
        update = worker.get_config_update(update_id, include_config)
        if not update:
            raise HTTPException(status_code=404, detail="Configuration update not found")
        return update
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting config update: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/config/gateway/{gateway_id}/latest", response_model=ConfigUpdateStatus)
async def get_latest_config(
    gateway_id: str,
    include_config: bool = True,
    worker: BaseWorker = Depends(get_worker)
):
    """Get the latest configuration for a gateway"""
    try:
        update = worker.get_latest_config(gateway_id, include_config)
        if not update:
            raise HTTPException(status_code=404, detail="No configuration found for this gateway")
        return update
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest config: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/config/mqtt/events", response_model=ConfigUpdateResponse)
async def process_config_mqtt_event(
    event: Dict[str, Any],
    worker: BaseWorker = Depends(get_worker)
):
    """Process MQTT events related to configuration updates"""
    try:
        logger.info(f"Received config MQTT event: {event}")
        
        # Extract required fields
        topic = event.get("topic")
        if not topic:
            raise HTTPException(status_code=400, detail="Topic is required")
        
        gateway_id = None
        event_type = None
        update_id = None
        
        # Extract info from topic: gateway/{gateway_id}/config/{action}
        # or config/{action}
        topic_parts = topic.split('/')
        
        if topic.startswith("gateway/"):
            if len(topic_parts) >= 4:
                gateway_id = topic_parts[1]
                event_type = topic_parts[3]  # e.g., "update", "delivered"
        elif topic.startswith("config/"):
            if len(topic_parts) >= 2:
                event_type = topic_parts[1]  # e.g., "new", "delivered"
        
        # Extract update_id from payload
        payload = event.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                payload = {"raw": payload}
        
        update_id = payload.get("update_id")
        
        # If we don't have an update_id but have gateway_id, try to find active update
        if not update_id and gateway_id:
            active_updates = worker.list_config_updates(
                gateway_id=gateway_id, 
                include_completed=False
            )
            if active_updates:
                update_id = active_updates[0].get("update_id")
        
        if not update_id:
            logger.warning(f"Could not determine update_id for topic {topic}")
            return {"status": "ignored", "reason": "No update_id found"}
        
        # Create task data with mqtt_config prefix to distinguish from gateway MQTT events
        task_data = {
            "type": "mqtt_config_event",
            "update_id": update_id,
            "gateway_id": gateway_id,
            "event_type": event_type,
            "topic": topic,
            "payload": payload,
            "timestamp": datetime.now().isoformat()
        }
        
        # Process the task using the existing task processor
        result = await worker.process_task(task_data)
        
        # Convert result to the expected format
        if isinstance(result, dict) and "status" not in result:
            result = {"status": "processed", "update": result}
            
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing config MQTT event: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))