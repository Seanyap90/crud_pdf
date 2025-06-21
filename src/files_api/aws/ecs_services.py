"""ECS service definitions for MongoDB and VLM worker services."""
import logging
from typing import Dict, Any, Optional
import json
from botocore.exceptions import ClientError

from files_api.settings import get_settings
from files_api.aws.utils import get_ecs_client, get_logs_client

logger = logging.getLogger(__name__)
settings = get_settings()


class ECSServiceManager:
    """Manager for ECS services and task definitions."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ecs_client = get_ecs_client()
        self.logs_client = get_logs_client()
        self.cluster_name = f"{settings.app_name}-ecs-cluster"
        self.services = {}
        self.task_definitions = {}
        
    def create_mongodb_service(self, vpc_config: Dict[str, Any], 
                              efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create MongoDB service with Fargate."""
        service_name = f"{settings.app_name}-mongodb"
        
        try:
            # Create task definition
            task_def_arn = self._create_mongodb_task_definition(efs_config)
            
            # Create service discovery for MongoDB
            service_discovery = self._create_service_discovery(
                service_name, vpc_config['vpc_id']
            )
            
            # Check for existing service
            existing_service = self._find_existing_service(service_name)
            if existing_service:
                logger.info(f"Using existing MongoDB service: {service_name}")
                self.services['mongodb'] = existing_service
                return existing_service
            
            # Create MongoDB service
            service_response = self.ecs_client.create_service(
                cluster=self.cluster_name,
                serviceName=service_name,
                taskDefinition=task_def_arn,
                desiredCount=1,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': [vpc_config['public_subnet_id']],
                        'securityGroups': [vpc_config['mongodb_security_group_id']],
                        'assignPublicIp': 'ENABLED'
                    }
                },
                serviceRegistries=[
                    {
                        'registryArn': service_discovery['ServiceArn'],
                        'containerName': 'mongodb',
                        'containerPort': 27017
                    }
                ],
                enableExecuteCommand=True,
                tags=[
                    {'key': 'Name', 'value': service_name},
                    {'key': 'Project', 'value': settings.app_name},
                    {'key': 'Purpose', 'value': 'MongoDB-Database'}
                ]
            )
            
            mongodb_service = service_response['service']
            self.services['mongodb'] = mongodb_service
            
            logger.info(f"Created MongoDB service: {service_name}")
            return mongodb_service
            
        except ClientError as e:
            logger.error(f"Failed to create MongoDB service: {e}")
            raise
    
    def create_vlm_worker_service(self, vpc_config: Dict[str, Any], 
                                 efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create VLM worker service with EC2 GPU instances."""
        service_name = f"{settings.app_name}-vlm-workers"
        
        try:
            # Create task definition
            task_def_arn = self._create_vlm_worker_task_definition(efs_config)
            
            # Check for existing service
            existing_service = self._find_existing_service(service_name)
            if existing_service:
                logger.info(f"Using existing VLM worker service: {service_name}")
                self.services['vlm_workers'] = existing_service
                return existing_service
            
            # Create VLM worker service
            service_response = self.ecs_client.create_service(
                cluster=self.cluster_name,
                serviceName=service_name,
                taskDefinition=task_def_arn,
                desiredCount=0,
                capacityProviderStrategy=[
                    {
                        'capacityProvider': f"{self.cluster_name}-gpu-cp",
                        'weight': 1,
                        'base': 0
                    }
                ],
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': [vpc_config['private_subnet_id']],
                        'securityGroups': [vpc_config['vlm_security_group_id']],
                        'assignPublicIp': 'DISABLED'
                    }
                },
                enableExecuteCommand=True,
                tags=[
                    {'key': 'Name', 'value': service_name},
                    {'key': 'Project', 'value': settings.app_name},
                    {'key': 'Purpose', 'value': 'VLM-GPU-Processing'}
                ]
            )
            
            vlm_service = service_response['service']
            self.services['vlm_workers'] = vlm_service
            
            logger.info(f"Created VLM worker service: {service_name}")
            return vlm_service
            
        except ClientError as e:
            logger.error(f"Failed to create VLM worker service: {e}")
            raise
    
    def _create_mongodb_task_definition(self, efs_config: Dict[str, Any]) -> str:
        """Create task definition for MongoDB."""
        family = f"{settings.app_name}-mongodb"
        
        try:
            # Create CloudWatch log group
            log_group = self._create_log_group(f"/ecs/{family}")
            
            # MongoDB task definition
            task_definition = {
                'family': family,
                'networkMode': 'awsvpc',
                'requiresCompatibilities': ['FARGATE'],
                'cpu': '512',
                'memory': '1024',
                'executionRoleArn': self._get_ecs_execution_role_arn(),
                'taskRoleArn': self._get_ecs_task_role_arn(),
                'volumes': [
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
                ],
                'containerDefinitions': [
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
                ],
                'tags': [
                    {'key': 'Name', 'value': family},
                    {'key': 'Project', 'value': settings.app_name}
                ]
            }
            
            # Register task definition
            response = self.ecs_client.register_task_definition(**task_definition)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            
            self.task_definitions['mongodb'] = task_def_arn
            logger.info(f"Created MongoDB task definition: {family}")
            
            return task_def_arn
            
        except ClientError as e:
            logger.error(f"Failed to create MongoDB task definition: {e}")
            raise
    
    def _create_vlm_worker_task_definition(self, efs_config: Dict[str, Any]) -> str:
        """Create task definition for VLM workers."""
        family = f"{settings.app_name}-vlm-worker"
        
        try:
            # Create CloudWatch log group
            log_group = self._create_log_group(f"/ecs/{family}")
            
            # VLM worker task definition  
            task_definition = {
                'family': family,
                'networkMode': 'awsvpc',
                'requiresCompatibilities': ['EC2'],
                'cpu': '3584',
                'memory': '14336',
                'executionRoleArn': self._get_ecs_execution_role_arn(),
                'taskRoleArn': self._get_ecs_task_role_arn(),
                'volumes': [
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
                ],
                'containerDefinitions': [
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
                            {'name': 'DEPLOYMENT_MODE', 'value': 'aws-prod'},
                            {'name': 'AWS_REGION', 'value': self.region},
                            {'name': 'SQS_QUEUE_URL', 'value': settings.sqs_queue_url},
                            {'name': 'S3_BUCKET_NAME', 'value': settings.s3_bucket_name},
                            {'name': 'MONGODB_URI', 'value': f"mongodb://admin:{settings.mongodb_password}@{settings.app_name}-mongodb.{settings.app_name}.local:27017/{settings.mongodb_database}"},
                            {'name': 'MODEL_CACHE_DIR', 'value': '/models'},
                            {'name': 'CUDA_VISIBLE_DEVICES', 'value': '0'},
                            {'name': 'PYTORCH_CUDA_ALLOC_CONF', 'value': 'max_split_size_mb:256'}
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
                        }
                    }
                ],
                'tags': [
                    {'key': 'Name', 'value': family},
                    {'key': 'Project', 'value': settings.app_name}
                ]
            }
            
            # Register task definition
            response = self.ecs_client.register_task_definition(**task_definition)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            
            self.task_definitions['vlm_worker'] = task_def_arn
            logger.info(f"Created VLM worker task definition: {family}")
            
            return task_def_arn
            
        except ClientError as e:
            logger.error(f"Failed to create VLM worker task definition: {e}")
            raise
    
    def _create_service_discovery(self, service_name: str, vpc_id: str) -> Dict[str, Any]:
        """Create Cloud Map service discovery for internal DNS."""
        try:
            from files_api.aws.utils import AWSClientManager
            servicediscovery_client = AWSClientManager().get_client('servicediscovery')
            
            # Create namespace if it doesn't exist
            namespace_name = settings.app_name
            namespace = self._create_or_get_namespace(namespace_name, vpc_id)
            
            # Check for existing service
            existing_service = self._find_existing_discovery_service(
                service_name, namespace['Id']
            )
            if existing_service:
                logger.info(f"Using existing service discovery: {service_name}")
                return existing_service
            
            # Create service discovery service
            service_response = servicediscovery_client.create_service(
                Name=service_name,
                NamespaceId=namespace['Id'],
                DnsConfig={
                    'DnsRecords': [
                        {
                            'Type': 'A',
                            'TTL': 60
                        }
                    ],
                    'RoutingPolicy': 'MULTIVALUE'
                },
                HealthCheckCustomConfig={
                    'FailureThreshold': 1
                },
                Tags=[
                    {'Key': 'Name', 'Value': service_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            logger.info(f"Created service discovery: {service_name}")
            return service_response['Service']
            
        except ClientError as e:
            logger.error(f"Failed to create service discovery: {e}")
            raise
    
    def _create_or_get_namespace(self, namespace_name: str, vpc_id: str) -> Dict[str, Any]:
        """Create or get Cloud Map namespace."""
        try:
            from files_api.aws.utils import AWSClientManager
            servicediscovery_client = AWSClientManager().get_client('servicediscovery')
            
            # List existing namespaces
            response = servicediscovery_client.list_namespaces()
            for namespace in response.get('Namespaces', []):
                if namespace['Name'] == namespace_name:
                    logger.info(f"Using existing namespace: {namespace_name}")
                    return namespace
            
            # Create new namespace
            namespace_response = servicediscovery_client.create_private_dns_namespace(
                Name=namespace_name,
                Vpc=vpc_id,
                Description=f"Service discovery namespace for {settings.app_name}",
                Tags=[
                    {'Key': 'Name', 'Value': namespace_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Wait for namespace to be created
            operation_id = namespace_response['OperationId']
            waiter = servicediscovery_client.get_waiter('operation_success')
            waiter.wait(OperationId=operation_id)
            
            # Get namespace details
            operation_response = servicediscovery_client.get_operation(
                OperationId=operation_id
            )
            namespace_id = operation_response['Operation']['Targets']['NAMESPACE']
            
            namespace_details = servicediscovery_client.get_namespace(Id=namespace_id)
            logger.info(f"Created namespace: {namespace_name}")
            
            return namespace_details['Namespace']
            
        except ClientError as e:
            logger.error(f"Failed to create namespace: {e}")
            raise
    
    def _create_log_group(self, log_group_name: str) -> str:
        """Create CloudWatch log group."""
        try:
            # Check if log group exists
            try:
                self.logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
                logger.info(f"Using existing log group: {log_group_name}")
                return log_group_name
            except ClientError:
                pass
            
            # Create log group
            self.logs_client.create_log_group(
                logGroupName=log_group_name,
                retentionInDays=7,
                tags={
                    'Name': log_group_name,
                    'Project': settings.app_name
                }
            )
            
            logger.info(f"Created log group: {log_group_name}")
            return log_group_name
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                return log_group_name
            logger.error(f"Failed to create log group: {e}")
            raise
    
    def _get_ecs_execution_role_arn(self) -> str:
        """Get or create ECS execution role ARN."""
        role_name = f"{settings.app_name}-ecs-execution-role"
        
        try:
            from files_api.aws.utils import get_iam_client
            iam_client = get_iam_client()
            
            # Check for existing role
            try:
                role_response = iam_client.get_role(RoleName=role_name)
                return role_response['Role']['Arn']
            except ClientError:
                pass
            
            # Create execution role
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            
            role_response = iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Tags=[
                    {'Key': 'Name', 'Value': role_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Attach execution policy
            iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy'
            )
            
            logger.info(f"Created ECS execution role: {role_name}")
            return role_response['Role']['Arn']
            
        except ClientError as e:
            logger.error(f"Failed to create ECS execution role: {e}")
            raise
    
    def _get_ecs_task_role_arn(self) -> str:
        """Get or create ECS task role ARN."""
        role_name = f"{settings.app_name}-ecs-task-role"
        
        try:
            from files_api.aws.utils import get_iam_client
            iam_client = get_iam_client()
            
            # Check for existing role
            try:
                role_response = iam_client.get_role(RoleName=role_name)
                return role_response['Role']['Arn']
            except ClientError:
                pass
            
            # Create task role
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            
            role_response = iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Tags=[
                    {'Key': 'Name', 'Value': role_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Create custom policy for task permissions
            task_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket"
                        ],
                        "Resource": [
                            f"arn:aws:s3:::{settings.s3_bucket_name}",
                            f"arn:aws:s3:::{settings.s3_bucket_name}/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sqs:ReceiveMessage",
                            "sqs:DeleteMessage",
                            "sqs:GetQueueAttributes"
                        ],
                        "Resource": settings.sqs_queue_arn or "*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogStream",
                            "logs:PutLogEvents"
                        ],
                        "Resource": "*"
                    }
                ]
            }
            
            # Create and attach custom policy
            policy_name = f"{role_name}-policy"
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName=policy_name,
                PolicyDocument=json.dumps(task_policy)
            )
            
            logger.info(f"Created ECS task role: {role_name}")
            return role_response['Role']['Arn']
            
        except ClientError as e:
            logger.error(f"Failed to create ECS task role: {e}")
            raise
    
    def get_services_info(self) -> Dict[str, Any]:
        """Get complete services configuration."""
        return {
            'services': self.services,
            'task_definitions': self.task_definitions
        }
    
    def _find_existing_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        """Find existing ECS service."""
        try:
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name,
                services=[service_name]
            )
            services = response.get('services', [])
            for service in services:
                if service['status'] == 'ACTIVE':
                    return service
            return None
        except ClientError:
            return None
    
    def _find_existing_discovery_service(self, service_name: str, 
                                        namespace_id: str) -> Optional[Dict[str, Any]]:
        """Find existing service discovery service."""
        try:
            from files_api.aws.utils import AWSClientManager
            servicediscovery_client = AWSClientManager().get_client('servicediscovery')
            
            response = servicediscovery_client.list_services(
                Filters=[
                    {
                        'Name': 'NAMESPACE_ID',
                        'Values': [namespace_id]
                    }
                ]
            )
            
            for service in response.get('Services', []):
                if service['Name'] == service_name:
                    return service
            return None
            
        except ClientError:
            return None
    
    def cleanup_services_resources(self) -> None:
        """Clean up services resources (for testing/cleanup)."""
        try:
            # Delete services
            for service_name, service_info in self.services.items():
                try:
                    # Scale down to 0 first
                    self.ecs_client.update_service(
                        cluster=self.cluster_name,
                        service=service_info['serviceName'],
                        desiredCount=0
                    )
                    
                    # Delete service
                    self.ecs_client.delete_service(
                        cluster=self.cluster_name,
                        service=service_info['serviceName'],
                        force=True
                    )
                    logger.info(f"Deleted service: {service_info['serviceName']}")
                except ClientError as e:
                    logger.warning(f"Failed to delete service: {e}")
            
            # Deregister task definitions
            for task_family, task_def_arn in self.task_definitions.items():
                try:
                    self.ecs_client.deregister_task_definition(
                        taskDefinition=task_def_arn
                    )
                    logger.info(f"Deregistered task definition: {task_family}")
                except ClientError as e:
                    logger.warning(f"Failed to deregister task definition: {e}")
                    
        except Exception as e:
            logger.error(f"Error during services cleanup: {e}")
            raise