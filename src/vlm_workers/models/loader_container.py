import os
import torch
import gc
import logging
import traceback
from pathlib import Path
from byaldi import RAGMultiModalModel
from huggingface_hub import snapshot_download
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

class ContainerModelLoader:
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
                logger.info("Creating new ContainerModelLoader instance")
                cls._instance = super(ContainerModelLoader, cls).__new__(cls)
                
                # Initialize containers but don't load models yet
                cls._instance.rag = None
                cls._instance.vlm = None
                cls._instance.processor = None
                
                # Create directories
                cls._instance._create_directories()
                
                # Log environment settings
                cls._instance._log_environment()
                
        return cls._instance
    
    def _create_directories(self):
        """Create necessary directories for model and offloading"""
        # Create offload directory
        offload_folder = os.environ.get('CPU_OFFLOAD_FOLDER', 'offload_folder')
        Path(offload_folder).mkdir(exist_ok=True)
        
        # Get cache directory based on deployment mode
        cache_dir = self._get_cache_directory()
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
            
        logger.info(f"Using directories: offload={offload_folder}, cache={cache_dir}")
    
    def _get_cache_directory(self) -> str:
        """Get cache directory - consistent path for both deployment modes."""
        deployment_mode = os.environ.get('DEPLOYMENT_MODE', 'aws-mock')
        
        # Use consistent /app/cache path for both aws-mock and aws-prod
        cache_dir = os.environ.get('TRANSFORMERS_CACHE', '/app/cache')
        
        if deployment_mode == 'aws-prod':
            # For aws-prod, validate EFS mount is available
            if self._validate_efs_mount(cache_dir):
                logger.info(f"ECS aws-prod: Using EFS cache directory: {cache_dir}")
                return cache_dir
            else:
                logger.warning("EFS mount not available, falling back to default cache")
                return '/app/cache'
        else:
            # For aws-mock, use Docker volume mount (models pre-downloaded by download_models.py)
            logger.info(f"ECS aws-mock: Using Docker volume cache directory: {cache_dir}")
            return cache_dir
    
    def _validate_efs_mount(self, cache_path: str) -> bool:
        """Validate that EFS mount point is accessible and contains expected HuggingFace cache structure."""
        try:
            # Check if path exists and is directory
            if not Path(cache_path).is_dir():
                logger.warning(f"EFS mount validation failed: {cache_path} not accessible")
                return False
            
            # Check for HuggingFace cache structure
            # Look for models--* directories that indicate HF cache
            cache_contents = list(Path(cache_path).glob('models--*'))
            if not cache_contents:
                logger.warning(f"EFS mount validation: No HuggingFace cache structure found in {cache_path}")
                return False
            
            # Check for expected model directories (at least ColPali should exist)
            expected_models = [
                'models--vidore--colpali',
                'models--vidore--colpaligemma-3b-mix-448-base',
                'models--HuggingFaceTB--SmolVLM-Instruct'
            ]
            
            found_models = [d.name for d in cache_contents]
            missing_critical = [m for m in expected_models[:2] if m not in found_models]  # ColPali models are critical
            
            if missing_critical:
                logger.warning(f"EFS mount validation: Missing critical model directories: {missing_critical}")
                return False
            
            logger.info(f"EFS mount validation successful: found {len(found_models)} model directories")
            return True
            
        except Exception as e:
            logger.warning(f"EFS mount validation error: {e}")
            return False
    
    def _log_environment(self):
        """Log environment configuration for debugging."""
        deployment_mode = os.environ.get('DEPLOYMENT_MODE', 'aws-mock')
        cache_dir = self._get_cache_directory()
        
        logger.info(f"DEPLOYMENT_MODE: {deployment_mode}")
        logger.info(f"TRANSFORMERS_CACHE: {os.environ.get('TRANSFORMERS_CACHE', 'Not set')}")
        logger.info(f"HF_HOME: {os.environ.get('HF_HOME', 'Not set')}")
        logger.info(f"HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE', 'Not set')}")
        logger.info(f"MODEL_MEMORY_LIMIT: {os.environ.get('MODEL_MEMORY_LIMIT', 'Not set')}")
        logger.info(f"CACHE_IMPLEMENTATION: {os.environ.get('CACHE_IMPLEMENTATION', 'Not set')}")
        logger.info(f"Resolved cache directory: {cache_dir}")
        
        # Log storage configuration based on deployment mode
        if deployment_mode == 'aws-prod':
            logger.info("ECS aws-prod storage:")
            logger.info(f"  EFS mount: /app/cache (contains all models)")
        else:
            logger.info("ECS aws-mock storage:")
            logger.info(f"  Docker volume: /app/cache (pre-populated by download_models.py)")
        
        try:
            cache_contents = os.listdir(cache_dir)
            logger.info(f"Cache directory ({cache_dir}) contains: {cache_contents}")
            
            # Look for ColPali model cache
            model_path = os.path.join(cache_dir, "models--vidore--colpali")
            if os.path.exists(model_path):
                logger.info(f"Found vidore/colpali cache at: {model_path}")
                snapshots_path = os.path.join(model_path, "snapshots")
                if os.path.exists(snapshots_path):
                    snapshots = os.listdir(snapshots_path)
                    logger.info(f"Snapshots available: {snapshots}")
            else:
                logger.warning("vidore/colpali cache not found in expected location")
                
        except Exception as e:
            logger.warning(f"Could not inspect cache directory: {str(e)}")
            
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            device_count = torch.cuda.device_count()
            total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            logger.info(f"GPU: {device_name} (Count: {device_count}, Memory: {total_memory:.2f}GB)")
        else:
            logger.warning("No GPU detected! Models will run on CPU which will be very slow.")

    def _lazy_load_rag(self):
        """Lazy load RAG model only when needed"""
        with self._init_lock:
            if self.rag is None:
                try:
                    logger.info("Lazy loading RAG Model...")
                    # Try loading from local files first (works for both EFS and Docker volumes)
                    try:
                        logger.info("Attempting to load RAG model from local files...")
                        self.rag = RAGMultiModalModel.from_pretrained(
                            "vidore/colpali"
                        )
                        ContainerModelLoader._is_rag_initialized = True
                        logger.info("RAG model initialized from local files successfully")
                    except Exception as local_error:
                        logger.warning(f"Could not load RAG model from local files: {str(local_error)}")
                        # Check if we're in offline mode (aws-prod with EFS)
                        if os.environ.get('HF_HUB_OFFLINE', '0') == '1':
                            logger.error("HF_HUB_OFFLINE=1 but local model loading failed")
                            raise local_error
                        logger.info("Falling back to remote loading...")
                        self.rag = self._load_rag_model()
                        ContainerModelLoader._is_rag_initialized = True
                        logger.info("RAG model initialized successfully from remote")
                except Exception as e:
                    logger.error(f"Error initializing RAG model: {str(e)}")
                    logger.error(traceback.format_exc())
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
                    
                    # First try loading from local files
                    try:
                        logger.info("Attempting to load VLM model from local files...")
                        # Create BitsAndBytes config
                        bnb_config = BitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_use_double_quant=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.float16
                        )
                        
                        processor = AutoProcessor.from_pretrained(
                            "HuggingFaceTB/SmolVLM-Instruct", 
                            local_files_only=True
                        )
                        
                        vlm = Idefics3ForConditionalGeneration.from_pretrained(
                            "HuggingFaceTB/SmolVLM-Instruct",
                            quantization_config=bnb_config,
                            device_map="auto",
                            torch_dtype=torch.float16,
                            max_memory={0: os.environ.get('MODEL_MEMORY_LIMIT', '24GiB')},
                            local_files_only=True
                        )
                        
                        # Store in both instance and thread-local storage
                        self.vlm = vlm
                        self.processor = processor
                        self._thread_local.vlm = vlm
                        self._thread_local.processor = processor
                        
                        ContainerModelLoader._is_vlm_initialized = True
                        logger.info("VLM model initialized from local files successfully")
                    except Exception as local_error:
                        logger.warning(f"Could not load VLM model from local files: {str(local_error)}")
                        # Check if we're in offline mode (aws-prod with EFS)
                        if os.environ.get('HF_HUB_OFFLINE', '0') == '1':
                            logger.error("HF_HUB_OFFLINE=1 but local model loading failed")
                            raise local_error
                        logger.info("Falling back to remote loading...")
                        
                        # Fall back to loading from remote
                        vlm, processor = self._load_vlm_and_processor()
                        
                        # Store in both instance and thread-local storage
                        self.vlm = vlm
                        self.processor = processor
                        self._thread_local.vlm = vlm
                        self._thread_local.processor = processor
                        
                        ContainerModelLoader._is_vlm_initialized = True
                        logger.info("VLM model initialized from remote successfully")
                except Exception as e:
                    logger.error(f"Error initializing VLM: {str(e)}")
                    logger.error(traceback.format_exc())
            
            return self.vlm, self.processor
    
    def create_new_rag_model(self):
        """Create a new RAG model instance, loading from local cache."""
        logger.info("Creating new RAG model instance...")
        try:
            # Get the cache directory (EFS or Docker volume)
            cache_dir = self._get_cache_directory()
            logger.info(f"Attempting to load RAG model from cache: {cache_dir}")
            
            # Log cache contents for debugging
            try:
                cache_contents = os.listdir(cache_dir)
                logger.info(f"Cache directory contents: {cache_contents}")
                model_path = os.path.join(cache_dir, "models--vidore--colpali")
                if os.path.exists(model_path):
                    snapshots_path = os.path.join(model_path, "snapshots")
                    if os.path.exists(snapshots_path):
                        snapshots = os.listdir(snapshots_path)
                        logger.info(f"vidore/colpali snapshots: {snapshots}")
                else:
                    logger.warning("vidore/colpali cache not found")
            except Exception as e:
                logger.warning(f"Could not list cache directory: {str(e)}")
            
            # Retrieve local model path
            local_model_path = snapshot_download(
                "vidore/colpali",
                local_files_only=True,
                cache_dir=cache_dir
            )
            logger.info(f"Local model path: {local_model_path}")
            
            # Load model from local path with minimal parameters
            rag = RAGMultiModalModel.from_pretrained(local_model_path)
            logger.info("RAG model loaded successfully from cache")
            return rag
        except Exception as e:
            logger.error(f"Error loading RAG model: {str(e)}")
            logger.error(traceback.format_exc())
            raise

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

    def _load_rag_model(self):
        """Load RAG model with proper error handling and environment settings"""
        logger.info("Loading RAG model from remote...")
        gc.collect()
        torch.cuda.empty_cache()
        
        # Get memory limit from environment
        memory_limit = os.environ.get('MODEL_MEMORY_LIMIT', '24GiB')
        logger.info(f"Using memory limit for RAG model: {memory_limit}")
        
        try:
            # Create config with environment-based settings
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
            )
            
            # Check if offloading is enabled
            if os.environ.get('OFFLOAD_TO_CPU', 'false').lower() == 'true':
                logger.info("CPU offloading enabled for RAG model")
                offload_folder = os.environ.get('CPU_OFFLOAD_FOLDER', 'offload_folder')
                
                self.rag = RAGMultiModalModel.from_pretrained(
                    "vidore/colpali",
                    device_map="auto",
                    torch_dtype=torch.float16,
                    max_memory={0: memory_limit},
                    quantization_config=quantization_config,
                    offload_folder=offload_folder,
                    local_files_only=False
                )
            else:
                self.rag = RAGMultiModalModel.from_pretrained(
                    "vidore/colpali",
                    device_map="auto",
                    torch_dtype=torch.float16,
                    max_memory={0: memory_limit},
                    quantization_config=quantization_config,
                    local_files_only=False
                )
            
            # Log memory usage
            if torch.cuda.is_available():
                memory_allocated = torch.cuda.memory_allocated(0) / (1024**3)  # GB
                memory_reserved = torch.cuda.memory_reserved(0) / (1024**3)    # GB
                logger.info(f"GPU memory after RAG model loading: Allocated: {memory_allocated:.2f}GB, Reserved: {memory_reserved:.2f}GB")
                
            self._is_rag_initialized = True
            logger.info("RAG model initialized successfully")
            return self.rag
            
        except Exception as e:
            logger.error(f"Error loading RAG model: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def _load_vlm_and_processor(self):
        """Load VLM and processor with optimized memory settings"""
        logger.info("Loading SmolVLM-Instruct from remote...")
        torch.cuda.empty_cache()
        gc.collect()
        
        # Get memory limit from environment
        memory_limit = os.environ.get('MODEL_MEMORY_LIMIT', '24GiB')
        logger.info(f"Using memory limit for VLM model: {memory_limit}")
        
        # Get cache implementation from environment
        cache_impl = os.environ.get('CACHE_IMPLEMENTATION', 'standard')
        logger.info(f"Using cache implementation: {cache_impl}")
        
        try:
            # First load processor which is lighter
            processor = AutoProcessor.from_pretrained(
                "HuggingFaceTB/SmolVLM-Instruct", 
                local_files_only=False
            )
            
            # Create BitsAndBytes config for 4-bit quantization
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16
            )
            
            # Check if offloading is enabled
            if os.environ.get('OFFLOAD_TO_CPU', 'false').lower() == 'true':
                logger.info("CPU offloading enabled for VLM model")
                offload_folder = os.environ.get('CPU_OFFLOAD_FOLDER', 'offload_folder')
                
                vlm = Idefics3ForConditionalGeneration.from_pretrained(
                    "HuggingFaceTB/SmolVLM-Instruct",
                    quantization_config=bnb_config,
                    device_map="auto",
                    torch_dtype=torch.float16,
                    max_memory={0: memory_limit},
                    offload_folder=offload_folder,
                    low_cpu_mem_usage=True,
                    local_files_only=False
                )
            else:
                vlm = Idefics3ForConditionalGeneration.from_pretrained(
                    "HuggingFaceTB/SmolVLM-Instruct",
                    quantization_config=bnb_config,
                    device_map="auto",
                    torch_dtype=torch.float16,
                    max_memory={0: memory_limit},
                    local_files_only=False
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
            logger.error(traceback.format_exc())
            raise

    def check_model_status(self):
        """Check current model status"""
        return {
            "rag_initialized": ContainerModelLoader._is_rag_initialized,
            "vlm_initialized": ContainerModelLoader._is_vlm_initialized,
            "ready": ContainerModelLoader._is_rag_initialized  # Consider ready if RAG is loaded
        }
    
    def unload_vlm(self):
        """Unload VLM to free memory"""
        with self._init_lock:
            if self.vlm is not None:
                logger.info("Unloading VLM to free memory")
                self.vlm = None
                self.processor = None
                ContainerModelLoader._is_vlm_initialized = False
                
                # Force garbage collection
                gc.collect()
                torch.cuda.empty_cache()
                return True
            return False