"""
Unified model loading interface for VLM workers.

Provides a consistent interface for model loading across different deployment modes:
- local-dev: Direct GPU access from local HuggingFace cache
- aws-mock: Docker volume mounts with container model loading
- aws-prod: EFS mounts with offline model loading
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional, Any
import os
import logging
from files_api.config.settings import get_settings

logger = logging.getLogger(__name__)

class ModelLoaderInterface(ABC):
    """Abstract interface for model loading implementations."""
    
    @abstractmethod
    def load_rag_model(self) -> Any:
        """Load the RAG (Retrieval Augmented Generation) model."""
        pass
    
    @abstractmethod
    def load_vlm_model(self) -> Tuple[Any, Any]:
        """Load the VLM (Vision Language Model) and processor."""
        pass
    
    @abstractmethod
    def validate_environment(self) -> bool:
        """Validate that the environment is ready for model loading."""
        pass

class ModelManager:
    """
    Unified model loading manager that delegates to mode-specific loaders.
    
    This class provides a consistent interface while internally using different
    loading strategies based on the deployment mode.
    """
    
    def __init__(self):
        self.settings = get_settings()
        self._loader: Optional[ModelLoaderInterface] = None
        self._rag_model = None
        self._vlm_model = None
        self._vlm_processor = None
    
    def _get_loader(self) -> ModelLoaderInterface:
        """Get the appropriate model loader for the current deployment mode."""
        if self._loader is None:
            if self.settings.deployment_mode == "local-dev":
                from vlm_workers.models.loader_local import LocalModelLoader
                self._loader = LocalModelLoader()
                logger.info("Using LocalModelLoader for local-dev mode")
            else:
                # Both aws-mock and aws-prod use container-based loading
                from vlm_workers.models.loader_container import ContainerModelLoader
                self._loader = ContainerModelLoader()
                logger.info(f"Using ContainerModelLoader for {self.settings.deployment_mode} mode")
        
        return self._loader
    
    def get_rag_model(self) -> Any:
        """
        Get the RAG model, loading it if necessary.
        
        Returns:
            The loaded RAG model instance
        """
        if self._rag_model is None:
            loader = self._get_loader()
            if loader.validate_environment():
                self._rag_model = loader.load_rag_model()
                logger.info("RAG model loaded successfully")
            else:
                logger.error("Environment validation failed for RAG model loading")
                raise RuntimeError("Environment not ready for model loading")
        
        return self._rag_model
    
    def get_vlm_model(self) -> Tuple[Any, Any]:
        """
        Get the VLM model and processor, loading them if necessary.
        
        Returns:
            Tuple of (model, processor)
        """
        if self._vlm_model is None or self._vlm_processor is None:
            loader = self._get_loader()
            if loader.validate_environment():
                self._vlm_model, self._vlm_processor = loader.load_vlm_model()
                logger.info("VLM model and processor loaded successfully")
            else:
                logger.error("Environment validation failed for VLM model loading")
                raise RuntimeError("Environment not ready for model loading")
        
        return self._vlm_model, self._vlm_processor
    
    def model_on_device(self) -> bool:
        """
        Check if models are loaded and available.
        
        Returns:
            True if models are loaded, False otherwise
        """
        return (self._rag_model is not None and 
                self._vlm_model is not None and 
                self._vlm_processor is not None)
    
    def clear_models(self):
        """Clear loaded models to free memory."""
        self._rag_model = None
        self._vlm_model = None
        self._vlm_processor = None
        logger.info("Models cleared from memory")
    
    def unload_vlm(self):
        """Unload VLM model to free memory (for backward compatibility)."""
        self._vlm_model = None
        self._vlm_processor = None
        logger.info("VLM model unloaded from memory")
        return True

# Global instance for backward compatibility
_model_manager_instance: Optional[ModelManager] = None

def get_model_manager() -> ModelManager:
    """Get the global model manager instance."""
    global _model_manager_instance
    if _model_manager_instance is None:
        _model_manager_instance = ModelManager()
    return _model_manager_instance

# Convenience functions for backward compatibility
def model_on_device() -> bool:
    """Check if models are loaded and available."""
    return get_model_manager().model_on_device()