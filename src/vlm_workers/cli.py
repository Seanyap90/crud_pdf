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
              type=click.Choice(["local-dev", "aws-mock", "aws-prod"]), 
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
    
    # Create worker (use standard Worker for all modes)
    from vlm_workers.worker import Worker
    worker_instance = Worker(queue)
    print(f"Worker initialized for {settings.deployment_mode} mode")
    
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
@click.option("--scale", default=1, help="Number of worker replicas to simulate")
def simulate_scaling(scale):
    """Simulate auto-scaling for testing (aws-mock mode)"""
    settings = get_settings()
    
    if settings.deployment_mode not in ["aws-mock", "aws-prod"]:
        print("❌ Scaling simulation only available in aws-mock or aws-prod mode")
        return
    
    print(f"Simulating scaling to {scale} worker replicas...")
    
    try:
        from vlm_workers.scaling.auto_scaler import get_task_manager
        
        task_manager = get_task_manager()
        result = task_manager.simulate_scaling(desired_count=scale)
        
        if result:
            print(f"✅ Scaling simulation completed: {scale} replicas")
            
            # Show scaling history
            history = task_manager.get_scaling_history()
            if history:
                print("\n📊 Recent scaling events:")
                for event in history[-3:]:  # Show last 3 events
                    print(f"  {event['timestamp']}: {event['desired_count']} replicas ({event['service']})")
        else:
            print("❌ Scaling simulation failed")
            
    except Exception as e:
        print(f"❌ Scaling simulation error: {e}")

@cli.command()
def show_scaling_status():
    """Show current scaling status and history"""
    settings = get_settings()
    
    try:
        from vlm_workers.scaling.auto_scaler import get_task_manager
        
        task_manager = get_task_manager()
        
        print(f"🔧 Scaling Status ({settings.deployment_mode} mode)")
        print(f"Cluster: {task_manager.cluster_name}")
        print(f"Service: {task_manager.service_name or 'docker-compose'}")
        
        # Show task statistics
        stats = task_manager.get_task_statistics()
        print(f"\n📈 Task Statistics:")
        print(f"  Active tasks: {stats['active_tasks']}")
        print(f"  Completed tasks: {stats['completed_tasks']}")
        print(f"  Failed tasks: {stats['failed_tasks']}")
        print(f"  Success rate: {stats['success_rate']:.1f}%")
        
        # Show scaling history
        history = task_manager.get_scaling_history()
        if history:
            print(f"\n📊 Scaling History ({len(history)} events):")
            for event in history[-5:]:  # Show last 5 events
                print(f"  {event['timestamp']}: {event['desired_count']} replicas")
        else:
            print("\n📊 No scaling events recorded")
            
    except Exception as e:
        print(f"❌ Error getting scaling status: {e}")

if __name__ == "__main__":
    cli()