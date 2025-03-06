# cli.py
import click
import os
import asyncio
import logging
from files_api.vlm.rag import Worker
from files_api.msg_queue import QueueFactory
from files_api.config import config
from files_api.vlm.load_models import ModelManager

# Configure logging
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """CLI commands for FastAPI and Worker management"""
    pass

@cli.command()
@click.option("--mode", 
              type=click.Choice(["local-mock", "local", "hybrid", "cloud"]), 
              default="local-mock")
@click.option("--preload-models/--no-preload-models", default=False, 
              help="Preload ML models during startup")
def worker(mode, preload_models):
    """Start the worker in specified mode"""
    print(f"Starting worker in {mode} mode...")
    
    # Set queue type in config
    os.environ["QUEUE_TYPE"] = mode
    config.QUEUE_TYPE = mode  # Update config directly
    
    # Set model behavior via environment variable
    os.environ["DISABLE_DUPLICATE_LOADING"] = "true"
    
    # Get queue handler
    queue = QueueFactory.get_queue_handler()
    print(queue)
    
    # Preload models if requested
    if preload_models:
        print("Preloading ML models...")
        model_manager = ModelManager()
        rag = model_manager.get_rag_model()
        if rag:
            print("RAG model loaded successfully")
        else:
            print("Warning: Failed to load RAG model")
    
    # Create worker
    worker = Worker(queue)
    
    try:
        # Run worker
        print("Worker ready to process tasks")
        asyncio.run(worker.listen_for_tasks())
    except KeyboardInterrupt:
        worker.stop()
    finally:
        print("Worker shutdown complete")

if __name__ == "__main__":
    cli()