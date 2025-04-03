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
