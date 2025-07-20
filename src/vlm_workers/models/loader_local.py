"""
Local model loader for local-dev deployment mode.

Handles direct GPU access from local HuggingFace cache for development environments.
This loader is optimized for local development with direct hardware access.
"""

import os
import torch
import logging
from pathlib import Path
from typing import Tuple, Any, Optional
from vlm_workers.models.manager import ModelLoaderInterface

logger = logging.getLogger(__name__)

class LocalModelLoader(ModelLoaderInterface):
    """
    Model loader for local development environment.
    
    Features:
    - Direct GPU access
    - Local HuggingFace cache usage
    - GPU memory validation
    - Fast loading for development iteration
    """
    
    def __init__(self):
        self.device = self._get_device()
        self.cache_dir = self._get_local_cache_path()
        
    def _get_device(self) -> str:
        """Determine the best available device for model loading."""
        if torch.cuda.is_available():
            device = "cuda"
            logger.info(f"CUDA available: {torch.cuda.get_device_name()}")
        elif torch.backends.mps.is_available():
            device = "mps"
            logger.info("MPS (Apple Silicon) available")
        else:
            device = "cpu"
            logger.warning("No GPU available, using CPU")
        
        return device
    
    def _get_local_cache_path(self) -> str:
        """Get the local HuggingFace cache directory."""
        # Use HuggingFace default cache or custom path
        cache_dir = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
        return cache_dir
    
    def validate_environment(self) -> bool:
        """
        Validate that the local environment is ready for model loading.
        
        Returns:
            True if environment is ready, False otherwise
        """
        try:
            # Check GPU memory if using CUDA
            if self.device == "cuda":
                if not self.validate_gpu_memory():
                    return False
            
            # Check cache directory accessibility
            cache_path = Path(self.cache_dir)
            if not cache_path.exists():
                logger.info(f"Creating cache directory: {cache_path}")
                cache_path.mkdir(parents=True, exist_ok=True)
            
            # Check write permissions
            if not os.access(cache_path, os.W_OK):
                logger.error(f"No write access to cache directory: {cache_path}")
                return False
            
            logger.info(f"Local environment validated successfully")
            logger.info(f"Device: {self.device}")
            logger.info(f"Cache directory: {self.cache_dir}")
            
            return True
            
        except Exception as e:
            logger.error(f"Environment validation failed: {e}")
            return False
    
    def validate_gpu_memory(self) -> bool:
        """
        Validate GPU memory availability for model loading.
        
        Returns:
            True if sufficient GPU memory is available
        """
        if self.device != "cuda":
            return True
        
        try:
            # Get GPU memory info
            total_memory = torch.cuda.get_device_properties(0).total_memory
            allocated_memory = torch.cuda.memory_allocated(0)
            free_memory = total_memory - allocated_memory
            
            # Convert to GB for readability
            total_gb = total_memory / (1024**3)
            free_gb = free_memory / (1024**3)
            
            logger.info(f"GPU Memory - Total: {total_gb:.1f}GB, Free: {free_gb:.1f}GB")
            
            # Require at least 4GB free for model loading
            min_required_gb = 4.0
            if free_gb < min_required_gb:
                logger.error(f"Insufficient GPU memory. Required: {min_required_gb}GB, Available: {free_gb:.1f}GB")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"GPU memory validation failed: {e}")
            return False
    
    def load_rag_model(self) -> Any:
        """
        Load the RAG model for local development.
        
        Returns:
            Loaded RAG model instance
        """
        try:
            logger.info("Loading RAG model for local development...")
            
            # Import here to avoid circular imports
            from byaldi import RAGMultiModalModel
            
            # Load RAG model with local cache
            rag_model = RAGMultiModalModel.from_pretrained(
                "vidore/colpali-v1.2",
                cache_dir=self.cache_dir,
                device=self.device
            )
            
            logger.info("RAG model loaded successfully")
            return rag_model
            
        except Exception as e:
            logger.error(f"Failed to load RAG model: {e}")
            raise RuntimeError(f"RAG model loading failed: {e}")
    
    def load_vlm_model(self) -> Tuple[Any, Any]:
        """
        Load the VLM model and processor for local development.
        
        Returns:
            Tuple of (model, processor)
        """
        try:
            logger.info("Loading VLM model for local development...")
            
            # Import here to avoid circular imports
            from transformers import AutoModelForVision2Seq, AutoProcessor
            
            model_id = "HuggingFaceTB/SmolVLM-Instruct"
            
            # Load model and processor with local cache
            model = AutoModelForVision2Seq.from_pretrained(
                model_id,
                cache_dir=self.cache_dir,
                torch_dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
                device_map=self.device if self.device != "cpu" else None
            )
            
            processor = AutoProcessor.from_pretrained(
                model_id,
                cache_dir=self.cache_dir
            )
            
            # Move to device if not already there
            if self.device != "cpu" and not hasattr(model, 'device_map'):
                model = model.to(self.device)
            
            logger.info(f"VLM model loaded successfully on {self.device}")
            return model, processor
            
        except Exception as e:
            logger.error(f"Failed to load VLM model: {e}")
            raise RuntimeError(f"VLM model loading failed: {e}")
    
    def get_local_cache_path(self) -> str:
        """Get the local cache directory path."""
        return self.cache_dir
    
    def clear_gpu_cache(self):
        """Clear GPU cache to free memory."""
        if self.device == "cuda":
            torch.cuda.empty_cache()
            logger.info("GPU cache cleared")