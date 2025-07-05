"""
ECS Task Definition Builder
Creates reusable task definitions for MongoDB and VLM workers with GPU and EFS support.
"""
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from botocore.exceptions import ClientError

from files_api.aws.utils import get_ecs_client, get_logs_client
from files_api.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class TaskDefinitionConfig:
    """Configuration for ECS task definitions."""
    family: str
    cpu: str
    memory: str
    network_mode: str = 'awsvpc'
    requires_compatibilities: List[str] = None
    execution_role_arn: str = None
    task_role_arn: str = None
    volumes: List[Dict[str, Any]] = None
    container_definitions: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.requires_compatibilities is None:
            self.requires_compatibilities = ['FARGATE']
        if self.volumes is None:
            self.volumes = []
        if self.container_definitions is None:
            self.container_definitions = []


class TaskDefinitionBuilder:
    """Builds ECS task definitions with GPU and EFS support."""
    
    def __init__(self):
        self.ecs_client = get_ecs_client()
        self.logs_client = get_logs_client()
        self.region = settings.aws_region
        
    def create_log_group(self, log_group_name: str) -> str:
        """Create CloudWatch log group for task logging."""
        try:
            self.logs_client.create_log_group(
                logGroupName=log_group_name,
                tags={
                    'Environment': settings.deployment_mode,
                    'Application': settings.app_name,
                    'ManagedBy': 'ECS'
                }
            )
            logger.info(f"Created log group: {log_group_name}")
            
        except self.logs_client.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log group already exists: {log_group_name}")
        except Exception as e:
            logger.error(f"Failed to create log group {log_group_name}: {e}")
            raise
        
        return log_group_name
    
    def get_ecs_execution_role_arn(self) -> str:
        """Get ECS task execution role ARN."""
        # This should match the role created in ecs_services.py
        role_name = f"{settings.app_name}-ecs-execution-role"
        return f"arn:aws:iam::{settings.account_id}:role/{role_name}"
    
    def get_ecs_task_role_arn(self) -> str:
        """Get ECS task role ARN."""
        # This should match the role created in ecs_services.py
        role_name = f"{settings.app_name}-ecs-task-role"
        return f"arn:aws:iam::{settings.account_id}:role/{role_name}"
    
    def build_mongodb_task_definition(self, efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build MongoDB task definition with EFS storage."""
        family = f"{settings.app_name}-mongodb"
        log_group = self.create_log_group(f"/ecs/{family}")
        
        # EFS volume for MongoDB data
        volumes = [
            {
                'name': 'mongodb-data',
                'efsVolumeConfiguration': {
                    'fileSystemId': efs_config['mongodb']['file_system_id'],
                    'transitEncryption': 'ENABLED',
                    'authorizationConfig': {
                        'accessPointId': efs_config['mongodb']['access_point_id']
                    }
                }
            }
        ]
        
        # MongoDB container definition
        container_definitions = [
            {
                'name': 'mongodb',
                'image': 'mongo:7.0',
                'essential': True,
                'portMappings': [
                    {
                        'containerPort': 27017,
                        'protocol': 'tcp'
                    }
                ],
                'environment': [
                    {'name': 'MONGO_INITDB_ROOT_USERNAME', 'value': 'admin'},
                    {'name': 'MONGO_INITDB_ROOT_PASSWORD', 'value': settings.mongodb_password},
                    {'name': 'MONGO_INITDB_DATABASE', 'value': settings.mongodb_database}
                ],
                'mountPoints': [
                    {
                        'sourceVolume': 'mongodb-data',
                        'containerPath': '/data/db',
                        'readOnly': False
                    }
                ],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': log_group,
                        'awslogs-region': self.region,
                        'awslogs-stream-prefix': 'mongodb'
                    }
                },
                'healthCheck': {
                    'command': [
                        'CMD-SHELL',
                        'mongosh --eval "db.adminCommand(\'ping\')" --quiet'
                    ],
                    'interval': 30,
                    'timeout': 5,
                    'retries': 3,
                    'startPeriod': 60
                }
            }
        ]
        
        return {
            'family': family,
            'networkMode': 'awsvpc',
            'requiresCompatibilities': ['FARGATE'],
            'cpu': '512',
            'memory': '1024',
            'executionRoleArn': self.get_ecs_execution_role_arn(),
            'taskRoleArn': self.get_ecs_task_role_arn(),
            'volumes': volumes,
            'containerDefinitions': container_definitions
        }
    
    def build_vlm_worker_task_definition(self, efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build VLM worker task definition with GPU and EFS model storage."""
        family = f"{settings.app_name}-vlm-worker"
        log_group = self.create_log_group(f"/ecs/{family}")
        
        # EFS volume for model storage (single mount point for all models)
        volumes = [
            {
                'name': 'model-storage',
                'efsVolumeConfiguration': {
                    'fileSystemId': efs_config['models']['file_system_id'],
                    'transitEncryption': 'ENABLED',
                    'authorizationConfig': {
                        'accessPointId': efs_config['models']['access_point_id']
                    }
                }
            }
        ]
        
        # VLM worker container definition with GPU requirements
        container_definitions = [
            {
                'name': 'vlm-worker',
                'image': f"{settings.ecr_registry}/{settings.ecr_repo_name}:latest",
                'essential': True,
                'resourceRequirements': [
                    {
                        'type': 'GPU',
                        'value': '1'
                    }
                ],
                'environment': [
                    {'name': 'DEPLOYMENT_MODE', 'value': settings.deployment_mode},
                    {'name': 'AWS_DEFAULT_REGION', 'value': settings.aws_region},
                    {'name': 'S3_BUCKET_NAME', 'value': settings.s3_bucket_name},
                    {'name': 'SQS_QUEUE_URL', 'value': settings.sqs_queue_url},
                    {'name': 'MODEL_MEMORY_LIMIT', 'value': settings.regional_config[settings.aws_region]['gpu_memory_limit']},
                    {'name': 'INSTANCE_TYPE', 'value': settings.primary_instance_type},
                    {'name': 'DISABLE_DUPLICATE_LOADING', 'value': str(settings.disable_duplicate_loading).lower()},
                    {'name': 'TRANSFORMERS_CACHE', 'value': '/app/cache'},
                    {'name': 'HF_HOME', 'value': '/app/cache'},
                    {'name': 'HF_HUB_OFFLINE', 'value': '1'},  # Use offline mode with EFS models
                    {'name': 'CUDA_VISIBLE_DEVICES', 'value': '0'},
                    {'name': 'PYTORCH_CUDA_ALLOC_CONF', 'value': 'max_split_size_mb:128,garbage_collection_threshold:0.8'},
                    {'name': 'OMP_NUM_THREADS', 'value': '8'},
                    {'name': 'MKL_NUM_THREADS', 'value': '6'}
                ],
                'mountPoints': [
                    {
                        'sourceVolume': 'model-storage',
                        'containerPath': '/app/cache',
                        'readOnly': True
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
                'healthCheck': {
                    'command': [
                        'CMD-SHELL',
                        'ps aux | grep python | grep -v grep || exit 1'
                    ],
                    'interval': 60,
                    'timeout': 20,
                    'retries': 3,
                    'startPeriod': 300  # 5 minutes for model loading
                },
                'dockerLabels': {
                    'com.nvidia.cuda.version': '11.8',
                    'com.nvidia.volumes.needed': 'nvidia_driver'
                }
            }
        ]
        
        return {
            'family': family,
            'networkMode': 'awsvpc',
            'requiresCompatibilities': ['EC2'],  # GPU requires EC2, not Fargate
            'cpu': '3584',      # 3.5 vCPU (leave 0.5 for system)
            'memory': '14336',  # 14 GB (leave 2GB headroom for 16GB total)
            'executionRoleArn': self.get_ecs_execution_role_arn(),
            'taskRoleArn': self.get_ecs_task_role_arn(),
            'volumes': volumes,
            'containerDefinitions': container_definitions
        }
    
    def register_task_definition(self, task_definition: Dict[str, Any]) -> str:
        """Register task definition with ECS and return ARN."""
        try:
            response = self.ecs_client.register_task_definition(**task_definition)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            
            logger.info(f"Registered task definition: {task_definition['family']}")
            logger.info(f"Task definition ARN: {task_def_arn}")
            
            return task_def_arn
            
        except ClientError as e:
            logger.error(f"Failed to register task definition {task_definition['family']}: {e}")
            raise
    
    def create_mongodb_task_definition(self, efs_config: Dict[str, Any]) -> str:
        """Create and register MongoDB task definition."""
        task_definition = self.build_mongodb_task_definition(efs_config)
        return self.register_task_definition(task_definition)
    
    def create_vlm_worker_task_definition(self, efs_config: Dict[str, Any]) -> str:
        """Create and register VLM worker task definition."""
        task_definition = self.build_vlm_worker_task_definition(efs_config)
        return self.register_task_definition(task_definition)
    
    def deregister_task_definition(self, task_definition_arn: str) -> bool:
        """Deregister a task definition."""
        try:
            self.ecs_client.deregister_task_definition(
                taskDefinition=task_definition_arn
            )
            logger.info(f"Deregistered task definition: {task_definition_arn}")
            return True
            
        except ClientError as e:
            logger.error(f"Failed to deregister task definition {task_definition_arn}: {e}")
            return False
    
    def list_task_definitions(self, family_prefix: Optional[str] = None) -> List[str]:
        """List task definition ARNs, optionally filtered by family prefix."""
        try:
            kwargs = {'status': 'ACTIVE'}
            if family_prefix:
                kwargs['familyPrefix'] = family_prefix
            
            response = self.ecs_client.list_task_definitions(**kwargs)
            return response.get('taskDefinitionArns', [])
            
        except ClientError as e:
            logger.error(f"Failed to list task definitions: {e}")
            return []


def create_task_definition_builder() -> TaskDefinitionBuilder:
    """Factory function to create a TaskDefinitionBuilder."""
    return TaskDefinitionBuilder()


# Convenience functions for common operations
def create_mongodb_task_definition(efs_config: Dict[str, Any]) -> str:
    """Create MongoDB task definition."""
    builder = create_task_definition_builder()
    return builder.create_mongodb_task_definition(efs_config)


def create_vlm_worker_task_definition(efs_config: Dict[str, Any]) -> str:
    """Create VLM worker task definition."""
    builder = create_task_definition_builder()
    return builder.create_vlm_worker_task_definition(efs_config)


def cleanup_task_definitions(family_prefix: str) -> int:
    """Clean up all task definitions with given family prefix."""
    builder = create_task_definition_builder()
    task_defs = builder.list_task_definitions(family_prefix)
    
    cleaned_count = 0
    for task_def_arn in task_defs:
        if builder.deregister_task_definition(task_def_arn):
            cleaned_count += 1
    
    logger.info(f"Cleaned up {cleaned_count} task definitions with prefix: {family_prefix}")
    return cleaned_count


if __name__ == "__main__":
    # Example usage for testing
    import json
    
    builder = create_task_definition_builder()
    
    # Mock EFS config for testing
    mock_efs_config = {
        'mongodb': {
            'file_system_id': 'fs-12345678',
            'access_point_id': 'fsap-12345678'
        },
        'models': {
            'file_system_id': 'fs-87654321',
            'colpali_access_point_id': 'fsap-87654321',
            'smolvlm_access_point_id': 'fsap-87654322'
        }
    }
    
    # Build task definitions (don't register in test mode)
    mongodb_task_def = builder.build_mongodb_task_definition(mock_efs_config)
    vlm_task_def = builder.build_vlm_worker_task_definition(mock_efs_config)
    
    print("MongoDB Task Definition:")
    print(json.dumps(mongodb_task_def, indent=2))
    print("\nVLM Worker Task Definition:")
    print(json.dumps(vlm_task_def, indent=2))