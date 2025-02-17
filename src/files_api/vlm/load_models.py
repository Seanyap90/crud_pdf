import torch
import logging
from pathlib import Path
from byaldi import RAGMultiModalModel
from transformers import (
    Idefics3ForConditionalGeneration, 
    AutoProcessor, 
    BitsAndBytesConfig
)
from contextlib import contextmanager

logger = logging.getLogger(__name__)

@contextmanager
def model_on_device(model, device='cuda'):
    """Context manager to temporarily move model to specific device"""
    try:
        original_device = next(model.parameters()).device
        if original_device != device:
            logger.info(f"Moving model to {device}...")
            model.to(device)
            if device == 'cuda':
                import gc
                gc.collect()
            else:
                torch.cuda.empty_cache()
        yield model
    finally:
        if original_device != device:
            logger.info(f"Moving model back to {original_device}...")
            model.to(original_device)
            if device == 'cuda':
                torch.cuda.empty_cache()
            else:
                import gc
                gc.collect()

class ModelManager:
    _instance = None
    _is_initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ModelManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not ModelManager._is_initialized:
            logger.info("Initializing ModelManager...")
            self.rag = None
            self.vlm = None
            self.processor = None
            Path("offload_folder").mkdir(exist_ok=True)
            ModelManager._is_initialized = True

    def initialize_models(self):
        """Initialize all models"""
        if self.rag is None:
            self.rag = self._load_rag_model()
        if self.vlm is None or self.processor is None:
            self.vlm, self.processor = self._load_vlm_and_processor()

    def _load_rag_model(self):
        """Load RAG model"""
        logger.info("Loading RAG Model...")
        return RAGMultiModalModel.from_pretrained("vidore/colpali")

    def _load_vlm_and_processor(self):
        """Load VLM and processor with optimized memory settings"""
        logger.info("Loading SmolVLM-Instruct...")
        torch.cuda.empty_cache()
        
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_compute_type=torch.float16
        )
        
        vlm = Idefics3ForConditionalGeneration.from_pretrained(
            "HuggingFaceTB/SmolVLM-Instruct",
            quantization_config=bnb_config,
            device_map="cpu",
            torch_dtype=torch.float16,
            max_memory={0: "6GiB"},
            offload_folder="offload_folder",
            local_files_only=True
        )

        processor = AutoProcessor.from_pretrained(
            "HuggingFaceTB/SmolVLM-Instruct", 
            local_files_only=True
        )
        
        return vlm, processor

    def get_models(self):
        """Get all initialized models"""
        if not all([self.rag, self.vlm, self.processor]):
            self.initialize_models()
        return self.rag, self.vlm, self.processor