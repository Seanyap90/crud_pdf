from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class BaseWorker(ABC):
    """Base class for worker implementations across different environments"""
    
    @abstractmethod
    async def start(self) -> None:
        """Start the worker"""
        pass
        
    @abstractmethod
    async def stop(self) -> None:
        """Stop the worker"""
        pass
        
    @abstractmethod
    async def process_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a task
        
        Args:
            task_data: Data for the task to be processed
            
        Returns:
            Result of the task processing
        """
        pass
    
    @abstractmethod
    def append_event(self, event: Dict[str, Any]) -> None:
        """Append an event to the event store
        
        Args:
            event: The event to append
        """
        pass
    
    @abstractmethod
    def read_events(self, aggregate_id: str) -> List[Dict[str, Any]]:
        """Read all events for an aggregate
        
        Args:
            aggregate_id: ID of the aggregate
            
        Returns:
            List of events for the aggregate
        """
        pass
    
    @abstractmethod
    def get_current_version(self, aggregate_id: str) -> int:
        """Get the current version of an aggregate
        
        Args:
            aggregate_id: ID of the aggregate
            
        Returns:
            Current version of the aggregate
        """
        pass
    
    @abstractmethod
    def update_read_model(self, gateway_id: str) -> None:
        """Update the read model for a gateway
        
        Args:
            gateway_id: ID of the gateway
        """
        pass
    
    @abstractmethod
    def get_gateway_status(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a gateway
        
        Args:
            gateway_id: ID of the gateway
            
        Returns:
            Current status of the gateway
        """
        pass
    
    @abstractmethod
    def list_gateways(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """List all gateways
        
        Args:
            include_deleted: Whether to include deleted gateways
            
        Returns:
            List of all gateways
        """
        pass
    
    @abstractmethod
    async def check_and_process_timeouts(self) -> None:
        """Check for and process any gateway timeouts
        
        This method should check for gateways that have timed out during connection
        or response phases and update their state accordingly.
        """
        pass
    
    @abstractmethod
    def update_config_read_model(self, update_id: str) -> None:
        """Update the read model for a configuration update
        
        Args:
            update_id: ID of the configuration update
        """
        pass
    
    @abstractmethod
    def get_config_update(self, update_id: str, include_config: bool = False) -> Optional[Dict[str, Any]]:
        """Get a configuration update
        
        Args:
            update_id: ID of the configuration update
            include_config: Whether to include the full YAML configuration
            
        Returns:
            Configuration update data
        """
        pass
    
    @abstractmethod
    def list_config_updates(
        self, 
        gateway_id: Optional[str] = None, 
        include_completed: bool = True
    ) -> List[Dict[str, Any]]:
        """List configuration updates
        
        Args:
            gateway_id: Filter by gateway ID
            include_completed: Whether to include completed updates
            
        Returns:
            List of configuration updates
        """
        pass
    
    @abstractmethod
    def get_latest_config(self, gateway_id: str, include_config: bool = True) -> Optional[Dict[str, Any]]:
        """Get the latest configuration for a gateway
        
        Args:
            gateway_id: ID of the gateway
            include_config: Whether to include the full YAML configuration
            
        Returns:
            Latest configuration update data
        """
        pass
