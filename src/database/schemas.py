"""
JSON schemas for NoSQL document validation.
This module defines schemas for validating documents in the NoSQL collections.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
import jsonschema
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class ExtractionStatus(str, Enum):
    """Enumeration for possible extraction statuses"""
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class DeviceStatus(str, Enum):
    """Enumeration for device statuses"""
    ONLINE = 'online'
    OFFLINE = 'offline'
    MAINTENANCE = 'maintenance'
    ERROR = 'error'


class GatewayStatus(str, Enum):
    """Enumeration for gateway statuses"""
    CREATED = 'created'
    CONNECTED = 'connected'
    DISCONNECTED = 'disconnected'
    MAINTENANCE = 'maintenance'
    ERROR = 'error'


# Vendor embedded schema for invoices
class VendorSchema(BaseModel):
    """Schema for embedded vendor in invoice documents"""
    vendor_id: str = Field(..., description="Unique vendor identifier")
    vendor_name: str = Field(..., min_length=1, max_length=100, description="Vendor name")
    created_at: Optional[datetime] = Field(None, description="Vendor creation timestamp")
    is_active: bool = Field(True, description="Whether vendor is active")


# Category embedded schema for invoices
class CategorySchema(BaseModel):
    """Schema for embedded category in invoice documents"""
    category_id: int = Field(..., description="Category identifier")
    category_name: str = Field(..., min_length=1, max_length=50, description="Category name")
    description: Optional[str] = Field(None, description="Category description")


# Main invoice document schema
class VendorInvoiceSchema(BaseModel):
    """Schema for vendor invoice documents"""
    invoice_id: int = Field(..., description="Unique invoice identifier")
    vendor: VendorSchema = Field(..., description="Embedded vendor information")
    category: Optional[CategorySchema] = Field(None, description="Embedded category information")
    invoice_number: str = Field(..., min_length=1, max_length=50, description="Invoice number")
    invoice_date: datetime = Field(..., description="Invoice date")
    upload_date: datetime = Field(..., description="Upload timestamp")
    filename: str = Field(..., min_length=1, max_length=255, description="Original filename")
    filepath: str = Field(..., min_length=1, max_length=500, description="File storage path")
    reported_weight_kg: Optional[Decimal] = Field(None, ge=0, description="Reported weight in kg")
    unit_price: Optional[Decimal] = Field(None, ge=0, description="Unit price")
    total_amount: Optional[Decimal] = Field(None, ge=0, description="Total amount")
    extraction_status: ExtractionStatus = Field(ExtractionStatus.PENDING, description="Processing status")
    processing_date: Optional[datetime] = Field(None, description="Processing start timestamp")
    completion_date: Optional[datetime] = Field(None, description="Processing completion timestamp")
    error_message: Optional[str] = Field(None, description="Error message if processing failed")

    @field_validator('invoice_number')
    def unique_invoice_per_vendor(cls, v):
        """Ensure invoice number uniqueness per vendor (enforced at application level)"""
        return v


# Gateway document schema
class GatewaySchema(BaseModel):
    """Schema for gateway documents"""
    gateway_id: str = Field(..., description="Unique gateway identifier")
    name: str = Field(..., min_length=1, description="Gateway name")
    location: str = Field(..., min_length=1, description="Gateway location")
    status: GatewayStatus = Field(..., description="Gateway status")
    last_updated: Optional[datetime] = Field(None, description="Last update timestamp")
    last_heartbeat: Optional[datetime] = Field(None, description="Last heartbeat timestamp")
    uptime: Optional[str] = Field(None, description="Gateway uptime")
    health: Optional[str] = Field(None, description="Health status")
    error: Optional[str] = Field(None, description="Error message")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    connected_at: Optional[datetime] = Field(None, description="Connection timestamp")
    disconnected_at: Optional[datetime] = Field(None, description="Disconnection timestamp")
    deleted_at: Optional[datetime] = Field(None, description="Deletion timestamp")
    certificate_info: Optional[Dict[str, Any]] = Field(None, description="Certificate information")


# Device info embedded in measurements
class DeviceInfoSchema(BaseModel):
    """Schema for embedded device info in measurements"""
    device_id: str = Field(..., description="Device identifier")
    gateway_id: str = Field(..., description="Parent gateway identifier")
    device_type: str = Field(..., description="Type of device")
    name: Optional[str] = Field(None, description="Device name")
    location: Optional[str] = Field(None, description="Device location")
    status: DeviceStatus = Field(..., description="Device status")


# Device document schema
class DeviceSchema(BaseModel):
    """Schema for device documents"""
    device_id: str = Field(..., description="Unique device identifier")
    gateway_id: str = Field(..., description="Parent gateway identifier")
    device_type: str = Field(..., min_length=1, description="Type of device")
    name: Optional[str] = Field(None, description="Device name")
    location: Optional[str] = Field(None, description="Device location")
    status: DeviceStatus = Field(..., description="Device status")
    last_updated: Optional[datetime] = Field(None, description="Last update timestamp")
    last_measurement: Optional[datetime] = Field(None, description="Last measurement timestamp")
    last_config_fetch: Optional[datetime] = Field(None, description="Last config fetch timestamp")
    config_version: Optional[str] = Field(None, description="Configuration version")
    config_hash: Optional[str] = Field(None, description="Configuration hash")
    device_config: Optional[Dict[str, Any]] = Field(None, description="Device configuration")


# Measurement document schema
class MeasurementSchema(BaseModel):
    """Schema for measurement documents with embedded device info"""
    measurement_id: int = Field(..., description="Unique measurement identifier")
    device_info: DeviceInfoSchema = Field(..., description="Embedded device information")
    measurement_type: str = Field(..., min_length=1, description="Type of measurement")
    timestamp: datetime = Field(..., description="Measurement timestamp")
    processed: bool = Field(False, description="Whether measurement is processed")
    uploaded_to_cloud: bool = Field(False, description="Whether uploaded to cloud")
    payload: Dict[str, Any] = Field(..., description="Measurement payload data")


# Configuration update document schema
class ConfigUpdateSchema(BaseModel):
    """Schema for configuration update documents"""
    update_id: str = Field(..., description="Unique update identifier")
    gateway_id: str = Field(..., description="Target gateway identifier")
    state: str = Field(..., description="Update state")
    version: Optional[str] = Field(None, description="Configuration version")
    config_hash: Optional[str] = Field(None, description="Configuration hash")
    config_version: Optional[str] = Field(None, description="Configuration version string")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    last_updated: Optional[datetime] = Field(None, description="Last update timestamp")
    published_at: Optional[datetime] = Field(None, description="Published timestamp")
    requested_at: Optional[datetime] = Field(None, description="Requested timestamp")
    sent_at: Optional[datetime] = Field(None, description="Sent timestamp")
    delivered_at: Optional[datetime] = Field(None, description="Delivered timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completed timestamp")
    failed_at: Optional[datetime] = Field(None, description="Failed timestamp")
    delivery_status: Optional[str] = Field(None, description="Delivery status")
    error: Optional[str] = Field(None, description="Error message")
    yaml_config: Optional[str] = Field(None, description="YAML configuration content")


# JSON Schema validators (for runtime validation)
VENDOR_INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_id": {"type": "integer"},
        "vendor": {
            "type": "object",
            "properties": {
                "vendor_id": {"type": "string", "minLength": 1},
                "vendor_name": {"type": "string", "minLength": 1, "maxLength": 100},
                "created_at": {"type": ["string", "null"], "format": "date-time"},
                "is_active": {"type": "boolean"}
            },
            "required": ["vendor_id", "vendor_name"],
            "additionalProperties": False
        },
        "category": {
            "type": ["object", "null"],
            "properties": {
                "category_id": {"type": "integer"},
                "category_name": {"type": "string", "minLength": 1, "maxLength": 50},
                "description": {"type": ["string", "null"]}
            },
            "required": ["category_id", "category_name"],
            "additionalProperties": False
        },
        "invoice_number": {"type": "string", "minLength": 1, "maxLength": 50},
        "invoice_date": {"type": "string", "format": "date-time"},
        "upload_date": {"type": "string", "format": "date-time"},
        "filename": {"type": "string", "minLength": 1, "maxLength": 255},
        "filepath": {"type": "string", "minLength": 1, "maxLength": 500},
        "reported_weight_kg": {"type": ["number", "null"], "minimum": 0},
        "unit_price": {"type": ["number", "null"], "minimum": 0},
        "total_amount": {"type": ["number", "null"], "minimum": 0},
        "extraction_status": {"type": "string", "enum": ["pending", "processing", "completed", "failed"]},
        "processing_date": {"type": ["string", "null"], "format": "date-time"},
        "completion_date": {"type": ["string", "null"], "format": "date-time"},
        "error_message": {"type": ["string", "null"]}
    },
    "required": ["invoice_id", "vendor", "invoice_number", "invoice_date", "upload_date", "filename", "filepath"],
    "additionalProperties": False
}

GATEWAY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "gateway_id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "location": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": ["created", "connected", "disconnected", "maintenance", "error"]},
        "last_updated": {"type": ["string", "null"], "format": "date-time"},
        "last_heartbeat": {"type": ["string", "null"], "format": "date-time"},
        "uptime": {"type": ["string", "null"]},
        "health": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "created_at": {"type": ["string", "null"], "format": "date-time"},
        "connected_at": {"type": ["string", "null"], "format": "date-time"},
        "disconnected_at": {"type": ["string", "null"], "format": "date-time"},
        "deleted_at": {"type": ["string", "null"], "format": "date-time"},
        "certificate_info": {"type": ["object", "string", "null"]}
    },
    "required": ["gateway_id", "name", "location", "status"],
    "additionalProperties": False
}

DEVICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "device_id": {"type": "string", "minLength": 1},
        "gateway_id": {"type": "string", "minLength": 1},
        "device_type": {"type": "string", "minLength": 1},
        "name": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": ["online", "offline", "maintenance", "error"]},
        "last_updated": {"type": ["string", "null"], "format": "date-time"},
        "last_measurement": {"type": ["string", "null"], "format": "date-time"},
        "last_config_fetch": {"type": ["string", "null"], "format": "date-time"},
        "config_version": {"type": ["string", "null"]},
        "config_hash": {"type": ["string", "null"]},
        "device_config": {"type": ["object", "null"]}
    },
    "required": ["device_id", "gateway_id", "device_type", "status"],
    "additionalProperties": False
}

MEASUREMENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "measurement_id": {"type": "integer"},
        "device_info": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "minLength": 1},
                "gateway_id": {"type": "string", "minLength": 1},
                "device_type": {"type": "string", "minLength": 1},
                "name": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "status": {"type": "string", "enum": ["online", "offline", "maintenance", "error"]}
            },
            "required": ["device_id", "gateway_id", "device_type", "status"],
            "additionalProperties": False
        },
        "measurement_type": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "format": "date-time"},
        "processed": {"type": "boolean"},
        "uploaded_to_cloud": {"type": "boolean"},
        "payload": {"type": "object"}
    },
    "required": ["measurement_id", "device_info", "measurement_type", "timestamp", "payload"],
    "additionalProperties": False
}

CONFIG_UPDATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "update_id": {"type": "string", "minLength": 1},
        "gateway_id": {"type": "string", "minLength": 1},
        "state": {"type": "string", "minLength": 1},
        "version": {"type": ["string", "null"]},
        "config_hash": {"type": ["string", "null"]},
        "config_version": {"type": ["string", "null"]},
        "created_at": {"type": ["string", "null"], "format": "date-time"},
        "last_updated": {"type": ["string", "null"], "format": "date-time"},
        "published_at": {"type": ["string", "null"], "format": "date-time"},
        "requested_at": {"type": ["string", "null"], "format": "date-time"},
        "sent_at": {"type": ["string", "null"], "format": "date-time"},
        "delivered_at": {"type": ["string", "null"], "format": "date-time"},
        "completed_at": {"type": ["string", "null"], "format": "date-time"},
        "failed_at": {"type": ["string", "null"], "format": "date-time"},
        "delivery_status": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "yaml_config": {"type": ["string", "null"]}
    },
    "required": ["update_id", "gateway_id", "state"],
    "additionalProperties": False
}


def validate_vendor_invoice_document(document: Dict[str, Any]) -> None:
    """Validate a vendor invoice document against the schema"""
    jsonschema.validate(document, VENDOR_INVOICE_JSON_SCHEMA)


def validate_gateway_document(document: Dict[str, Any]) -> None:
    """Validate a gateway document against the schema"""
    jsonschema.validate(document, GATEWAY_JSON_SCHEMA)


def validate_device_document(document: Dict[str, Any]) -> None:
    """Validate a device document against the schema"""
    jsonschema.validate(document, DEVICE_JSON_SCHEMA)


def validate_measurement_document(document: Dict[str, Any]) -> None:
    """Validate a measurement document against the schema"""
    jsonschema.validate(document, MEASUREMENT_JSON_SCHEMA)


def validate_config_update_document(document: Dict[str, Any]) -> None:
    """Validate a config update document against the schema"""
    jsonschema.validate(document, CONFIG_UPDATE_JSON_SCHEMA)


# Schema mapping for easy access
DOCUMENT_SCHEMAS = {
    'vendor_invoices': VENDOR_INVOICE_JSON_SCHEMA,
    'gateways': GATEWAY_JSON_SCHEMA,
    'devices': DEVICE_JSON_SCHEMA,
    'measurements': MEASUREMENT_JSON_SCHEMA,
    'config_updates': CONFIG_UPDATE_JSON_SCHEMA
}

DOCUMENT_VALIDATORS = {
    'vendor_invoices': validate_vendor_invoice_document,
    'gateways': validate_gateway_document,
    'devices': validate_device_document,
    'measurements': validate_measurement_document,
    'config_updates': validate_config_update_document
}