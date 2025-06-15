"""
Device service for NoSQL document operations.
Replaces SQL device operations with document-based operations.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class DeviceService:
    """Service for managing device documents"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def register_device(
        self,
        device_id: str,
        gateway_id: str,
        device_type: str,
        name: Optional[str] = None,
        location: Optional[str] = None,
        status: str = "offline",
        config_version: Optional[str] = None,
        config_hash: Optional[str] = None,
        device_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Register a new device or update existing one (equivalent to register_end_device)"""
        try:
            timestamp = datetime.now().isoformat()
            
            # Check if device already exists
            existing_device = self.get_device(device_id)
            
            if existing_device:
                # Update existing device
                device_doc = existing_device.copy()
                device_doc.update({
                    "gateway_id": gateway_id,
                    "device_type": device_type,
                    "name": name or device_doc.get("name", f"Device {device_id}"),
                    "location": location or device_doc.get("location", "Unknown"),
                    "status": status,
                    "last_updated": timestamp
                })
                
                # Only update config fields if provided
                if config_version:
                    device_doc["config_version"] = config_version
                    device_doc["last_config_fetch"] = timestamp
                
                if config_hash:
                    device_doc["config_hash"] = config_hash
                
                if device_config:
                    device_doc["device_config"] = device_config
                
                self.adapter.update_document('devices', device_id, device_doc)
                logger.info(f"Updated device document: {device_id}")
            else:
                # Create new device
                device_doc = {
                    "device_id": device_id,
                    "gateway_id": gateway_id,
                    "device_type": device_type,
                    "name": name or f"Device {device_id}",
                    "location": location or "Unknown",
                    "status": status,
                    "last_updated": timestamp,
                    "last_measurement": None,
                    "last_config_fetch": timestamp if config_version else None,
                    "config_version": config_version,
                    "config_hash": config_hash,
                    "device_config": device_config
                }
                
                self.adapter.create_document('devices', device_doc)
                logger.info(f"Created device document: {device_id}")
            
            return device_doc
            
        except Exception as e:
            logger.error(f"Error registering device: {e}")
            raise
    
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get device document by ID"""
        try:
            return self.adapter.get_document('devices', device_id)
        except Exception as e:
            logger.error(f"Error getting device document: {e}")
            raise
    
    def update_device(self, device_id: str, updates: Dict[str, Any]) -> bool:
        """Update device document"""
        try:
            device = self.get_device(device_id)
            if not device:
                return False
            
            device.update(updates)
            device['last_updated'] = datetime.now().isoformat()
            
            success = self.adapter.update_document('devices', device_id, device)
            if success:
                logger.info(f"Updated device document: {device_id}")
            return success
            
        except Exception as e:
            logger.error(f"Error updating device document: {e}")
            raise
    
    def delete_device(self, device_id: str) -> bool:
        """Delete device document"""
        try:
            success = self.adapter.delete_document('devices', device_id)
            if success:
                logger.info(f"Deleted device document: {device_id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting device document: {e}")
            raise
    
    def list_devices(
        self,
        gateway_id: Optional[str] = None,
        include_offline: bool = True
    ) -> List[Dict[str, Any]]:
        """List devices with optional filtering (equivalent to list_end_devices)"""
        try:
            query = {}
            
            if gateway_id:
                query['gateway_id'] = gateway_id
            
            devices = self.adapter.query_documents('devices', query)
            
            # Filter offline devices if requested
            if not include_offline:
                devices = [d for d in devices if d.get('status') == 'online']
            
            # Parse device_config JSON if it's stored as string
            for device in devices:
                if device.get('device_config') and isinstance(device['device_config'], str):
                    try:
                        device['device_config'] = json.loads(device['device_config'])
                    except:
                        pass
            
            return devices
            
        except Exception as e:
            logger.error(f"Error listing device documents: {e}")
            raise
    
    def list_devices_by_gateway(self, gateway_id: str) -> List[Dict[str, Any]]:
        """List all devices for a specific gateway"""
        return self.list_devices(gateway_id=gateway_id)
    
    def update_device_status(self, device_id: str, status: str) -> bool:
        """Update device status"""
        try:
            return self.update_device(device_id, {'status': status})
        except Exception as e:
            logger.error(f"Error updating device status: {e}")
            raise
    
    def update_device_measurement_time(self, device_id: str, timestamp: Optional[str] = None) -> bool:
        """Update device last measurement time"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        try:
            return self.update_device(device_id, {'last_measurement': timestamp})
        except Exception as e:
            logger.error(f"Error updating device measurement time: {e}")
            raise
    
    def update_device_config(
        self,
        device_id: str,
        config_version: str,
        config_hash: Optional[str] = None,
        device_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update device configuration"""
        try:
            updates = {
                'config_version': config_version,
                'last_config_fetch': datetime.now().isoformat()
            }
            
            if config_hash:
                updates['config_hash'] = config_hash
            
            if device_config:
                updates['device_config'] = device_config
            
            return self.update_device(device_id, updates)
            
        except Exception as e:
            logger.error(f"Error updating device config: {e}")
            raise
    
    def update_device_parameter_set(
        self,
        device_id: str,
        parameter_set: str,
        parameters: Dict[str, Any]
    ) -> bool:
        """Update device parameter set assignment and parameters"""
        try:
            device = self.get_device(device_id)
            if not device:
                return False
            
            # Get current config or create new one
            config = device.get('device_config', {})
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except:
                    config = {}
            
            # Update parameter set
            config['active_parameter_set'] = parameter_set
            config['parameters'] = parameters
            
            return self.update_device(device_id, {
                'device_config': config,
                'last_config_fetch': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error updating device parameter set: {e}")
            raise
    
    def get_device_config(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get device configuration"""
        try:
            device = self.get_device(device_id)
            if not device:
                return None
            
            config = device.get('device_config')
            if isinstance(config, str):
                try:
                    return json.loads(config)
                except:
                    return None
            
            return config
            
        except Exception as e:
            logger.error(f"Error getting device config: {e}")
            return None
    
    def get_devices_by_type(self, device_type: str) -> List[Dict[str, Any]]:
        """Get all devices of a specific type"""
        try:
            return self.adapter.query_documents('devices', {'device_type': device_type})
        except Exception as e:
            logger.error(f"Error getting devices by type: {e}")
            raise
    
    def get_online_devices(self, gateway_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all online devices, optionally filtered by gateway"""
        try:
            query = {'status': 'online'}
            if gateway_id:
                query['gateway_id'] = gateway_id
            
            return self.adapter.query_documents('devices', query)
        except Exception as e:
            logger.error(f"Error getting online devices: {e}")
            raise


# Global service instance
_device_service = None

def get_device_service(db_path: str = "recycling.db") -> DeviceService:
    """Get or create device service instance"""
    global _device_service
    if _device_service is None or _device_service.db_path != db_path:
        _device_service = DeviceService(db_path)
    return _device_service