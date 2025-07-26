"""
ECS Auto-scaling Manager
Manages auto-scaling lifecycle and cleanup for ECS services.
"""
import logging
from typing import Dict, Any, List, Optional
from deployment.aws.infrastructure.ecs_scaling import ECSAutoScaler, ECSScalingConfig

logger = logging.getLogger(__name__)


class ECSScalingManager:
    """Manages multiple ECS auto-scalers and their lifecycle."""
    
    def __init__(self):
        self.autoscalers: Dict[str, ECSAutoScaler] = {}
        self.scaling_configs: Dict[str, Dict[str, Any]] = {}
    
    def register_autoscaler(self, service_name: str, autoscaler: ECSAutoScaler, 
                          config: Dict[str, Any]) -> None:
        """Register an auto-scaler for tracking and cleanup."""
        self.autoscalers[service_name] = autoscaler
        self.scaling_configs[service_name] = config
        logger.info(f"Registered auto-scaler for service: {service_name}")
    
    def get_autoscaler(self, service_name: str) -> Optional[ECSAutoScaler]:
        """Get auto-scaler for a specific service."""
        return self.autoscalers.get(service_name)
    
    def get_all_scaling_status(self) -> Dict[str, Dict[str, Any]]:
        """Get scaling status for all registered services."""
        status = {}
        
        for service_name, autoscaler in self.autoscalers.items():
            try:
                service_status = autoscaler.get_scaling_status()
                service_status['config'] = self.scaling_configs.get(service_name, {})
                status[service_name] = service_status
            except Exception as e:
                logger.error(f"Failed to get status for {service_name}: {e}")
                status[service_name] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return status
    
    def cleanup_all_autoscalers(self) -> Dict[str, bool]:
        """Clean up all registered auto-scalers."""
        cleanup_results = {}
        
        for service_name, autoscaler in self.autoscalers.items():
            try:
                logger.info(f"Cleaning up auto-scaler for {service_name}")
                autoscaler.cleanup_auto_scaling()
                cleanup_results[service_name] = True
                logger.info(f"✅ Successfully cleaned up auto-scaler for {service_name}")
            except Exception as e:
                logger.error(f"❌ Failed to cleanup auto-scaler for {service_name}: {e}")
                cleanup_results[service_name] = False
        
        # Clear internal tracking
        self.autoscalers.clear()
        self.scaling_configs.clear()
        
        return cleanup_results
    
    def cleanup_service_autoscaler(self, service_name: str) -> bool:
        """Clean up auto-scaler for a specific service."""
        if service_name not in self.autoscalers:
            logger.warning(f"No auto-scaler found for service: {service_name}")
            return False
        
        try:
            autoscaler = self.autoscalers[service_name]
            autoscaler.cleanup_auto_scaling()
            
            # Remove from tracking
            del self.autoscalers[service_name]
            if service_name in self.scaling_configs:
                del self.scaling_configs[service_name]
            
            logger.info(f"✅ Successfully cleaned up auto-scaler for {service_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to cleanup auto-scaler for {service_name}: {e}")
            return False


# Global scaling manager instance
_scaling_manager_instance: Optional[ECSScalingManager] = None


def get_scaling_manager() -> ECSScalingManager:
    """Get or create global scaling manager instance."""
    global _scaling_manager_instance
    if _scaling_manager_instance is None:
        _scaling_manager_instance = ECSScalingManager()
    return _scaling_manager_instance


def cleanup_all_scaling() -> Dict[str, bool]:
    """Convenience function to cleanup all auto-scaling."""
    manager = get_scaling_manager()
    return manager.cleanup_all_autoscalers()