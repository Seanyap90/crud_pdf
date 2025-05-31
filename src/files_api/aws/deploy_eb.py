"""AWS Elastic Beanstalk deployment for VLM+RAG worker (non-HTTP worker)."""
import os
import logging
import json
import time
import subprocess
import argparse
import zipfile
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import contextmanager

# Import settings
from files_api.settings import get_settings

# Import AWS utilities
from files_api.aws.utils import (
    AWSClientManager,
    get_s3_client,
    get_sqs_client,
    get_elasticbeanstalk_client,
    get_iam_client,
    get_ecr_client,
    create_s3_bucket,
    create_sqs_queue,
    get_queue_arn
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get settings instance
settings = get_settings()

# Constants from settings (no more hardcoding!)
DEFAULT_REGION = settings.aws_region
S3_BUCKET_NAME = settings.s3_bucket_name
SQS_QUEUE_NAME = settings.sqs_queue_name
EB_APP_NAME = f"{settings.app_name.lower().replace(' ', '-')}-eb"
EB_ENV_NAME = f"{settings.app_name.lower().replace(' ', '-')}-worker-env"
ECR_REPO_NAME = settings.ecr_repo_name

# Solution stack for single-container Docker
SOLUTION_STACK_DOCKER = "64bit Amazon Linux 2023 v4.0.1 running Docker"

def log_operation(description: str):
    """Decorator for timing and logging AWS deployment operations."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger.info(f"Starting: {description}")
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"Completed: {description} in {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Failed: {description} after {duration:.2f}s - {str(e)}")
                raise
        return wrapper
    return decorator

class EBDeploymentStrategy:
    """Base class for EB deployment strategies."""
    
    def __init__(self, mode: str):
        self.mode = mode
        self.settings = get_settings()
    
    def setup_clients(self) -> None:
        """Set up AWS clients."""
        pass
    
    def create_dockerrun_file(self, ecr_uri: str) -> Dict[str, Any]:
        """Create Dockerrun.aws.json content for non-HTTP worker."""
        return {
            "AWSEBDockerrunVersion": "1",
            "Image": {
                "Name": ecr_uri,
                "Update": "true"
            },
            "Volumes": [],
            "Logging": "/var/log/eb-docker"
        }

class MockEBStrategy(EBDeploymentStrategy):
    """Strategy for aws-mock deployment using local containers."""
    
    def __init__(self):
        super().__init__("aws-mock")
        self.endpoint_url = self.settings.aws_endpoint_url or "http://localhost:5000"
    
    def setup_clients(self) -> None:
        """Set up environment for mock AWS clients."""
        # Environment is already set by settings
        logger.info(f"Using mock endpoint: {self.endpoint_url}")

class ProductionEBStrategy(EBDeploymentStrategy):
    """Strategy for production EB deployment."""
    
    def __init__(self):
        super().__init__("aws-prod")
    
    def setup_clients(self) -> None:
        """Standard boto3 will use environment credentials."""
        pass

class ElasticBeanstalkBuilder:
    """Builder for Elastic Beanstalk deployment of VLM+RAG worker."""
    
    def __init__(self, mode: str = "aws-mock", region: str = None):
        self.settings = get_settings()
        self.region = region or self.settings.aws_region
        self.mode = mode
        self.resources = {}
        
        # Update deployment mode in environment
        os.environ["DEPLOYMENT_MODE"] = mode
        
        # Create appropriate strategy
        if mode == "aws-mock":
            self.strategy = MockEBStrategy()
        else:
            self.strategy = ProductionEBStrategy()
        
        # Initialize environment
        self.strategy.setup_clients()
    
    @log_operation("Creating S3 bucket for application storage")
    def build_s3_bucket(self, bucket_name: str = None) -> 'ElasticBeanstalkBuilder':
        """Build S3 bucket for PDF storage."""
        bucket_name = bucket_name or self.settings.s3_bucket_name
        if create_s3_bucket(bucket_name):
            self.resources['s3_bucket_name'] = bucket_name
        return self
    
    @log_operation("Creating SQS queue for worker tasks")
    def build_sqs_queue(self, queue_name: str = None) -> 'ElasticBeanstalkBuilder':
        """Build SQS queue for task processing."""
        queue_name = queue_name or self.settings.sqs_queue_name
        
        # Worker will poll this queue directly
        attributes = {
            'VisibilityTimeout': '900',  # 15 minutes
            'MessageRetentionPeriod': '345600',  # 4 days
            'MaximumMessageSize': '262144'  # 256 KB
        }
        
        # Create the queue
        sqs_client = get_sqs_client()
        try:
            response = sqs_client.create_queue(
                QueueName=queue_name,
                Attributes=attributes
            )
            queue_url = response.get('QueueUrl')
            
            if queue_url:
                logger.info(f"Created SQS queue: {queue_name} ({queue_url})")
                
                # Store the queue URL
                self.resources['sqs_queue_url'] = queue_url
                
                # Update environment for subsequent operations
                os.environ['SQS_QUEUE_URL'] = queue_url
                
                # Get queue ARN
                queue_arn = get_queue_arn(queue_url)
                if queue_arn:
                    self.resources['sqs_queue_arn'] = queue_arn
            else:
                raise Exception("Failed to create SQS queue")
                
        except Exception as e:
            logger.error(f"Error creating SQS queue: {e}")
            raise
        
        return self
    
    @log_operation("Creating ECR repository")
    def build_ecr_repository(self) -> 'ElasticBeanstalkBuilder':
        """Build ECR repository for Docker images."""
        if self.mode == "aws-mock":
            logger.info("Using local Docker image for mock mode")
            self.resources['ecr_repository_uri'] = "rag-worker:latest"
            return self
        
        ecr_client = get_ecr_client()
        
        try:
            # Create repository
            response = ecr_client.create_repository(
                repositoryName=self.settings.ecr_repo_name,
                imageScanningConfiguration={
                    'scanOnPush': True
                }
            )
            
            repository_uri = response['repository']['repositoryUri']
            self.resources['ecr_repository_uri'] = repository_uri
            
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            # Get existing repository URI
            response = ecr_client.describe_repositories(
                repositoryNames=[self.settings.ecr_repo_name]
            )
            
            repository_uri = response['repositories'][0]['repositoryUri']
            self.resources['ecr_repository_uri'] = repository_uri
            logger.info(f"Using existing ECR repository: {repository_uri}")
        
        return self
    
    @log_operation("Building Docker image")
    def build_docker_image(self) -> 'ElasticBeanstalkBuilder':
        """Build Docker image for the worker."""
        if self.mode == "aws-mock":
            logger.info("Skipping Docker image build for mock mode - using local image")
            return self
        
        # In production mode, we would build and push the Docker image
        try:
            # Get ECR login token
            ecr_client = get_ecr_client()
            response = ecr_client.get_authorization_token()
            token = response['authorizationData'][0]['authorizationToken']
            endpoint = response['authorizationData'][0]['proxyEndpoint']
            
            # Decode the token (it's base64 encoded)
            import base64
            username, password = base64.b64decode(token).decode().split(':')
            
            # Login to ECR
            subprocess.run([
                'docker', 'login', '--username', username, '--password-stdin', endpoint
            ], input=password.encode(), check=True)
            
            # Build the image
            ecr_uri = self.resources.get('ecr_repository_uri')
            dockerfile_path = Path(__file__).parent.parent / 'vlm' / 'Dockerfile'
            context_path = Path(__file__).parent.parent.parent.parent  # Go to project root
            
            logger.info(f"Building Docker image with context: {context_path}")
            subprocess.run([
                'docker', 'build',
                '-f', str(dockerfile_path),
                '-t', f"{ecr_uri}:latest",
                str(context_path)
            ], check=True)
            
            # Push the image
            logger.info(f"Pushing Docker image to {ecr_uri}")
            subprocess.run([
                'docker', 'push', f"{ecr_uri}:latest"
            ], check=True)
            
            logger.info("Docker image built and pushed successfully")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Error building/pushing Docker image: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during Docker build: {e}")
            raise
        
        return self
    
    @log_operation("Creating EB application")
    def build_eb_application(self) -> 'ElasticBeanstalkBuilder':
        """Create Elastic Beanstalk application."""
        eb_client = get_elasticbeanstalk_client()
        
        try:
            # Create application
            eb_client.create_application(
                ApplicationName=EB_APP_NAME,
                Description="VLM+RAG Worker Application for PDF Processing"
            )
            
            self.resources['eb_app_name'] = EB_APP_NAME
            logger.info(f"Created EB application: {EB_APP_NAME}")
            
        except eb_client.exceptions.ApplicationNameAlreadyExistsException:
            logger.info(f"EB application {EB_APP_NAME} already exists")
            self.resources['eb_app_name'] = EB_APP_NAME
        
        return self
    
    @log_operation("Creating application version")
    def build_app_version(self, version_label: str = None) -> 'ElasticBeanstalkBuilder':
        """Create application version with Docker configuration."""
        if self.mode == "aws-mock":
            logger.info("Using local Docker image for mock mode")
            self.resources['app_version'] = "mock-v1"
            return self
        
        eb_client = get_elasticbeanstalk_client()
        s3_client = get_s3_client()
        
        # Generate version label
        if not version_label:
            version_label = f"v{int(time.time())}"
        
        # Create deployment package
        deployment_bucket = f"eb-deployments-{self.region}-{int(time.time())}"
        create_s3_bucket(deployment_bucket)
        
        # Create Dockerrun.aws.json
        dockerrun_content = self.strategy.create_dockerrun_file(
            ecr_uri=self.resources.get('ecr_repository_uri')
        )
        
        # Create deployment ZIP
        deployment_zip = f"/tmp/eb-deployment-{version_label}.zip"
        with zipfile.ZipFile(deployment_zip, 'w') as zf:
            zf.writestr('Dockerrun.aws.json', json.dumps(dockerrun_content, indent=2))
            
            # Add .ebextensions for GPU support and environment configuration
            ebextensions_content = f"""
option_settings:
  aws:autoscaling:launchconfiguration:
    InstanceType: g4dn.xlarge
    IamInstanceProfile: aws-elasticbeanstalk-ec2-role
  aws:elasticbeanstalk:application:environment:
    DEPLOYMENT_MODE: {self.mode}
    S3_BUCKET_NAME: {self.resources.get('s3_bucket_name', self.settings.s3_bucket_name)}
    SQS_QUEUE_URL: {self.resources.get('sqs_queue_url', '')}
    SQS_QUEUE_NAME: {self.settings.sqs_queue_name}
    AWS_DEFAULT_REGION: {self.region}
    MODEL_MEMORY_LIMIT: {self.settings.model_memory_limit}
    DISABLE_DUPLICATE_LOADING: {str(self.settings.disable_duplicate_loading).lower()}
    
packages:
  yum:
    nvidia-docker2: []
    
commands:
  01_nvidia_docker:
    command: |
      # Enable nvidia runtime
      echo '{{"default-runtime": "nvidia", "runtimes": {{"nvidia": {{"path": "/usr/bin/nvidia-container-runtime", "runtimeArgs": []}}}}}}' > /etc/docker/daemon.json
      service docker restart
"""
            zf.writestr('.ebextensions/01_gpu.config', ebextensions_content)
        
        # Upload to S3
        deployment_key = f"deployments/{version_label}.zip"
        s3_client.upload_file(deployment_zip, deployment_bucket, deployment_key)
        
        # Create application version
        eb_client.create_application_version(
            ApplicationName=EB_APP_NAME,
            VersionLabel=version_label,
            SourceBundle={
                'S3Bucket': deployment_bucket,
                'S3Key': deployment_key
            }
        )
        
        self.resources['app_version'] = version_label
        self.resources['deployment_bucket'] = deployment_bucket
        
        # Clean up local file
        os.remove(deployment_zip)
        
        return self
    
    @log_operation("Creating worker environment")
    def build_worker_environment(self) -> 'ElasticBeanstalkBuilder':
        """Create Elastic Beanstalk worker environment."""
        eb_client = get_elasticbeanstalk_client()
        
        try:
            # Create environment
            eb_client.create_environment(
                ApplicationName=EB_APP_NAME,
                EnvironmentName=EB_ENV_NAME,
                SolutionStackName=SOLUTION_STACK_DOCKER,
                VersionLabel=self.resources.get('app_version'),
                OptionSettings=[
                    {
                        'Namespace': 'aws:elasticbeanstalk:sqsd',
                        'OptionName': 'WorkerQueueURL',
                        'Value': self.resources.get('sqs_queue_url', '')
                    },
                    {
                        'Namespace': 'aws:elasticbeanstalk:sqsd',
                        'OptionName': 'HttpPath',
                        'Value': '/process-task'
                    },
                    {
                        'Namespace': 'aws:autoscaling:launchconfiguration',
                        'OptionName': 'InstanceType',
                        'Value': 'g4dn.xlarge'
                    },
                    {
                        'Namespace': 'aws:autoscaling:asg',
                        'OptionName': 'MinSize',
                        'Value': '1'
                    },
                    {
                        'Namespace': 'aws:autoscaling:asg',
                        'OptionName': 'MaxSize',
                        'Value': '5'
                    }
                ],
                Tier={
                    'Name': 'Worker',
                    'Type': 'SQS/HTTP'
                }
            )
            
            self.resources['eb_env_name'] = EB_ENV_NAME
            logger.info(f"Created EB worker environment: {EB_ENV_NAME}")
            
        except eb_client.exceptions.TooManyApplicationsException:
            logger.error("Too many EB applications. Please clean up some applications.")
            raise
        except Exception as e:
            logger.error(f"Error creating EB environment: {e}")
            raise
        
        return self
    
    def export_environment(self) -> 'ElasticBeanstalkBuilder':
        """Export configuration to environment variables and .env.aws file."""
        # Set environment variables for current process
        env_vars = {
            'DEPLOYMENT_MODE': self.mode,
            'AWS_DEFAULT_REGION': self.region,
            'S3_BUCKET_NAME': self.resources.get('s3_bucket_name', self.settings.s3_bucket_name),
            'SQS_QUEUE_URL': self.resources.get('sqs_queue_url', ''),
            'SQS_QUEUE_NAME': self.settings.sqs_queue_name,
            'EB_APP_NAME': self.resources.get('eb_app_name', EB_APP_NAME),
            'EB_ENV_NAME': self.resources.get('eb_env_name', EB_ENV_NAME),
        }
        
        if self.mode == "aws-mock":
            env_vars.update({
                'AWS_ENDPOINT_URL': self.strategy.endpoint_url,
                'AWS_ACCESS_KEY_ID': self.settings.aws_access_key_id or 'mock',
                'AWS_SECRET_ACCESS_KEY': self.settings.aws_secret_access_key or 'mock',
            })
        
        # Set in current process
        for key, value in env_vars.items():
            if value:
                os.environ[key] = str(value)
        
        # ALSO write .env.aws file for FastAPI to read
        env_file_path = Path('.env.aws')
        with open(env_file_path, 'w') as f:
            f.write("# Generated by deploy_eb.py - AWS mock environment\n")
            for key, value in env_vars.items():
                if value:
                    f.write(f"{key}={value}\n")
        
        logger.info(f"Exported configuration to environment variables and {env_file_path}")
        return self
    
    def build(self) -> 'ElasticBeanstalkBuilder':
        """Build all resources."""
        try:
            self.build_s3_bucket()
            self.build_sqs_queue()
            self.build_ecr_repository()
            self.build_docker_image()
            
            if self.mode == "aws-prod":
                self.build_eb_application()
                self.build_app_version()
                self.build_worker_environment()
            
            # Export to environment instead of creating file
            self.export_environment()
            
            return self
        except Exception as e:
            logger.error(f"Error building EB environment: {e}")
            raise
    
    def get_resources(self) -> Dict[str, Any]:
        """Get the created resources."""
        return self.resources
    
    def cleanup(self) -> None:
        """Clean up all created resources."""
        logger.info("Cleaning up EB resources...")
        
        if self.mode == "aws-prod":
            try:
                eb_client = get_elasticbeanstalk_client()
                
                # Terminate environment
                if 'eb_env_name' in self.resources:
                    try:
                        eb_client.terminate_environment(
                            EnvironmentName=self.resources['eb_env_name']
                        )
                        logger.info(f"Terminating EB environment: {self.resources['eb_env_name']}")
                        
                        # Wait for environment to terminate
                        waiter = eb_client.get_waiter('environment_terminated')
                        waiter.wait(EnvironmentNames=[self.resources['eb_env_name']])
                        
                    except Exception as e:
                        logger.warning(f"Error terminating EB environment: {e}")
                
                # Delete application
                if 'eb_app_name' in self.resources:
                    try:
                        eb_client.delete_application(
                            ApplicationName=self.resources['eb_app_name'],
                            TerminateEnvByForce=True
                        )
                        logger.info(f"Deleted EB application: {self.resources['eb_app_name']}")
                    except Exception as e:
                        logger.warning(f"Error deleting EB application: {e}")
                
            except Exception as e:
                logger.warning(f"Error during EB cleanup: {e}")
        
        # Clean up common resources
        try:
            # Clean up SQS queue
            if 'sqs_queue_url' in self.resources:
                sqs_client = get_sqs_client()
                sqs_client.delete_queue(QueueUrl=self.resources['sqs_queue_url'])
                logger.info("Deleted SQS queue")
        except Exception as e:
            logger.warning(f"Error cleaning up SQS queue: {e}")
        
        try:
            # Clean up S3 bucket
            if 's3_bucket_name' in self.resources:
                s3_client = get_s3_client()
                
                # Empty bucket first
                try:
                    import boto3
                    s3_resource = boto3.resource(
                        's3',
                        region_name=self.region,
                        endpoint_url=self.strategy.endpoint_url if hasattr(self.strategy, 'endpoint_url') else None
                    )
                    bucket = s3_resource.Bucket(self.resources['s3_bucket_name'])
                    bucket.objects.all().delete()
                except Exception:
                    pass
                
                s3_client.delete_bucket(Bucket=self.resources['s3_bucket_name'])
                logger.info("Deleted S3 bucket")
        except Exception as e:
            logger.warning(f"Error cleaning up S3 bucket: {e}")
        
        logger.info("EB cleanup complete")

def main():
    """Command-line interface for EB deployment."""
    parser = argparse.ArgumentParser(description='Deploy EB environment for VLM+RAG worker')
    parser.add_argument('--mode', choices=['aws-mock', 'aws-prod'], default='aws-mock',
                        help='Deployment mode (default: aws-mock)')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Skip cleanup of resources')
    parser.add_argument('--keep-running', action='store_true',
                        help='Keep the environment running until interrupted')
    args = parser.parse_args()
    
    builder = ElasticBeanstalkBuilder(mode=args.mode)
    
    try:
        builder.build()
        resources = builder.get_resources()
        
        logger.info("EB environment deployed successfully")
        logger.info(f"Resources: {json.dumps(resources, indent=2)}")
        
        if args.mode == "aws-mock":
            logger.info("\nMock EB environment ready!")
            logger.info("The worker will run in Docker using docker-compose.eb-mock.yml")
            logger.info("\nEnvironment variables have been set.")
            logger.info("You can now run: docker-compose -f docker-compose.eb-mock.yml up")
            
            if args.keep_running:
                logger.info("\nPress Ctrl+C to stop.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    logger.info("Stopping...")
        else:
            logger.info(f"\nEB Application: {resources.get('eb_app_name')}")
            logger.info(f"EB Environment: {resources.get('eb_env_name')}")
            logger.info(f"SQS Queue: {resources.get('sqs_queue_url')}")
            
            if args.keep_running:
                logger.info("\nEnvironment is running. Press Ctrl+C to terminate.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    logger.info("Terminating environment...")
    finally:
        if not args.no_cleanup:
            builder.cleanup()

if __name__ == "__main__":
    main()