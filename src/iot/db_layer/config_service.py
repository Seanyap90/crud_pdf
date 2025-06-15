"""
Configuration service for NoSQL document operations.
Replaces SQL config_updates operations with document-based operations.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class ConfigService:
    """Service for managing configuration update documents"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def create_config_update(
        self,
        update_id: str,
        gateway_id: str,
        state: str = "stored",
        version: Optional[str] = None,
        config_hash: Optional[str] = None,
        config_version: Optional[str] = None,
        yaml_config: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a new configuration update document"""
        try:
            timestamp = datetime.now().isoformat()
            
            # Generate config version if not provided
            if not config_version:
                config_version = f"v{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            config_doc = {
                "update_id": update_id,
                "gateway_id": gateway_id,
                "state": state,
                "version": version,
                "config_hash": config_hash,
                "config_version": config_version,
                "created_at": timestamp,
                "last_updated": timestamp,
                "published_at": None,
                "requested_at": None,
                "sent_at": None,
                "delivered_at": None,
                "completed_at": None,
                "failed_at": None,
                "delivery_status": None,
                "error": None,
                "yaml_config": yaml_config  # Store for now, might remove later
            }
            
            # Add any additional fields from kwargs
            config_doc.update(kwargs)
            
            self.adapter.create_document('config_updates', config_doc)
            logger.info(f"Created config update document: {update_id}")
            return config_doc
            
        except Exception as e:
            logger.error(f"Error creating config update document: {e}")
            raise
    
    def get_config_update(self, update_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration update document by ID"""
        try:
            return self.adapter.get_document('config_updates', update_id)
        except Exception as e:
            logger.error(f"Error getting config update document: {e}")
            raise
    
    def update_config_update(
        self,
        update_id: str,
        gateway_id: str,
        state: str,
        version: Optional[str] = None,
        config_hash: Optional[str] = None,
        config_version: Optional[str] = None,
        created_at: Optional[str] = None,
        published_at: Optional[str] = None,
        requested_at: Optional[str] = None,
        sent_at: Optional[str] = None,
        delivered_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        failed_at: Optional[str] = None,
        last_updated: Optional[str] = None,
        delivery_status: Optional[str] = None,
        error: Optional[str] = None
    ) -> bool:
        """Update configuration update document (equivalent to update_config_update)"""
        try:
            # Get existing document or create new one
            config_doc = self.get_config_update(update_id)
            if not config_doc:
                # Create new document if it doesn't exist
                config_doc = {
                    "update_id": update_id,
                    "gateway_id": gateway_id,
                    "state": state,
                    "version": version,
                    "config_hash": config_hash,
                    "config_version": config_version or f"v{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "created_at": created_at or datetime.now().isoformat(),
                    "last_updated": last_updated or datetime.now().isoformat(),
                    "published_at": published_at,
                    "requested_at": requested_at,
                    "sent_at": sent_at,
                    "delivered_at": delivered_at,
                    "completed_at": completed_at,
                    "failed_at": failed_at,
                    "delivery_status": delivery_status,
                    "error": error
                }
                self.adapter.create_document('config_updates', config_doc)
                success = True
            else:
                # Update existing document
                config_doc.update({
                    "gateway_id": gateway_id,
                    "state": state,
                    "last_updated": last_updated or datetime.now().isoformat()
                })
                
                # Update optional fields only if provided
                if version is not None:
                    config_doc["version"] = version
                if config_hash is not None:
                    config_doc["config_hash"] = config_hash
                if config_version is not None:
                    config_doc["config_version"] = config_version
                if created_at is not None:
                    config_doc["created_at"] = created_at
                if published_at is not None:
                    config_doc["published_at"] = published_at
                if requested_at is not None:
                    config_doc["requested_at"] = requested_at
                if sent_at is not None:
                    config_doc["sent_at"] = sent_at
                if delivered_at is not None:
                    config_doc["delivered_at"] = delivered_at
                if completed_at is not None:
                    config_doc["completed_at"] = completed_at
                if failed_at is not None:
                    config_doc["failed_at"] = failed_at
                if delivery_status is not None:
                    config_doc["delivery_status"] = delivery_status
                if error is not None:
                    config_doc["error"] = error
                
                success = self.adapter.update_document('config_updates', update_id, config_doc)
            
            if success:
                logger.info(f"Updated config update document: {update_id} with state={state}")
            else:
                logger.warning(f"Failed to update config update document: {update_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error updating config update document: {e}")
            raise
    
    def list_config_updates(
        self,
        gateway_id: Optional[str] = None,
        include_completed: bool = True
    ) -> List[Dict[str, Any]]:
        """List configuration updates with optional filtering"""
        try:
            query = {}
            
            if gateway_id:
                query['gateway_id'] = gateway_id
            
            config_updates = self.adapter.query_documents('config_updates', query)
            
            # Filter out completed/failed if requested
            if not include_completed:
                config_updates = [
                    cu for cu in config_updates 
                    if cu.get('state') not in ['completed', 'failed']
                ]
            
            # Sort by last_updated descending
            config_updates.sort(
                key=lambda x: x.get('last_updated', ''), 
                reverse=True
            )
            
            return config_updates
            
        except Exception as e:
            logger.error(f"Error listing config update documents: {e}")
            raise
    
    def get_latest_config_for_gateway(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest completed configuration update for a gateway"""
        try:
            # Get all config updates for the gateway
            config_updates = self.list_config_updates(gateway_id=gateway_id)
            
            # First, try to get the most recent completed config
            completed_configs = [
                cu for cu in config_updates 
                if cu.get('state') == 'completed'
            ]
            
            if completed_configs:
                # Sort by completed_at timestamp
                completed_configs.sort(
                    key=lambda x: x.get('completed_at', ''), 
                    reverse=True
                )
                return completed_configs[0]
            
            # If no completed config, get the most recent one in any state
            if config_updates:
                return config_updates[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting latest config for gateway: {e}")
            return None
    
    def delete_config_update(self, update_id: str) -> bool:
        """Delete configuration update document"""
        try:
            success = self.adapter.delete_document('config_updates', update_id)
            if success:
                logger.info(f"Deleted config update document: {update_id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting config update document: {e}")
            raise
    
    def get_config_by_hash(self, config_hash: str) -> Optional[Dict[str, Any]]:
        """Get configuration metadata by hash"""
        try:
            config_updates = self.adapter.query_documents(
                'config_updates', 
                {'config_hash': config_hash}
            )
            
            if config_updates:
                # Sort by created_at and return the most recent
                config_updates.sort(
                    key=lambda x: x.get('created_at', ''), 
                    reverse=True
                )
                return config_updates[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting config by hash: {e}")
            return None
    
    def update_config_state(self, update_id: str, state: str, **kwargs) -> bool:
        """Update just the state and optionally other fields of a config update"""
        try:
            config = self.get_config_update(update_id)
            if not config:
                return False
            
            config['state'] = state
            config['last_updated'] = datetime.now().isoformat()
            
            # Update any additional fields provided
            for key, value in kwargs.items():
                if value is not None:
                    config[key] = value
            
            return self.adapter.update_document('config_updates', update_id, config)
            
        except Exception as e:
            logger.error(f"Error updating config state: {e}")
            raise
    
    def get_configs_by_state(self, state: str) -> List[Dict[str, Any]]:
        """Get all configuration updates in a specific state"""
        try:
            return self.adapter.query_documents('config_updates', {'state': state})
        except Exception as e:
            logger.error(f"Error getting configs by state: {e}")
            raise
    
    def count_config_updates(self, gateway_id: Optional[str] = None) -> int:
        """Count configuration updates, optionally filtered by gateway"""
        try:
            query = {}
            if gateway_id:
                query['gateway_id'] = gateway_id
            
            return self.adapter.count_documents('config_updates', query)
            
        except Exception as e:
            logger.error(f"Error counting config updates: {e}")
            raise


# Global service instance
_config_service = None

def get_config_service(db_path: str = "recycling.db") -> ConfigService:
    """Get or create config service instance"""
    global _config_service
    if _config_service is None or _config_service.db_path != db_path:
        _config_service = ConfigService(db_path)
    return _config_service