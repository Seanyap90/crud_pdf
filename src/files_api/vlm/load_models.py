import os
import torch
import gc
import logging
from pathlib import Path
from byaldi import RAGMultiModalModel
from transformers import (
    Idefics3ForConditionalGeneration, 
    AutoProcessor, 
    BitsAndBytesConfig
)
from contextlib import contextmanager
import threading

logger = logging.getLogger(__name__)

@contextmanager
def model_on_device(model, device='cuda'):
    """Context manager to temporarily move model to specific device"""
    try:
        # Check if model has device map
        if hasattr(model, 'hf_device_map'):
            logger.info("Model has device map, using as is")
            yield model
            return
            
        original_device = next(model.parameters()).device
        if original_device != device:
            logger.info(f"Moving model to {device}...")
            # Clear memory before moving
            if device == 'cuda':
                torch.cuda.empty_cache()
            
            model.to(device)
            
            # Force garbage collection
            if device == 'cuda':
                gc.collect()
            else:
                torch.cuda.empty_cache()
        yield model
    finally:
        if 'original_device' in locals() and original_device != device:
            logger.info(f"Moving model back to {original_device}...")
            model.to(original_device)
            if device == 'cuda':
                torch.cuda.empty_cache()
            else:
                gc.collect()

class ModelManager:
    _instance = None
    _lock = threading.RLock()
    _init_lock = threading.RLock()
    _thread_local = threading.local() # Separate lock for initialization
    
    # Track initialization state
    _is_rag_initialized = False
    _is_vlm_initialized = False
    
    # Environment variable to control model loading behavior
    _disable_duplicate_loading = os.environ.get('DISABLE_DUPLICATE_LOADING', 'true').lower() == 'true'

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                logger.info("Creating new ModelManager instance")
                cls._instance = super(ModelManager, cls).__new__(cls)
                
                # Initialize containers but don't load models yet
                cls._instance.rag = None
                cls._instance.vlm = None
                cls._instance.processor = None
                
                # Create directory for model offloading
                Path("offload_folder").mkdir(exist_ok=True)
                
        return cls._instance

    def _lazy_load_rag(self):
        """Lazy load RAG model only when needed"""
        with self._init_lock:
            if self.rag is None:
                try:
                    logger.info("Lazy loading RAG Model...")
                    self.rag = self._load_rag_model()
                    ModelManager._is_rag_initialized = True
                    logger.info("RAG model initialized successfully")
                except Exception as e:
                    logger.error(f"Error initializing RAG model: {str(e)}")
            return self.rag
    
    def _lazy_load_vlm(self):
        """Thread-safe lazy loading of VLM model"""
        with self._init_lock:
            # Check thread-local storage first
            if hasattr(self._thread_local, 'vlm') and self._thread_local.vlm is not None:
                return self._thread_local.vlm, self._thread_local.processor
                
            if self.vlm is None or self.processor is None:
                try:
                    logger.info(f"Loading VLM in thread {threading.current_thread().name}")
                    vlm, processor = self._load_vlm_and_processor()
                    
                    # Store in both instance and thread-local storage
                    self.vlm = vlm
                    self.processor = processor
                    self._thread_local.vlm = vlm
                    self._thread_local.processor = processor
                    
                    ModelManager._is_vlm_initialized = True
                except Exception as e:
                    logger.error(f"Error initializing VLM: {str(e)}")
            
            return self.vlm, self.processor

    def get_rag_model(self):
        """Get RAG model, loading if necessary"""
        return self._lazy_load_rag()
    
    def get_vlm_model(self):
        """Get VLM model and processor, loading if necessary"""
        return self._lazy_load_vlm()

    def get_models(self):
        """Get all models, loading if necessary"""
        rag = self._lazy_load_rag()
        vlm, processor = self._lazy_load_vlm()
        return rag, vlm, processor
    
    def reset_index_state(self):
        """Reset the RAG model's index state without reloading the entire model"""
        with self._init_lock:
            if self.rag is not None:
                try:
                    logger.info("Resetting RAG model index state")
                    # Just clear the existing index reference without reloading model
                    if hasattr(self.rag, 'clear_index'):
                        self.rag.clear_index()
                    elif hasattr(self.rag, 'reset_index'):
                        self.rag.reset_index()
                    else:
                        # If no explicit method exists, we can just create a new index
                        logger.info("No explicit index reset method, using existing model")
                    return True
                except Exception as e:
                    logger.error(f"Error resetting RAG model index: {str(e)}")
            return False

    # def _load_rag_model(self):
    #     """Load RAG model"""
    #     logger.info("Loading RAG Model...")
    #     gc.collect()  # Force garbage collection before loading
    #     return RAGMultiModalModel.from_pretrained("vidore/colpali")

    def _load_rag_model(self):
        logger.info("Loading RAG model...")
        gc.collect()
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        self.rag = RAGMultiModalModel.from_pretrained(
            "vidore/colpali",
            device_map="auto",
            torch_dtype=torch.float16,
            max_memory={0: "24GiB"},
            quantization_config=quantization_config,
        )
        self._is_rag_initialized = True
        logger.info("RAG model initialized successfully")
        return self.rag

    def _load_vlm_and_processor(self):
        """Load VLM and processor with optimized memory settings"""
        logger.info("Loading SmolVLM-Instruct...")
        torch.cuda.empty_cache()
        gc.collect()
        
        # Create BitsAndBytes config for 4-bit quantization
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16
        )
        
        # First load processor which is lighter
        try:
            processor = AutoProcessor.from_pretrained(
                "HuggingFaceTB/SmolVLM-Instruct", 
                local_files_only=False
            )
        except Exception as e:
            logger.error(f"Error loading processor: {str(e)}")
            return None, None
            
        # Then load the model with device map for better memory management
        try:
            vlm = Idefics3ForConditionalGeneration.from_pretrained(
                "HuggingFaceTB/SmolVLM-Instruct",
                quantization_config=bnb_config,
                device_map="auto",  # Use automatic device mapping
                torch_dtype=torch.float16,
                max_memory={0: "24GiB"},  # Adjust based on your GPU
                local_files_only=True
            )
            
            # Log device placement for debugging
            if hasattr(vlm, 'hf_device_map'):
                logger.info("Model device map:")
                for key, device in vlm.hf_device_map.items():
                    if key.count('.') <= 1:  # Only log main modules
                        logger.info(f"  {key}: {device}")
                        
            return vlm, processor
        except Exception as e:
            logger.error(f"Error loading VLM model: {str(e)}")
            return None, processor

    def check_model_status(self):
        """Check current model status"""
        return {
            "rag_initialized": ModelManager._is_rag_initialized,
            "vlm_initialized": ModelManager._is_vlm_initialized,
            "ready": ModelManager._is_rag_initialized  # Consider ready if RAG is loaded
        }
    
    def unload_vlm(self):
        """Unload VLM to free memory"""
        with self._init_lock:
            if self.vlm is not None:
                logger.info("Unloading VLM to free memory")
                self.vlm = None
                self.processor = None
                ModelManager._is_vlm_initialized = False
                
                # Force garbage collection
                gc.collect()
                torch.cuda.empty_cache()
                return True
            return False