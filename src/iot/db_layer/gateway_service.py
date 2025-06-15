"""
Gateway service for NoSQL document operations.
Replaces SQL gateway operations with document-based operations.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class GatewayService:
    """Service for managing gateway documents"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def create_gateway(
        self,
        gateway_id: str,
        name: str,
        location: str,
        status: str = "created",
        **kwargs
    ) -> Dict[str, Any]:
        """Create a new gateway document"""
        try:
            timestamp = datetime.now().isoformat()
            
            gateway_doc = {
                "gateway_id": gateway_id,
                "name": name,
                "location": location,
                "status": status,
                "last_updated": timestamp,
                "last_heartbeat": None,
                "uptime": None,
                "health": None,
                "error": None,
                "created_at": timestamp,
                "connected_at": None,
                "disconnected_at": None,
                "deleted_at": None,
                "certificate_info": None
            }
            
            # Add any additional fields from kwargs
            gateway_doc.update(kwargs)
            
            self.adapter.create_document('gateways', gateway_doc)
            logger.info(f"Created gateway document: {gateway_id}")
            return gateway_doc
            
        except Exception as e:
            logger.error(f"Error creating gateway document: {e}")
            raise
    
    def get_gateway(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        """Get gateway document by ID"""
        try:
            return self.adapter.get_document('gateways', gateway_id)
        except Exception as e:
            logger.error(f"Error getting gateway document: {e}")
            raise
    
    def update_gateway(
        self,
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
        **kwargs
    ) -> bool:
        """Update gateway document (equivalent to update_gateway_read_model)"""
        try:
            # Format timestamps to YYYY-MM-DDThh:mm:ss format without microseconds
            def format_timestamp(ts):
                if not ts:
                    return ts
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
                    return dt.strftime("%Y-%m-%dT%H:%M:%S")
                except:
                    return ts
            
            # Format all timestamps
            last_updated = format_timestamp(last_updated)
            last_heartbeat = format_timestamp(last_heartbeat)
            created_at = format_timestamp(created_at)
            connected_at = format_timestamp(connected_at)
            disconnected_at = format_timestamp(disconnected_at)
            deleted_at = format_timestamp(deleted_at)
            
            # Format certificate_info timestamps if present
            if certificate_info and isinstance(certificate_info, dict):
                if "installed_at" in certificate_info:
                    certificate_info["installed_at"] = format_timestamp(certificate_info["installed_at"])
                if "removed_at" in certificate_info:
                    certificate_info["removed_at"] = format_timestamp(certificate_info["removed_at"])
            
            # Format error as JSON if it's not already
            if error and not (isinstance(error, str) and error.startswith("{")):
                if "offline" in str(error).lower():
                    error = json.dumps({"status": "reported offline"})
                else:
                    error = json.dumps({"message": str(error)})
            
            gateway_doc = {
                "gateway_id": gateway_id,
                "name": name,
                "location": location,
                "status": status,
                "last_updated": last_updated,
                "last_heartbeat": last_heartbeat,
                "uptime": uptime,
                "health": health,
                "error": error,
                "created_at": created_at,
                "connected_at": connected_at,
                "disconnected_at": disconnected_at,
                "deleted_at": deleted_at,
                "certificate_info": certificate_info
            }
            
            # Add any additional fields from kwargs
            gateway_doc.update(kwargs)
            
            success = self.adapter.update_document('gateways', gateway_id, gateway_doc)
            if success:
                logger.info(f"Updated gateway document: {gateway_id} with status={status}")
            else:
                logger.warning(f"Gateway document not found for update: {gateway_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error updating gateway document: {e}")
            raise
    
    def delete_gateway(self, gateway_id: str) -> bool:
        """Delete gateway document"""
        try:
            success = self.adapter.delete_document('gateways', gateway_id)
            if success:
                logger.info(f"Deleted gateway document: {gateway_id}")
            else:
                logger.warning(f"Gateway document not found for deletion: {gateway_id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting gateway document: {e}")
            raise
    
    def list_gateways(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """List all gateway documents (equivalent to list_gateways)"""
        try:
            if include_deleted:
                # Get all gateways
                gateways = self.adapter.query_documents('gateways', {})
            else:
                # Get gateways excluding deleted ones
                gateways = self.adapter.query_documents('gateways', {})
                gateways = [g for g in gateways if g.get('status') != 'deleted']
            
            # Parse certificate_info from JSON string if needed
            for gateway in gateways:
                if gateway.get('certificate_info') and isinstance(gateway['certificate_info'], str):
                    try:
                        gateway['certificate_info'] = json.loads(gateway['certificate_info'])
                    except:
                        gateway['certificate_info'] = None
            
            return gateways
            
        except Exception as e:
            logger.error(f"Error listing gateway documents: {e}")
            raise
    
    def get_gateway_status(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        """Get gateway status (equivalent to get_gateway_status)"""
        try:
            gateway = self.get_gateway(gateway_id)
            if not gateway:
                return None
            
            # Parse certificate_info from JSON string if needed
            if gateway.get('certificate_info') and isinstance(gateway['certificate_info'], str):
                try:
                    gateway['certificate_info'] = json.loads(gateway['certificate_info'])
                except:
                    gateway['certificate_info'] = None
            
            return gateway
            
        except Exception as e:
            logger.error(f"Error getting gateway status: {e}")
            return None
    
    def get_connected_gateways(self) -> List[Dict[str, Any]]:
        """Get all gateways with connected status"""
        try:
            return self.adapter.query_documents('gateways', {'status': 'connected'})
        except Exception as e:
            logger.error(f"Error getting connected gateways: {e}")
            raise
    
    def update_gateway_field(
        self,
        gateway_id: str,
        field: str,
        value: Any,
        update_timestamp: bool = True
    ) -> bool:
        """Update a specific field in gateway document"""
        try:
            gateway = self.get_gateway(gateway_id)
            if not gateway:
                return False
            
            gateway[field] = value
            if update_timestamp:
                gateway['last_updated'] = datetime.now().isoformat()
            
            return self.adapter.update_document('gateways', gateway_id, gateway)
            
        except Exception as e:
            logger.error(f"Error updating gateway field: {e}")
            raise
    
    def update_gateway_heartbeat(self, gateway_id: str, timestamp: Optional[str] = None) -> bool:
        """Update gateway heartbeat timestamp"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        return self.update_gateway_field(gateway_id, 'last_heartbeat', timestamp)
    
    def update_gateway_health(self, gateway_id: str, health_data: Dict[str, Any]) -> bool:
        """Update gateway health metrics"""
        try:
            gateway = self.get_gateway(gateway_id)
            if not gateway:
                return False
            
            # Update health fields
            for field in ['health', 'uptime', 'memory', 'cpu']:
                if field in health_data:
                    gateway[field] = health_data[field]
            
            gateway['last_updated'] = datetime.now().isoformat()
            
            return self.adapter.update_document('gateways', gateway_id, gateway)
            
        except Exception as e:
            logger.error(f"Error updating gateway health: {e}")
            raise
    
    def set_gateway_error(self, gateway_id: str, error_message: str) -> bool:
        """Set gateway error message"""
        if "offline" in error_message.lower():
            error_json = json.dumps({"status": "reported offline"})
        else:
            error_json = json.dumps({"message": error_message})
        
        return self.update_gateway_field(gateway_id, 'error', error_json)
    
    def clear_gateway_error(self, gateway_id: str) -> bool:
        """Clear gateway error"""
        return self.update_gateway_field(gateway_id, 'error', None)
    
    def update_certificate_info(
        self,
        gateway_id: str,
        cert_info: Dict[str, Any]
    ) -> bool:
        """Update gateway certificate information"""
        return self.update_gateway_field(gateway_id, 'certificate_info', cert_info)


# Global service instance
_gateway_service = None

def get_gateway_service(db_path: str = "recycling.db") -> GatewayService:
    """Get or create gateway service instance"""
    global _gateway_service
    if _gateway_service is None or _gateway_service.db_path != db_path:
        _gateway_service = GatewayService(db_path)
    return _gateway_service