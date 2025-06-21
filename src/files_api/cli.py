# cli.py
import click
import os
import asyncio
import logging
from files_api.vlm.rag import Worker
from files_api.msg_queue import QueueFactory
from files_api.vlm.load_models import ModelManager
from files_api.settings import get_settings

# Configure logging
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """CLI commands for FastAPI and Worker management"""
    pass

@cli.command()
@click.option("--mode", 
              type=click.Choice(["local-dev", "aws-mock", "aws-prod"]), 
              default="local-dev",
              help="Deployment mode")
@click.option("--preload-models/--no-preload-models", default=False, 
              help="Preload ML models during startup")
def worker(mode, preload_models):
    """Start the worker in specified mode"""
    print(f"Starting worker in {mode} mode...")
    
    # Set deployment mode in environment
    os.environ["DEPLOYMENT_MODE"] = mode
    
    # Clear settings cache to pick up new mode
    from files_api.settings import get_settings
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
        model_manager = ModelManager()
        rag = model_manager.get_rag_model()
        if rag:
            print("RAG model loaded successfully")
        else:
            print("Warning: Failed to load RAG model")
    
    # Create worker
    worker_instance = Worker(queue)
    
    try:
        # Run worker
        print("Worker ready to process tasks")
        asyncio.run(worker_instance.listen_for_tasks())
    except KeyboardInterrupt:
        worker_instance.stop()
    finally:
        print("Worker shutdown complete")

@cli.command()
def show_config():
    """Show current configuration"""
    settings = get_settings()
    
    print("Current Configuration:")
    print(f"  Deployment Mode: {settings.deployment_mode}")
    print(f"  AWS Region: {settings.aws_region}")
    print(f"  AWS Endpoint: {settings.aws_endpoint_url}")
    print(f"  S3 Bucket: {settings.s3_bucket_name}")
    print(f"  SQS Queue Name: {settings.sqs_queue_name}")
    print(f"  SQS Queue URL: {settings.sqs_queue_url}")
    print(f"  Model Memory Limit: {settings.model_memory_limit}")
    print(f"  Disable Duplicate Loading: {settings.disable_duplicate_loading}")

@cli.command()
@click.option("--app", 
              type=click.Choice(["files-api", "iot-backend", "all"]), 
              default="all",
              help="Which Lambda function to deploy")
@click.option("--mode", 
              type=click.Choice(["aws-prod"]), 
              default="aws-prod",
              help="Deployment mode")
def lambda_deploy(app, mode):
    """Deploy Lambda functions to AWS"""
    print(f"Deploying {app} Lambda function(s) in {mode} mode...")
    
    try:
        from files_api.aws.deploy_lambda import deploy_files_api_lambda
        
        if app in ["files-api", "all"]:
            print("Deploying Files API Lambda...")
            result = deploy_files_api_lambda()
            if result:
                print(f"✅ Files API Lambda deployed successfully")
                print(f"Function URL: {result.get('function_url', 'N/A')}")
            else:
                print("❌ Files API Lambda deployment failed")
                return
        
        # Future: IoT Backend Lambda deployment
        if app in ["iot-backend", "all"]:
            print("IoT Backend Lambda deployment not yet implemented")
        
        print("✅ Lambda deployment completed successfully")
        
    except ImportError as e:
        print(f"❌ Error importing deployment module: {e}")
        print("Make sure aws dependencies are installed")
    except Exception as e:
        print(f"❌ Lambda deployment failed: {e}")
        raise

if __name__ == "__main__":
    cli()