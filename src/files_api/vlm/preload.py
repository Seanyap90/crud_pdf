import logging
from files_api.vlm.load_models import ModelManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def preload():
    logger.info("Starting model preloading...")
    model_manager = ModelManager()
    model_manager.initialize_models()
    logger.info("Models preloaded successfully!")

if __name__ == "__main__":
    preload()