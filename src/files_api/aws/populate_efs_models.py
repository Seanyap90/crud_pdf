"""
EFS Model Population Task
One-time ECS task to download HuggingFace models to EFS for persistent storage.
"""
import logging
import json
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass

from files_api.aws.utils import get_ecs_client, get_logs_client
from files_api.aws.ecs_task_definitions import TaskDefinitionBuilder
from files_api.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ModelPopulationConfig:
    """Configuration for model population task."""
    cluster_name: str
    subnet_id: str
    security_group_id: str
    efs_models_config: Dict[str, Any]
    models_to_download: Dict[str, str]
    timeout_minutes: int = 60


class EFSModelPopulator:
    """Manages one-time ECS task for populating EFS with HuggingFace models."""
    
    def __init__(self):
        self.ecs_client = get_ecs_client()
        self.logs_client = get_logs_client()
        self.task_def_builder = TaskDefinitionBuilder()
        
    def create_model_downloader_task_definition(self, efs_config: Dict[str, Any]) -> str:
        """Create task definition for model downloader."""
        family = f"{settings.app_name}-model-downloader"
        log_group = self.task_def_builder.create_log_group(f"/ecs/{family}")
        
        # EFS volume for model storage
        volumes = [
            {
                'name': 'models-storage',
                'efsVolumeConfiguration': {
                    'fileSystemId': efs_config['models']['file_system_id'],
                    'transitEncryption': 'ENABLED',
                    'authorizationConfig': {
                        'accessPointId': efs_config['models']['access_point_id']
                    }
                }
            }
        ]
        
        # Model downloader container definition
        container_definitions = [
            {
                'name': 'model-downloader',
                'image': f"{settings.ecr_repo_name}:latest",
                'essential': True,
                'cpu': 2048,  # 2 vCPU
                'memory': 4096,  # 4GB RAM for model downloads
                'entryPoint': ['python', '-m', 'files_api.vlm.download_models'],
                'command': ['--mode', 'efs-downloader', '--timeout', '3600'],
                'environment': [
                    {'name': 'DEPLOYMENT_MODE', 'value': 'aws-prod'},
                    {'name': 'MODEL_CACHE_DIR', 'value': '/efs/models'},
                    {'name': 'HF_HUB_CACHE', 'value': '/efs/models/hub'},
                    {'name': 'TRANSFORMERS_CACHE', 'value': '/efs/models/transformers'},
                ],
                'mountPoints': [
                    {
                        'sourceVolume': 'models-storage',
                        'containerPath': '/efs/models',
                        'readOnly': False
                    }
                ],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': log_group,
                        'awslogs-region': settings.aws_region,
                        'awslogs-stream-prefix': 'model-downloader'
                    }
                },
                'stopTimeout': 30
            }
        ]
        
        # Task definition configuration
        task_def = {
            'family': family,
            'networkMode': 'awsvpc',
            'requiresCompatibilities': ['FARGATE'],
            'cpu': '2048',
            'memory': '4096',
            'executionRoleArn': self.task_def_builder.get_ecs_execution_role_arn(),
            'taskRoleArn': self.task_def_builder.get_ecs_task_role_arn(),
            'volumes': volumes,
            'containerDefinitions': container_definitions,
            'tags': [
                {'key': 'Environment', 'value': settings.deployment_mode},
                {'key': 'Application', 'value': settings.app_name},
                {'key': 'Purpose', 'value': 'ModelDownloader'}
            ]
        }
        
        try:
            response = self.ecs_client.register_task_definition(**task_def)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            logger.info(f"Registered model downloader task definition: {task_def_arn}")
            return task_def_arn
            
        except Exception as e:
            logger.error(f"Failed to register model downloader task definition: {e}")
            raise
    
    def run_model_population_task(self, config: ModelPopulationConfig) -> Dict[str, Any]:
        """Run one-time ECS task to populate EFS with models."""
        logger.info("Starting EFS model population task")
        
        # Create task definition
        task_def_arn = self.create_model_downloader_task_definition(config.efs_models_config)
        
        # Run the task
        try:
            response = self.ecs_client.run_task(
                cluster=config.cluster_name,
                taskDefinition=task_def_arn,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': [config.subnet_id],
                        'securityGroups': [config.security_group_id],
                        'assignPublicIp': 'ENABLED'  # Needed for HuggingFace downloads
                    }
                },
                tags=[
                    {'key': 'Purpose', 'value': 'ModelPopulation'},
                    {'key': 'Environment', 'value': settings.deployment_mode}
                ]
            )
            
            task_arn = response['tasks'][0]['taskArn']
            logger.info(f"Started model population task: {task_arn}")
            
            # Wait for task completion
            return self._wait_for_task_completion(config.cluster_name, task_arn, config.timeout_minutes)
            
        except Exception as e:
            logger.error(f"Failed to run model population task: {e}")
            raise
    
    def _wait_for_task_completion(self, cluster_name: str, task_arn: str, timeout_minutes: int) -> Dict[str, Any]:
        """Wait for task completion and return results."""
        logger.info(f"Waiting for task completion (timeout: {timeout_minutes} minutes)")
        
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        
        while time.time() - start_time < timeout_seconds:
            try:
                response = self.ecs_client.describe_tasks(
                    cluster=cluster_name,
                    tasks=[task_arn]
                )
                
                if not response['tasks']:
                    logger.error("Task not found")
                    break
                
                task = response['tasks'][0]
                last_status = task['lastStatus']
                
                logger.info(f"Task status: {last_status}")
                
                if last_status == 'STOPPED':
                    exit_code = task['containers'][0].get('exitCode', -1)
                    
                    if exit_code == 0:
                        logger.info("Model population completed successfully")
                        return {
                            'status': 'success',
                            'task_arn': task_arn,
                            'exit_code': exit_code,
                            'duration_seconds': int(time.time() - start_time)
                        }
                    else:
                        logger.error(f"Model population failed with exit code: {exit_code}")
                        return {
                            'status': 'failed',
                            'task_arn': task_arn,
                            'exit_code': exit_code,
                            'duration_seconds': int(time.time() - start_time)
                        }
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error checking task status: {e}")
                time.sleep(30)
        
        logger.error("Task timed out")
        return {
            'status': 'timeout',
            'task_arn': task_arn,
            'duration_seconds': timeout_seconds
        }
    
    def validate_models_in_efs(self, efs_config: Dict[str, Any]) -> bool:
        """Validate that models are properly downloaded to EFS."""
        # This would need to be implemented with actual EFS validation
        # For now, return True to indicate validation passed
        logger.info("EFS model validation not yet implemented - assuming success")
        return True


def populate_efs_models(cluster_name: str, subnet_id: str, security_group_id: str, 
                       efs_config: Dict[str, Any]) -> Dict[str, Any]:
    """Main function to populate EFS with models."""
    
    config = ModelPopulationConfig(
        cluster_name=cluster_name,
        subnet_id=subnet_id,
        security_group_id=security_group_id,
        efs_models_config=efs_config,
        models_to_download={
            'colpali': 'vidore/colpali-v1.2',
            'smolvlm': 'HuggingFaceTB/SmolVLM-Instruct'
        },
        timeout_minutes=60
    )
    
    populator = EFSModelPopulator()
    return populator.run_model_population_task(config)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Populate EFS with HuggingFace models")
    parser.add_argument("--cluster-name", required=True, help="ECS cluster name")
    parser.add_argument("--subnet-id", required=True, help="Subnet ID for task")
    parser.add_argument("--security-group-id", required=True, help="Security group ID")
    parser.add_argument("--efs-config", required=True, help="EFS configuration JSON file")
    
    args = parser.parse_args()
    
    with open(args.efs_config, 'r') as f:
        efs_config = json.load(f)
    
    result = populate_efs_models(
        args.cluster_name,
        args.subnet_id, 
        args.security_group_id,
        efs_config
    )
    
    print(json.dumps(result, indent=2))