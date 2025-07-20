# cli.py
import click
import os
import asyncio
import logging
from files_api.adapters.queue import QueueFactory
from files_api.config.settings import get_settings

# Configure logging
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """CLI commands for FastAPI and Worker management"""
    pass

# Worker commands moved to src/vlm_workers/cli.py

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
        from deployment.aws.services.lambda_deploy import deploy_files_api_lambda
        
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