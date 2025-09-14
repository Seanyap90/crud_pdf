"""
ECS Task Definition Builder

Purpose: Python module that creates AWS ECS task definitions for MongoDB, VLM (AI) workers, 
and model downloaders with GPU and EFS support.

Main class: TaskDefinitionBuilder with methods to build task definitions 
(build_*: returns Dict, create_*: registers and returns ARN), 
and manage CloudWatch log groups.

Key dependencies: boto3 for AWS ECS/CloudWatch operations, dataclass for TaskDefinitionConfig, 
and internal imports from deployment.aws.utils and files_api.settings.

Key features: Handles EFS volume mounting for persistent storage, GPU resource allocation for AI workloads, 
Fargate compatibility, and automatic IAM role ARN resolution for ECS execution/task roles.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError

# Import settings
from src.files_api.settings import get_settings

# Import AWS utilities
from deployment.aws.utils.aws_clients import (
    get_ecs_client,
    get_logs_client, 
    get_iam_client
)

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class TaskDefinitionConfig:
    """Configuration for ECS task definition with smart defaults."""
    family: str
    cpu: str = "1024"
    memory: str = "2048"
    requires_compatibilities: List[str] = field(default_factory=lambda: ['FARGATE'])
    network_mode: str = 'awsvpc'
    execution_role_arn: Optional[str] = None
    task_role_arn: Optional[str] = None
    volumes: List[Dict[str, Any]] = field(default_factory=list)
    container_definitions: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[Dict[str, str]] = field(default_factory=list)
    
    def __post_init__(self):
        """Set defaults based on family and other configurations."""
        # Set default tags if none provided
        if not self.tags:
            self.tags = [
                {'key': 'Name', 'value': self.family},
                {'key': 'Project', 'value': settings.app_name}
            ]
        
        # Set family-specific defaults
        if 'mongodb' in self.family.lower():
            if self.cpu == "1024":  # Default wasn't overridden
                self.cpu = "512"
                self.memory = "1024"
            self.tags.append({'key': 'Purpose', 'value': 'MongoDB-Database'})
            
        elif 'vlm-worker' in self.family.lower():
            if self.cpu == "1024":  # Default wasn't overridden
                self.cpu = "3584"  # 3.5 vCPU for g4dn.xlarge
                self.memory = "14336"  # 14GB for g4dn.xlarge
            self.requires_compatibilities = ['EC2']  # GPU requires EC2
            self.tags.append({'key': 'Purpose', 'value': 'VLM-GPU-Processing'})
            
        # Model downloader removed - AMI has pre-loaded models
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to ECS task definition dictionary."""
        task_def = {
            'family': self.family,
            'networkMode': self.network_mode,
            'requiresCompatibilities': self.requires_compatibilities,
            'cpu': self.cpu,
            'memory': self.memory,
            'containerDefinitions': self.container_definitions
        }
        
        # Add optional fields if provided
        if self.execution_role_arn:
            task_def['executionRoleArn'] = self.execution_role_arn
        if self.task_role_arn:
            task_def['taskRoleArn'] = self.task_role_arn
        if self.volumes:
            task_def['volumes'] = self.volumes
        if self.tags:
            task_def['tags'] = self.tags
            
        return task_def


class TaskDefinitionBuilder:
    """Builder for AWS ECS task definitions with GPU and EFS support."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ecs_client = get_ecs_client()
        self.logs_client = get_logs_client()
        self.task_definitions = {}
        
        # Pre-created IAM role ARNs (assume they exist)
        self.execution_role_arn = f"arn:aws:iam::{settings.account_id}:role/{settings.app_name}-ecs-execution-role"
        self.task_role_arn = f"arn:aws:iam::{settings.account_id}:role/{settings.app_name}-ecs-task-role"
        
    
    def add_lambda_function_environment(self, container_def: dict, lambda_function_url: str) -> dict:
        """Add Lambda Function URL to container environment variables."""
        if not container_def.get('environment'):
            container_def['environment'] = []

        # Add or update Lambda Function URL environment variables
        env_vars = {env['name']: env for env in container_def['environment']}
        env_vars['LAMBDA_FUNCTION_URL'] = {'name': 'LAMBDA_FUNCTION_URL', 'value': lambda_function_url}
        env_vars['API_BASE_URL'] = {'name': 'API_BASE_URL', 'value': lambda_function_url}  # For backward compatibility

        container_def['environment'] = list(env_vars.values())
        return container_def

    def build_vlm_worker_task_definition(self, efs_config: Dict[str, Any], lambda_function_url: str = None, database_host: str = None) -> Dict[str, Any]:
        """Build VLM worker task definition with GPU and EFS support. Returns task definition dict.""" 
        family = f"{settings.app_name}-vlm-worker"
        
        try:
            # Create CloudWatch log group
            log_group = self._create_log_group(f"/ecs/{family}")
            
            # Build VLM worker task definition configuration
            config = TaskDefinitionConfig(
                family=family,
                execution_role_arn=self.execution_role_arn,
                task_role_arn=self.task_role_arn,
                volumes=[
                    {
                        'name': 'model-storage',
                        'efsVolumeConfiguration': {
                            'fileSystemId': efs_config['shared_models']['file_system_id'],
                            'transitEncryption': 'ENABLED',
                            'authorizationConfig': {
                                'accessPointId': efs_config['shared_models']['access_point_id']
                            }
                        }
                    }
                ],
                container_definitions=[
                    {
                        'name': 'vlm-worker',
                        'image': f"{settings.ecr_registry}/{settings.ecr_repo_name}:latest",
                        'essential': True,
                        'user': '0:0',  # Run as root
                        'resourceRequirements': [
                            {
                                'type': 'GPU',
                                'value': '1'
                            }
                        ],
                        'environment': [
                            {'name': 'DEPLOYMENT_MODE', 'value': 'aws-prod'},
                            {'name': 'AWS_REGION', 'value': self.region},
                            {'name': 'SQS_QUEUE_URL', 'value': settings.sqs_queue_url},
                            {'name': 'S3_BUCKET_NAME', 'value': settings.s3_bucket_name},
                            {'name': 'DATABASE_HOST', 'value': database_host or 'localhost'},
                            {'name': 'DATABASE_PORT', 'value': '8080'},
                            {'name': 'MODEL_CACHE_DIR', 'value': '/app/cache'},
                            {'name': 'CUDA_VISIBLE_DEVICES', 'value': '0'},
                            {'name': 'PYTORCH_CUDA_ALLOC_CONF', 'value': 'max_split_size_mb:256'},
                            {'name': 'TRANSFORMERS_CACHE', 'value': '/app/cache'},
                            {'name': 'HF_HOME', 'value': '/app/cache'},
                            {'name': 'HF_HUB_OFFLINE', 'value': '1'},  # Use cached models only
                            {'name': 'LAMBDA_FUNCTION_URL', 'value': lambda_function_url or ''}
                        ],
                        'mountPoints': [
                            {
                                'sourceVolume': 'model-storage',
                                'containerPath': '/app/cache',
                                'readOnly': False
                            }
                        ],
                        'logConfiguration': {
                            'logDriver': 'awslogs',
                            'options': {
                                'awslogs-group': log_group,
                                'awslogs-region': self.region,
                                'awslogs-stream-prefix': 'vlm-worker'
                            }
                        },
                        'command': ['python', '-m', 'vlm_workers.cli', 'worker', '--mode', 'aws-prod']
                    }
                ]
            )
            
            # Lambda function URL is already added to environment variables above
            # No additional processing needed since it's included in the environment array
            
            task_def_dict = config.to_dict()
            logger.info(f"Built VLM worker task definition: {family}")
            return task_def_dict
            
        except Exception as e:
            logger.error(f"Failed to build VLM worker task definition: {e}")
            raise
    
    # Model downloader task definition removed - AMI has pre-loaded models
    
    
    def create_vlm_worker_task_definition(self, efs_config: Dict[str, Any], lambda_function_url: str = None, database_host: str = None) -> str:
        """Create and register VLM worker task definition. Returns task definition ARN."""
        task_def_dict = self.build_vlm_worker_task_definition(efs_config, lambda_function_url, database_host)
        return self._register_task_definition(task_def_dict)
    
    # Model downloader create method removed - AMI has pre-loaded models
    
    def _register_task_definition(self, task_def_dict: Dict[str, Any]) -> str:
        """Register task definition with ECS. Returns task definition ARN."""
        try:
            response = self.ecs_client.register_task_definition(**task_def_dict)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            
            # Store for cleanup tracking
            family = task_def_dict['family']
            self.task_definitions[family] = task_def_arn
            
            logger.info(f"Registered task definition: {family}")
            return task_def_arn
            
        except ClientError as e:
            logger.error(f"Failed to register task definition {task_def_dict['family']}: {e}")
            raise
    
    def _create_log_group(self, log_group_name: str) -> str:
        """Create CloudWatch log group for ECS tasks."""
        try:
            # Check if log group exists
            try:
                response = self.logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
                for log_group in response.get('logGroups', []):
                    if log_group['logGroupName'] == log_group_name:
                        logger.info(f"Using existing log group: {log_group_name}")
                        return log_group_name
            except ClientError:
                pass
            
            # Create log group
            self.logs_client.create_log_group(
                logGroupName=log_group_name,
                tags={
                    'Name': log_group_name,
                    'Project': settings.app_name
                }
            )
            
            # Set retention policy (7 days)
            self.logs_client.put_retention_policy(
                logGroupName=log_group_name,
                retentionInDays=7
            )
            
            logger.info(f"Created log group: {log_group_name}")
            return log_group_name
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                return log_group_name
            logger.error(f"Failed to create log group: {e}")
            raise
    
    def cleanup_task_definitions(self) -> None:
        """Clean up (deregister) all task definitions created by this builder."""
        try:
            for family, task_def_arn in self.task_definitions.items():
                try:
                    self.ecs_client.deregister_task_definition(
                        taskDefinition=task_def_arn
                    )
                    logger.info(f"Deregistered task definition: {family}")
                except ClientError as e:
                    logger.warning(f"Failed to deregister task definition {family}: {e}")
                    
            self.task_definitions.clear()
            
        except Exception as e:
            logger.error(f"Error during task definition cleanup: {e}")
            raise
    
    def get_task_definitions(self) -> Dict[str, str]:
        """Get all registered task definition ARNs."""
        return self.task_definitions.copy()


# Convenience functions for direct usage (backwards compatibility)
# Model downloader convenience function removed - AMI has pre-loaded models

def create_vlm_worker_task_definition(efs_config: Dict[str, Any], lambda_function_url: str = None) -> str:
    """Create VLM worker task definition (convenience function). Returns ARN."""
    builder = TaskDefinitionBuilder()
    return builder.create_vlm_worker_task_definition(efs_config, lambda_function_url)