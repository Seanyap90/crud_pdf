import os
import torch
import gc
import logging
import time
import psutil
from pathlib import Path
from byaldi import RAGMultiModalModel
from transformers import (
    Idefics3ForConditionalGeneration, 
    AutoProcessor, 
    BitsAndBytesConfig,
    GenerationConfig
)
from contextlib import contextmanager
import threading

logger = logging.getLogger(__name__)

def log_memory_usage(message="Current memory usage"):
    """Log current CPU and GPU memory usage"""
    # Log CPU memory
    process = psutil.Process(os.getpid())
    cpu_mem = process.memory_info().rss / (1024 * 1024)  # Convert to MB
    
    # Log GPU memory if available
    gpu_mem = "N/A"
    if torch.cuda.is_available():
        gpu_mem = torch.cuda.memory_allocated() / (1024 * 1024)  # Convert to MB
        gpu_mem_reserved = torch.cuda.memory_reserved() / (1024 * 1024)  # Convert to MB
        
        logger.info(f"{message}: CPU: {cpu_mem:.2f}MB, GPU allocated: {gpu_mem:.2f}MB, GPU reserved: {gpu_mem_reserved:.2f}MB")
    else:
        logger.info(f"{message}: CPU: {cpu_mem:.2f}MB, GPU: {gpu_mem}")

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
    _lock = threading.RLock()  # Using RLock for thread safety
    _init_lock = threading.RLock()  # Separate lock for initialization
    
    # Track initialization state
    _is_rag_initialized = False
    _is_vlm_initialized = False
    
    # Environment variable to control model loading behavior
    _disable_duplicate_loading = os.environ.get('DISABLE_DUPLICATE_LOADING', 'true').lower() == 'true'
    
    # Get GPU memory limit from environment or use a default (e.g., 80% of available)
    _gpu_memory_limit = os.environ.get('MODEL_MEMORY_LIMIT', None)
    if _gpu_memory_limit is None and torch.cuda.is_available():
        # Use default limit of ~75% of available GPU memory
        _gpu_memory_limit = f"{int(torch.cuda.get_device_properties(0).total_memory * 0.75 / (1024**3))}GiB"

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
                
                # Log memory limit
                if torch.cuda.is_available():
                    logger.info(f"GPU memory limit for models: {cls._gpu_memory_limit}")
                    logger.info(f"CUDA device count: {torch.cuda.device_count()}")
                    for i in range(torch.cuda.device_count()):
                        prop = torch.cuda.get_device_properties(i)
                        logger.info(f"GPU {i}: {prop.name}, {prop.total_memory / (1024**3):.2f}GB memory")
                else:
                    logger.warning("No GPU detected. Processing will be slower on CPU.")
                
        return cls._instance

    def _get_memory_map(self, for_rag=True):
        """Get appropriate memory map for RAG or VLM models"""
        if not torch.cuda.is_available():
            return "auto"
            
        # Decide on memory allocation strategy
        total_gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
        
        if for_rag:
            # RAG model is smaller, allocate ~30% of GPU memory
            rag_memory = min(total_gpu_memory * 0.3, 8)  # Cap at 8GB for RAG
            # Use integer keys, not string keys
            return {0: f"{rag_memory:.1f}GiB"}  
        else:
            # VLM model is larger, allocate ~60% of GPU memory
            vlm_memory = min(total_gpu_memory * 0.6, 24)  # Cap at 24GB for VLM
            # Use integer keys, not string keys
            return {0: f"{vlm_memory:.1f}GiB"}

    def _lazy_load_rag(self):
        """Lazy load RAG model only when needed with explicit GPU placement"""
        with self._init_lock:
            if self.rag is None:
                try:
                    logger.info("Lazy loading RAG Model...")
                    log_memory_usage("Before RAG model load")
                    
                    # Force cleanup before loading
                    gc.collect()
                    torch.cuda.empty_cache()
                    
                    # byaldi's RAGMultiModalModel doesn't support device_map and other parameters
                    # Load it simply and then move to GPU if available
                    start_time = time.time()
                    self.rag = RAGMultiModalModel.from_pretrained("vidore/colpali")
                    
                    # After loading, check if we need to move to GPU
                    if torch.cuda.is_available():
                        logger.info("Moving RAG model to GPU after loading")
                        # Check if model has a move_to method or needs device parameter
                        if hasattr(self.rag, 'to'):
                            self.rag.to('cuda')
                        elif hasattr(self.rag, 'model') and hasattr(self.rag.model, 'to'):
                            self.rag.model.to('cuda')
                    
                    load_time = time.time() - start_time
                    logger.info(f"RAG model loaded in {load_time:.2f} seconds")
                    
                    # Check if model components are on correct device
                    if torch.cuda.is_available():
                        if hasattr(self.rag, 'model'):
                            device = next(self.rag.model.parameters()).device
                            logger.info(f"RAG model device: {device}")
                        elif hasattr(self.rag, 'embedder'):
                            device = next(self.rag.embedder.parameters()).device
                            logger.info(f"RAG embedder device: {device}")
                    
                    ModelManager._is_rag_initialized = True
                    log_memory_usage("After RAG model load")
                    logger.info("RAG model initialized successfully")
                except Exception as e:
                    logger.error(f"Error initializing RAG model: {str(e)}", exc_info=True)
            return self.rag
    
    def _lazy_load_vlm(self):
        """Lazy load VLM model only when needed with CPU offload support"""
        with self._init_lock:
            if self.vlm is None or self.processor is None:
                try:
                    logger.info("Lazy loading VLM and processor...")
                    log_memory_usage("Before VLM model load")
                    
                    # Force cleanup before loading
                    gc.collect()
                    torch.cuda.empty_cache()
                    
                    # First load processor which is lighter
                    try:
                        processor = AutoProcessor.from_pretrained(
                            "HuggingFaceTB/SmolVLM-Instruct", 
                            local_files_only=True
                        )
                    except Exception as e:
                        logger.error(f"Error loading processor: {str(e)}")
                        return None, None
                    
                    try:
                        # Try two different approaches:
                        # 1. First try without quantization if GPU memory is sufficient
                        # 2. If that fails, try with CPU offload and 8-bit quantization
                        
                        start_time = time.time()
                        logger.info("Attempting to load VLM without quantization (approach 1)")
                        
                        try:
                            # Try loading without quantization first
                            vlm = Idefics3ForConditionalGeneration.from_pretrained(
                                "HuggingFaceTB/SmolVLM-Instruct",
                                device_map="auto",  # Let model decide placement
                                torch_dtype=torch.float16,  # Use half precision to save memory
                                offload_folder="offload_folder", # Support disk offload if needed
                                local_files_only=True
                            )
                            logger.info("Successfully loaded VLM without quantization")
                        except (ValueError, RuntimeError, MemoryError) as e:
                            # If the first approach fails, try with CPU offload enabled
                            logger.info(f"First loading approach failed: {str(e)}")
                            logger.info("Attempting to load with CPU offload (approach 2)")
                            
                            # Force cleanup before second attempt
                            gc.collect()
                            torch.cuda.empty_cache()
                            
                            # Try with int8 quantization which supports CPU offload better than 4-bit
                            vlm = Idefics3ForConditionalGeneration.from_pretrained(
                                "HuggingFaceTB/SmolVLM-Instruct",
                                device_map="auto",
                                torch_dtype=torch.float16,
                                load_in_8bit=True,  # Use 8-bit instead of 4-bit (better CPU support)
                                offload_folder="offload_folder",
                                local_files_only=True
                            )
                            logger.info("Successfully loaded VLM with 8-bit quantization and CPU offload")
                            
                        load_time = time.time() - start_time
                        logger.info(f"VLM model loaded in {load_time:.2f} seconds")
                        
                        # Log device placement
                        if hasattr(vlm, 'hf_device_map'):
                            device_counts = {}
                            for module, device in vlm.hf_device_map.items():
                                if device not in device_counts:
                                    device_counts[device] = 0
                                device_counts[device] += 1
                            logger.info(f"VLM model device distribution: {device_counts}")
                        
                        # Set models and update state
                        self.vlm = vlm
                        self.processor = processor
                        ModelManager._is_vlm_initialized = True
                        
                        log_memory_usage("After VLM model load")
                        return vlm, processor
                        
                    except Exception as model_error:
                        logger.error(f"Error loading VLM model: {str(model_error)}", exc_info=True)
                        
                        # Last resort - try to load on CPU only
                        logger.info("Attempting CPU-only loading as last resort")
                        try:
                            vlm = Idefics3ForConditionalGeneration.from_pretrained(
                                "HuggingFaceTB/SmolVLM-Instruct",
                                device_map="cpu",
                                local_files_only=True
                            )
                            logger.info("Successfully loaded VLM on CPU only")
                            
                            self.vlm = vlm
                            self.processor = processor
                            ModelManager._is_vlm_initialized = True
                            return vlm, processor
                        except Exception as cpu_error:
                            logger.error(f"CPU loading also failed: {str(cpu_error)}")
                            return None, processor
                        
                except Exception as e:
                    logger.error(f"Error initializing VLM model: {str(e)}", exc_info=True)
                    
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

    def check_model_status(self):
        """Check current model status"""
        return {
            "rag_initialized": ModelManager._is_rag_initialized,
            "vlm_initialized": ModelManager._is_vlm_initialized,
            "ready": ModelManager._is_rag_initialized  # Consider ready if RAG is loaded
        }
    
    def unload_vlm(self):
        """Unload VLM to free memory with thorough cleanup"""
        with self._init_lock:
            if self.vlm is not None:
                logger.info("Unloading VLM to free memory")
                log_memory_usage("Before VLM unload")
                
                # Explicitly delete references 
                del self.vlm
                self.vlm = None
                self.processor = None
                ModelManager._is_vlm_initialized = False
                
                # Force garbage collection in both CPU and GPU
                gc.collect()
                torch.cuda.empty_cache()
                
                log_memory_usage("After VLM unload")
                return True
            return False
    
    def unload_all(self):
        """Unload all models completely"""
        with self._init_lock:
            logger.info("Unloading all models")
            log_memory_usage("Before unloading all models")
            
            # Unload VLM first
            if self.vlm is not None:
                del self.vlm
                self.vlm = None
                self.processor = None
                ModelManager._is_vlm_initialized = False
            
            # Then unload RAG
            if self.rag is not None:
                del self.rag
                self.rag = None
                ModelManager._is_rag_initialized = False
            
            # Force thorough cleanup
            gc.collect()
            torch.cuda.empty_cache()
            
            log_memory_usage("After unloading all models")
            return True
            
    def efficient_unload(self):
        """More efficient unloading for batch processing scenarios"""
        with self._init_lock:
            # Only unload VLM if it's loaded
            if self.vlm is not None:
                logger.info("Efficiently unloading VLM while keeping RAG")
                log_memory_usage("Before efficient unload")
                
                # If using CUDA, move model to CPU first to free GPU memory
                if torch.cuda.is_available():
                    try:
                        # For models with device_map, we need a different approach
                        if hasattr(self.vlm, 'hf_device_map'):
                            # Let's just delete it as device_map models can't easily be moved
                            del self.vlm
                            self.vlm = None
                        else:
                            # For regular models, move to CPU first
                            self.vlm.to('cpu')
                            # Then delete
                            del self.vlm
                            self.vlm = None
                    except Exception as e:
                        logger.warning(f"Error during efficient VLM unloading: {str(e)}")
                        # Fall back to direct deletion
                        del self.vlm
                        self.vlm = None
                else:
                    # If not using CUDA, just delete
                    del self.vlm
                    self.vlm = None
                
                # Clear processor too
                self.processor = None
                ModelManager._is_vlm_initialized = False
                
                # Force garbage collection
                gc.collect()
                torch.cuda.empty_cache()
                
                log_memory_usage("After efficient unload")
                return True
            return False