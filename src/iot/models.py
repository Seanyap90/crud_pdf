from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any, Union, List
from enum import Enum

class GatewayState(str, Enum):
    """Gateway states aligned with state machine"""
    CREATED = "created"
    CONNECTED = "connected" 
    DISCONNECTED = "disconnected"
    DELETED = "deleted"

class GatewayUpdateType(str, Enum):
    """Types of gateway updates"""
    HEARTBEAT = "heartbeat"
    STATUS = "status"

class CreateGatewayRequest(BaseModel):
    """Request model for creating a new gateway"""
    name: str = Field(..., min_length=3, max_length=50)
    location: str = Field(..., min_length=3, max_length=100)
    gateway_id: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Test Gateway",
                "location": "Server Room A"
            }
        }

class MQTTEventRequest(BaseModel):
    """Model for MQTT events received from external sources"""
    gateway_id: str = Field(..., description="ID of the gateway that sent the event")
    event_type: str = Field(..., description="Type of event (heartbeat, status, response, etc.)")
    payload: Union[Dict[str, Any], str, None] = Field(default=None, description="Event payload data")
    update_type: Optional[GatewayUpdateType] = Field(default=None, description="Update type for gateway updates")
    timestamp: Optional[str] = Field(default_factory=lambda: datetime.now().isoformat())
    
    # Optional fields that might be included from rules engine
    topic: Optional[str] = Field(default=None, description="Original MQTT topic (from rules engine)")

    class Config:
        json_schema_extra = {
            "example": {
                "gateway_id": "gateway-20250315-123456",
                "event_type": "heartbeat",
                "update_type": "heartbeat",
                "payload": {"status": "ok", "uptime": 3600},
                "timestamp": "2025-03-15T12:34:56.789012"
            }
        }

class GatewayStatus(BaseModel):
    """Model for gateway status response - streamlined fields"""
    gateway_id: str = Field(..., description="ID of the gateway")
    name: str = Field(..., description="Name of the gateway")
    location: str = Field(..., description="Location of the gateway")
    status: str = Field(..., description="Current status of the gateway")
    last_updated: str = Field(..., description="Timestamp of the last update")
    last_heartbeat: Optional[str] = Field(None, description="Timestamp of the last heartbeat")
    health: Optional[str] = Field(None, description="Health status of the gateway")
    uptime: Optional[str] = Field(None, description="Uptime of the gateway")
    error: Optional[str] = Field(None, description="Error message (if any)")
    created_at: Optional[str] = Field(None, description="When the gateway was created")
    connected_at: Optional[str] = Field(None, description="When the gateway was connected")
    disconnected_at: Optional[str] = Field(None, description="When the gateway was disconnected")
    deleted_at: Optional[str] = Field(None, description="When the gateway was deleted")
    certificate_info: Optional[Dict[str, Any]] = Field(None, description="Certificate information")

class EventResponse(BaseModel):
    """Model for event processing response"""
    status: str = Field(..., description="Status of the event processing")
    gateway: GatewayStatus = Field(..., description="Updated gateway status after event processing")

class ErrorResponse(BaseModel):
    """Model for error responses"""
    detail: str = Field(..., description="Error detail message")
    
class GatewayList(BaseModel):
    """Model for list of gateways response"""
    gateways: List[GatewayStatus] = Field(..., description="List of gateways")
    total: int = Field(..., description="Total number of gateways")
    
class ApiResponse(BaseModel):
    """Generic API response model"""
    success: bool = Field(..., description="Success status of the operation")
    message: str = Field(..., description="Response message")
    data: Optional[Dict[str, Any]] = Field(None, description="Response data")