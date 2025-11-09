"""
CLI commands for VLM worker management.

Extracted from files_api/cli.py to separate worker concerns from API concerns.
Handles worker startup, model management, and worker-specific operations.
"""

import click
import os
import asyncio
import logging
from src.files_api.settings import get_settings
from files_api.adapters.queue import QueueFactory

# Configure logging
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """CLI commands for VLM Worker management"""
    pass

@cli.command()
@click.option("--mode", 
              type=click.Choice(["local-dev", "deploy-aws-local", "deploy-aws"]), 
              default="local-dev",
              help="Deployment mode")
@click.option("--preload-models/--no-preload-models", default=False, 
              help="Preload ML models during startup")
def worker(mode, preload_models):
    """Start the VLM worker in specified mode"""
    print(f"Starting VLM worker in {mode} mode...")
    
    # Set deployment mode in environment
    os.environ["DEPLOYMENT_MODE"] = mode
    
    # Clear settings cache to pick up new mode
    get_settings.cache_clear()
    
    # Get fresh settings
    settings = get_settings()
    print(f"Configuration loaded:")
    print(f"  Deployment mode: {settings.deployment_mode}")
    print(f"  S3 bucket: {settings.s3_bucket_name}")
    print(f"  SQS queue: {settings.sqs_queue_name}")
    print(f"  AWS endpoint: {settings.aws_endpoint_url}")
    
    # Set model behavior via environment variable
    os.environ["DISABLE_DUPLICATE_LOADING"] = str(settings.disable_duplicate_loading).lower()
    
    # Get queue handler (will use settings internally)
    queue = QueueFactory.get_queue_handler()
    print(f"Queue handler initialized: {type(queue).__name__}")
    
    # Preload models if requested
    if preload_models:
        print("Preloading ML models...")
        from vlm_workers.models.manager import get_model_manager
        
        model_manager = get_model_manager()
        try:
            rag = model_manager.get_rag_model()
            if rag:
                print("RAG model loaded successfully")
            else:
                print("Warning: Failed to load RAG model")
        except Exception as e:
            print(f"Warning: RAG model loading failed: {e}")
        
        try:
            vlm_model, vlm_processor = model_manager.get_vlm_model()
            if vlm_model and vlm_processor:
                print("VLM model and processor loaded successfully")
            else:
                print("Warning: Failed to load VLM model")
        except Exception as e:
            print(f"Warning: VLM model loading failed: {e}")
    
    # Create worker based on deployment mode
    if mode == "deploy-aws":
        from vlm_workers.aws_worker import AWSWorker
        worker_instance = AWSWorker(queue, mode=mode)
        print(f"AWS Worker initialized for {settings.deployment_mode} mode (AMI-based)")
    else:
        from vlm_workers.worker import Worker
        worker_instance = Worker(queue)
        print(f"Standard Worker initialized for {settings.deployment_mode} mode")
    
    try:
        # Run worker
        print("Worker ready to process tasks")
        asyncio.run(worker_instance.listen_for_tasks())
    except KeyboardInterrupt:
        print("Received shutdown signal...")
        worker_instance.stop()
    finally:
        print("Worker shutdown complete")

@cli.command()
def test_models():
    """Test model loading without starting the worker"""
    print("Testing model loading...")
    
    from vlm_workers.models.manager import get_model_manager
    
    model_manager = get_model_manager()
    settings = get_settings()
    
    print(f"Deployment mode: {settings.deployment_mode}")
    
    # Test RAG model
    try:
        print("Loading RAG model...")
        rag = model_manager.get_rag_model()
        print("✅ RAG model loaded successfully")
    except Exception as e:
        print(f"❌ RAG model loading failed: {e}")
    
    # Test VLM model
    try:
        print("Loading VLM model...")
        vlm_model, vlm_processor = model_manager.get_vlm_model()
        print("✅ VLM model and processor loaded successfully")
    except Exception as e:
        print(f"❌ VLM model loading failed: {e}")
    
    # Check if models are on device
    if model_manager.model_on_device():
        print("✅ All models are loaded and ready")
    else:
        print("⚠️ Some models are not loaded")

@cli.command()
def clear_models():
    """Clear loaded models from memory"""
    print("Clearing models from memory...")
    
    from vlm_workers.models.manager import get_model_manager
    
    model_manager = get_model_manager()
    model_manager.clear_models()
    
    # Clear GPU cache if available
    settings = get_settings()
    if settings.deployment_mode == "local-dev":
        try:
            from vlm_workers.models.loader_local import LocalModelLoader
            loader = LocalModelLoader()
            loader.clear_gpu_cache()
            print("✅ GPU cache cleared")
        except Exception as e:
            print(f"Warning: GPU cache clearing failed: {e}")
    
    print("✅ Models cleared from memory")

@cli.command()
def show_worker_config():
    """Show worker-specific configuration"""
    settings = get_settings()
    
    print("VLM Worker Configuration:")
    print(f"  Deployment Mode: {settings.deployment_mode}")
    print(f"  AWS Region: {settings.aws_region}")
    print(f"  AWS Endpoint: {settings.aws_endpoint_url}")
    print(f"  S3 Bucket: {settings.s3_bucket_name}")
    print(f"  SQS Queue Name: {settings.sqs_queue_name}")
    print(f"  SQS Queue URL: {settings.sqs_queue_url}")
    print(f"  Model Memory Limit: {settings.model_memory_limit}")
    print(f"  Disable Duplicate Loading: {settings.disable_duplicate_loading}")
    
    # Show model-specific info
    from vlm_workers.models.manager import get_model_manager
    model_manager = get_model_manager()
    
    print(f"\nModel Status:")
    print(f"  Models loaded: {model_manager.model_on_device()}")
    
    if settings.deployment_mode == "local-dev":
        try:
            from vlm_workers.models.loader_local import LocalModelLoader
            loader = LocalModelLoader()
            print(f"  Device: {loader.device}")
            print(f"  Cache directory: {loader.get_local_cache_path()}")
        except Exception as e:
            print(f"  Local loader info unavailable: {e}")

@cli.command()
def show_scaling_info():
    """Show scaling information for current deployment mode"""
    settings = get_settings()
    
    print(f"🔧 Scaling Information ({settings.deployment_mode} mode)")
    
    if settings.deployment_mode == "deploy-aws-local":
        print("📋 AWS Mock Mode:")
        print("  • Uses Docker Compose for local development")
        print("  • No auto-scaling capability (single GPU constraint)")
        print("  • Use 'docker-compose up' to start services")
        print("  • Use 'docker-compose logs' to view logs")
    
    elif settings.deployment_mode == "deploy-aws":
        print("📋 AWS Production Mode:")
        print("  • Uses AMI-based ECS tasks with EventBridge scaling")
        print("  • Scaling controlled by Lambda functions (external)")
        print("  • Scale-out: Start stopped instances (desired 0→2)")
        print("  • Scale-in: Stop instances but keep desired=2")
        print("  • Check AWS CloudWatch for task metrics")
    
    elif settings.deployment_mode == "local-dev":
        print("📋 Local Development Mode:")
        print("  • Single worker instance for development")
        print("  • No scaling functionality")
        print("  • Models loaded on-demand")
    
    else:
        print(f"❓ Unknown deployment mode: {settings.deployment_mode}")
    
    print(f"\n⚙️  Configuration:")
    print(f"  • S3 Bucket: {settings.s3_bucket_name}")
    print(f"  • SQS Queue: {settings.sqs_queue_name}")
    print(f"  • AWS Region: {settings.aws_region}")

if __name__ == "__main__":
    cli()