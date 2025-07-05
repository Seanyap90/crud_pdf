"""AWS ECS deployment for VLM+RAG worker with MongoDB and GPU support."""
import logging
import json
import time
import subprocess
import argparse
from typing import Dict, Any, List

# Import settings
from files_api.settings import get_settings

# Import AWS utilities
from files_api.aws.utils import (
    get_ecr_client,
    create_s3_bucket,
    create_sqs_queue,
    get_queue_arn
)

# Import ECS infrastructure components
from files_api.aws.vpc_network import VPCNetworkBuilder
from files_api.aws.efs_manager import EFSManager
from files_api.aws.ecs_cluster import ECSClusterManager
from files_api.aws.ecs_services import ECSServiceManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get settings instance
settings = get_settings()

# Constants from settings
DEFAULT_REGION = settings.aws_region
S3_BUCKET_NAME = settings.s3_bucket_name
SQS_QUEUE_NAME = settings.sqs_queue_name
ECS_CLUSTER_NAME = f"{settings.app_name.lower().replace(' ', '-')}-ecs-cluster"
ECR_REPO_NAME = settings.ecr_repo_name

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


class ECSDeploymentStrategy:
    """Base class for ECS deployment strategies."""
    
    def __init__(self, mode: str):
        self.mode = mode
        self.settings = get_settings()
        self.deployment_config = {}
    
    def setup_clients(self) -> None:
        """Set up AWS clients."""
        pass
    
    def create_container_config(self, ecr_uri: str) -> Dict[str, Any]:
        """Create container configuration for ECS tasks."""
        return {
            "image": ecr_uri,
            "essential": True,
            "environment": self._get_container_environment(),
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-region": self.settings.aws_region,
                    "awslogs-stream-prefix": "ecs"
                }
            }
        }
    
    def _get_container_environment(self) -> List[Dict[str, str]]:
        """Get environment variables for containers."""
        return [
            {"name": "DEPLOYMENT_MODE", "value": self.mode},
            {"name": "AWS_REGION", "value": self.settings.aws_region},
            {"name": "S3_BUCKET_NAME", "value": self.settings.s3_bucket_name},
            {"name": "SQS_QUEUE_URL", "value": self.settings.sqs_queue_url or ""}
        ]


class MockECSStrategy(ECSDeploymentStrategy):
    """Strategy for aws-mock deployment using existing infrastructure."""
    
    def __init__(self):
        super().__init__("aws-mock")
    
    def setup_clients(self) -> None:
        """Mock deployment uses existing infrastructure - no client setup needed."""
        logger.info("Mock deployment will use existing aws-mock infrastructure")
    
    @log_operation("Mock ECS deployment using existing aws-mock target")
    def deploy(self) -> Dict[str, Any]:
        """Create mock AWS resources for ECS simulation."""
        logger.info("Mock ECS deployment - creating required AWS resources")
        
        try:
            # Create S3 bucket for mock environment
            bucket_created = create_s3_bucket(S3_BUCKET_NAME)
            if bucket_created:
                logger.info(f"Mock S3 bucket created: {S3_BUCKET_NAME}")
            
            # Create SQS queue for mock environment
            queue_url = create_sqs_queue(SQS_QUEUE_NAME)
            if queue_url:
                queue_arn = get_queue_arn(queue_url)
                logger.info(f"Mock SQS queue created: {SQS_QUEUE_NAME} -> {queue_url}")
                
                # Set queue URL in settings for worker access
                settings.sqs_queue_url = queue_url
                settings.sqs_queue_arn = queue_arn
            else:
                raise Exception(f"Failed to create SQS queue: {SQS_QUEUE_NAME}")
            
            return {
                "status": "success",
                "mode": "mock",
                "message": "ECS mock mode - AWS resources created",
                "database": "sqlite3",
                "infrastructure": "docker-compose via make aws-mock",
                "s3_bucket": S3_BUCKET_NAME,
                "sqs_queue_url": queue_url,
                "sqs_queue_arn": queue_arn
            }
            
        except Exception as e:
            logger.error(f"Mock deployment failed: {e}")
            return {
                "status": "failed",
                "mode": "mock",
                "error": str(e)
            }


class ProductionECSStrategy(ECSDeploymentStrategy):
    """Strategy for aws-prod deployment using real AWS ECS."""
    
    def __init__(self):
        super().__init__("aws-prod")
        self.vpc_builder = None
        self.efs_manager = None
        self.cluster_manager = None
        self.service_manager = None
        self.deployment_config = {}
    
    def validate_gpu_quota(self, region: str) -> bool:
        """Check if we have sufficient GPU quota before deployment."""
        try:
            import boto3
            client = boto3.client('service-quotas', region_name=region)
            response = client.get_service_quota(
                ServiceCode='ec2',
                QuotaCode='L-DB2E81BA'
            )
            
            current_quota = response['Quota']['Value']
            required_quota = 12.0  # 3× g4dn.xlarge = 12 vCPUs
            
            if current_quota >= required_quota:
                logger.info(f"✅ GPU quota sufficient: {current_quota} vCPUs available")
                return True
            else:
                logger.error(f"❌ Insufficient GPU quota: {current_quota} < {required_quota}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to check quota: {e}")
            return False
    
    def setup_clients(self) -> None:
        """Set up AWS clients for production deployment."""
        # Initialize infrastructure managers
        self.vpc_builder = VPCNetworkBuilder(self.settings.aws_region)
        self.efs_manager = EFSManager(self.settings.aws_region)
        self.cluster_manager = ECSClusterManager(self.settings.aws_region)
        self.service_manager = ECSServiceManager(self.settings.aws_region)
        
        logger.info("Initialized ECS infrastructure managers")
    
    @log_operation("Production ECS deployment")
    def deploy(self) -> Dict[str, Any]:
        """Deploy complete ECS infrastructure."""
        try:
            # NEW: Pre-deployment GPU quota validation
            if not self.validate_gpu_quota(self.settings.aws_region):
                raise Exception("Insufficient GPU quota - request increase first")
            
            # Phase 1: Core Infrastructure
            logger.info("Phase 1: Setting up core infrastructure")
            vpc_config = self._setup_vpc_infrastructure()
            efs_config = self._setup_efs_storage(vpc_config)
            
            # Phase 2: ECS Infrastructure
            logger.info("Phase 2: Setting up ECS infrastructure")
            cluster_config = self._setup_ecs_cluster(vpc_config)
            
            # Phase 3: Services
            logger.info("Phase 3: Deploying services")
            services_config = self._deploy_services(vpc_config, efs_config)
            
            # Phase 4: Auto-scaling (placeholder for ecs_scaling.py)
            logger.info("Phase 4: Configuring auto-scaling")
            scaling_config = self._setup_auto_scaling(services_config)
            
            # Compile deployment summary
            self.deployment_config = {
                "status": "success",
                "mode": "production",
                "region": self.settings.aws_region,
                "cluster_name": ECS_CLUSTER_NAME,
                "vpc": vpc_config,
                "efs": efs_config,
                "cluster": cluster_config,
                "services": services_config,
                "scaling": scaling_config,
                "deployment_time": time.time()
            }
            
            logger.info("ECS deployment completed successfully")
            return self.deployment_config
            
        except Exception as e:
            logger.error(f"ECS deployment failed: {e}")
            self.deployment_config["status"] = "failed"
            self.deployment_config["error"] = str(e)
            raise
    
    def deploy_infrastructure_only(self) -> Dict[str, Any]:
        """Deploy only infrastructure components without services."""
        try:
            # Phase 1: Core Infrastructure
            logger.info("Infrastructure-only mode: Setting up core infrastructure")
            vpc_config = self._setup_vpc_infrastructure()
            efs_config = self._setup_efs_storage(vpc_config)
            
            # Phase 2: ECS Infrastructure
            logger.info("Infrastructure-only mode: Setting up ECS infrastructure")
            cluster_config = self._setup_ecs_cluster(vpc_config)
            
            # Skip services deployment
            logger.info("Infrastructure-only mode: Skipping services deployment")
            
            # Compile infrastructure-only summary
            infrastructure_config = {
                "status": "success",
                "mode": "infrastructure-only",
                "region": self.settings.aws_region,
                "cluster_name": ECS_CLUSTER_NAME,
                "vpc": vpc_config,
                "efs": efs_config,
                "cluster": cluster_config,
                "s3_bucket": S3_BUCKET_NAME,
                "sqs_queue": SQS_QUEUE_NAME
            }
            
            logger.info("Infrastructure-only deployment completed successfully")
            return infrastructure_config
            
        except Exception as e:
            logger.error(f"Infrastructure-only deployment failed: {e}")
            return {
                "status": "failed",
                "mode": "infrastructure-only",
                "error": str(e)
            }
    
    @log_operation("VPC and networking setup")
    def _setup_vpc_infrastructure(self) -> Dict[str, Any]:
        """Set up VPC infrastructure."""
        # Build VPC with single AZ design
        vpc_config = (self.vpc_builder
                     .build_vpc("10.0.0.0/16")
                     .build_subnets("us-east-1a")  # Single AZ for cost optimization
                     .build_internet_gateway()
                     .build_nat_gateway()
                     .build_route_tables()
                     .build_security_groups()
                     .get_vpc_config())
        
        logger.info(f"VPC created: {vpc_config['vpc_id']}")
        return vpc_config
    
    @log_operation("EFS storage setup")
    def _setup_efs_storage(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up EFS file systems for MongoDB and models."""
        # Create MongoDB EFS
        mongodb_efs = self.efs_manager.create_mongodb_efs(
            vpc_config['vpc_id'],
            vpc_config['public_subnet_id'],
            vpc_config['efs_security_group_id']
        )
        
        # Create models EFS
        models_efs = self.efs_manager.create_models_efs(
            vpc_config['vpc_id'],
            vpc_config['private_subnet_id'],
            vpc_config['efs_security_group_id']
        )
        
        # Wait for mount targets to be available
        self.efs_manager.wait_for_mount_targets_available()
        
        efs_config = {
            "mongodb": mongodb_efs,
            "models": models_efs
        }
        
        logger.info(f"EFS systems created: MongoDB={mongodb_efs['file_system_id']}, Models={models_efs['file_system_id']}")
        return efs_config
    
    @log_operation("ECS cluster setup")
    def _setup_ecs_cluster(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up ECS cluster with GPU capacity provider."""
        cluster_config = self.cluster_manager.create_cluster(
            vpc_config['vpc_id'],
            [vpc_config['private_subnet_id']]
        )
        
        logger.info(f"ECS cluster created: {cluster_config['clusterName']}")
        return self.cluster_manager.get_cluster_info()
    
    @log_operation("ECS services deployment")
    def _deploy_services(self, vpc_config: Dict[str, Any], efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy MongoDB and VLM worker services."""
        # Create MongoDB service
        mongodb_service = self.service_manager.create_mongodb_service(
            vpc_config, efs_config
        )
        
        # Create VLM worker service
        vlm_service = self.service_manager.create_vlm_worker_service(
            vpc_config, efs_config
        )
        
        services_config = self.service_manager.get_services_info()
        logger.info(f"Services deployed: MongoDB={mongodb_service['serviceName']}, VLM={vlm_service['serviceName']}")
        
        return services_config
    
    @log_operation("Auto-scaling configuration")
    def _setup_auto_scaling(self, services_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up auto-scaling for VLM workers."""
        # Placeholder for ecs_scaling.py integration
        # This will be implemented when ecs_scaling.py is created
        vlm_service_name = services_config.get('services', {}).get('vlm_workers', {}).get('serviceName', 'vlm-workers')
        
        scaling_config = {
            "vlm_workers": {
                "service_name": vlm_service_name,
                "min_capacity": 0,
                "max_capacity": 3,
                "target_metric": "SQS_ApproximateNumberOfMessages",
                "scale_out_cooldown": 300,
                "scale_in_cooldown": 300,
                "status": "configured"
            }
        }
        
        logger.info(f"Auto-scaling configuration prepared for {vlm_service_name} (implementation pending)")
        return scaling_config


class ECSDeploymentBuilder:
    """Builder for ECS deployment with different strategies."""
    
    def __init__(self, mode: str = None, infrastructure_only: bool = False):
        self.mode = mode or settings.deployment_mode
        self.infrastructure_only = infrastructure_only
        self.strategy = self._create_strategy()
        self.ecr_uri = None
        
    def _create_strategy(self) -> ECSDeploymentStrategy:
        """Create deployment strategy based on mode."""
        if self.mode == "aws-mock":
            return MockECSStrategy()
        elif self.mode == "aws-prod":
            return ProductionECSStrategy()
        else:
            raise ValueError(f"Unsupported deployment mode: {self.mode}")
    
    @log_operation("ECR image preparation")
    def prepare_ecr_image(self, docker_image_path: str = None) -> 'ECSDeploymentBuilder':
        """Prepare and push Docker image to ECR."""
        docker_image_path = docker_image_path or "src/files_api/vlm"
        
        if self.mode == "aws-mock":
            logger.info("Skipping ECR for mock mode")
            self.ecr_uri = "mock-image:latest"
            return self
        
        # ECR operations for production
        ecr_client = get_ecr_client()
        
        try:
            # Create ECR repository if it doesn't exist
            try:
                ecr_client.create_repository(repositoryName=ECR_REPO_NAME)
                logger.info(f"Created ECR repository: {ECR_REPO_NAME}")
            except ecr_client.exceptions.RepositoryAlreadyExistsException:
                logger.info(f"ECR repository already exists: {ECR_REPO_NAME}")
            
            # Get ECR login token
            token_response = ecr_client.get_authorization_token()
            token_data = token_response['authorizationData'][0]
            
            # Build ECR URI
            account_id = token_data['proxyEndpoint'].split('.')[0].split('//')[-1]
            self.ecr_uri = f"{account_id}.dkr.ecr.{DEFAULT_REGION}.amazonaws.com/{ECR_REPO_NAME}:latest"
            
            # Docker build and push operations
            self._build_and_push_image(docker_image_path, token_data)
            
            logger.info(f"ECR image ready: {self.ecr_uri}")
            
        except Exception as e:
            logger.error(f"ECR preparation failed: {e}")
            raise
        
        return self
    
    def _build_and_push_image(self, docker_path: str, token_data: Dict[str, Any]) -> None:
        """Build and push Docker image to ECR."""
        import base64
        
        # Decode ECR token
        token = base64.b64decode(token_data['authorizationToken']).decode('utf-8')
        username, password = token.split(':')
        
        # Docker login
        subprocess.run([
            "docker", "login", "--username", username, "--password-stdin",
            token_data['proxyEndpoint']
        ], input=password.encode(), check=True)
        
        # Build image
        subprocess.run([
            "docker", "build", "-t", self.ecr_uri, docker_path
        ], check=True)
        
        # Push image
        subprocess.run([
            "docker", "push", self.ecr_uri
        ], check=True)
        
        logger.info(f"Pushed image to ECR: {self.ecr_uri}")
    
    @log_operation("S3 and SQS setup")
    def setup_supporting_services(self) -> 'ECSDeploymentBuilder':
        """Set up S3 bucket and SQS queue."""
        if self.mode == "aws-mock":
            logger.info("Skipping S3/SQS setup for mock mode")
            return self
        
        # Create S3 bucket
        if create_s3_bucket(S3_BUCKET_NAME):
            logger.info(f"S3 bucket ready: {S3_BUCKET_NAME}")
        
        # Create SQS queue
        queue_url = create_sqs_queue(SQS_QUEUE_NAME)
        if queue_url:
            queue_arn = get_queue_arn(queue_url)
            # Update settings with queue info
            settings.sqs_queue_url = queue_url
            settings.sqs_queue_arn = queue_arn
            logger.info(f"SQS queue ready: {SQS_QUEUE_NAME}")
        
        return self
    
    @log_operation("ECS infrastructure deployment")
    def deploy(self) -> Dict[str, Any]:
        """Execute the deployment strategy."""
        self.strategy.setup_clients()
        
        if self.infrastructure_only and hasattr(self.strategy, 'deploy_infrastructure_only'):
            return self.strategy.deploy_infrastructure_only()
        else:
            return self.strategy.deploy()


def export_infrastructure_config(result: Dict[str, Any], export_file: str) -> None:
    """Export infrastructure configuration to environment file for docker-compose."""
    if result.get("status") != "success":
        logger.error("Cannot export config from failed deployment")
        return
    
    try:
        config_lines = [
            "# AWS Infrastructure Configuration for Docker Compose",
            "# Generated by deploy_ecs.py --infrastructure-only",
            "",
            f"# Deployment Info",
            f"APP_NAME={settings.app_name}",
            f"AWS_DEFAULT_REGION={result['region']}",
            f"DEPLOYMENT_MODE=aws-prod",
            "",
            f"# ECS Configuration",
            f"ECS_CLUSTER_NAME={result['cluster_name']}",
            f"ECS_SERVICE_NAME=vlm-worker",
            "",
            f"# VPC Configuration",
            f"VPC_ID={result['vpc']['vpc_id']}",
            f"PUBLIC_SUBNET_ID={result['vpc']['public_subnet_id']}",
            f"PRIVATE_SUBNET_ID={result['vpc']['private_subnet_id']}",
            "",
            f"# EFS Configuration",
            f"EFS_MONGODB_ID={result['efs']['mongodb']['file_system_id']}",
            f"EFS_MODELS_ID={result['efs']['models']['file_system_id']}",
            f"EFS_MONGODB_MOUNT_PATH=/mnt/efs/mongodb",
            f"EFS_MODELS_MOUNT_PATH=/mnt/efs/models",
            "",
            f"# S3 and SQS Configuration", 
            f"S3_BUCKET_NAME={result['s3_bucket']}",
            f"SQS_QUEUE_NAME={result['sqs_queue']}",
            f"SQS_QUEUE_URL=https://sqs.{result['region']}.amazonaws.com/{settings.aws_account_id}/{result['sqs_queue']}",
            "",
            f"# MongoDB Configuration",
            f"MONGO_USERNAME=admin",
            f"MONGO_PASSWORD=password",
            f"MONGO_DATABASE=crud_pdf",
            "",
            f"# Docker Compose Configuration",
            f"COMPOSE_NETWORK_SUBNET=172.20.0.0/16",
            f"ECR_REPO_NAME={settings.ecr_repo_name}",
            f"IMAGE_TAG=latest",
            f"VLM_WORKER_REPLICAS=0",
            "",
            f"# CloudWatch Logging",
            f"CLOUDWATCH_LOG_GROUP=/ecs/{settings.app_name}",
            "",
            f"# Usage:",
            f"# 1. Mount EFS file systems:",
            f"#    sudo mkdir -p /mnt/efs/mongodb /mnt/efs/models",
            f"#    sudo mount -t efs {result['efs']['mongodb']['file_system_id']}:/ /mnt/efs/mongodb",
            f"#    sudo mount -t efs {result['efs']['models']['file_system_id']}:/ /mnt/efs/models",
            f"# 2. Run: docker-compose -f docker-compose.aws-prod.yml up"
        ]
        
        with open(export_file, 'w') as f:
            f.write('\n'.join(config_lines))
        
        logger.info(f"Infrastructure configuration exported to: {export_file}")
        print(f"\nInfrastructure configuration exported to: {export_file}")
        print(f"Next steps:")
        print(f"1. Source the config: source {export_file}")
        print(f"2. Mount EFS file systems (see comments in {export_file})")
        print(f"3. Run: docker-compose -f src/files_api/docker-compose.aws-prod.yml up")
        
    except Exception as e:
        logger.error(f"Failed to export infrastructure config: {e}")
        raise


def cleanup_deployment(mode: str = None) -> None:
    """Clean up ECS deployment resources."""
    mode = mode or settings.deployment_mode
    
    if mode == "aws-mock":
        logger.info("Cleaning up mock deployment")
        try:
            # Use existing aws-mock-down function
            subprocess.run(["make", "aws-mock-down"], check=True, cwd=".")
            logger.info("Mock deployment cleaned up using existing aws-mock-down")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Mock cleanup failed: {e}")
    
    elif mode == "aws-prod":
        logger.info("Cleaning up production deployment")
        try:
            # Initialize managers
            service_manager = ECSServiceManager(settings.aws_region)
            cluster_manager = ECSClusterManager(settings.aws_region)
            efs_manager = EFSManager(settings.aws_region)
            vpc_builder = VPCNetworkBuilder(settings.aws_region)
            
            # Clean up in reverse order
            service_manager.cleanup_services_resources()
            cluster_manager.cleanup_cluster_resources()
            efs_manager.cleanup_efs_resources()
            vpc_builder.cleanup_vpc_resources()
            
            logger.info("Production deployment cleaned up")
        except Exception as e:
            logger.error(f"Production cleanup failed: {e}")
            raise


def main():
    """Main deployment entry point."""
    parser = argparse.ArgumentParser(description="Deploy ECS infrastructure for VLM workers")
    parser.add_argument("--mode", choices=["aws-mock", "aws-prod"], 
                       default=settings.deployment_mode,
                       help="Deployment mode")
    parser.add_argument("--cleanup", action="store_true", 
                       help="Clean up deployment instead of deploying")
    parser.add_argument("--docker-path", default="src/files_api/vlm",
                       help="Path to Docker build context")
    parser.add_argument("--infrastructure-only", action="store_true",
                       help="Deploy only infrastructure and export config for docker-compose")
    parser.add_argument("--export-config", default=".env.aws-prod",
                       help="File to export infrastructure configuration")
    
    args = parser.parse_args()
    
    try:
        if args.cleanup:
            cleanup_deployment(args.mode)
            return
        
        # Execute deployment
        builder = ECSDeploymentBuilder(args.mode, args.infrastructure_only)
        
        # Skip ECR image preparation for infrastructure-only mode
        if args.infrastructure_only:
            result = (builder
                     .setup_supporting_services()
                     .deploy())
        else:
            result = (builder
                     .setup_supporting_services()
                     .prepare_ecr_image(args.docker_path)
                     .deploy())
        
        # Output deployment summary
        print(json.dumps(result, indent=2, default=str))
        
        if result.get("status") == "success":
            logger.info("ECS deployment completed successfully!")
            print(f"\nDeployment Summary:")
            print(f"Mode: {result.get('mode')}")
            print(f"Region: {result.get('region')}")
            print(f"Cluster: {result.get('cluster_name')}")
            
            if args.mode == "aws-prod":
                print(f"VPC ID: {result['vpc']['vpc_id']}")
                print(f"MongoDB EFS: {result['efs']['mongodb']['file_system_id']}")
                print(f"Models EFS: {result['efs']['models']['file_system_id']}")
                
                # Export configuration for infrastructure-only mode
                if args.infrastructure_only:
                    export_infrastructure_config(result, args.export_config)
        else:
            logger.error("ECS deployment failed!")
            return 1
            
    except Exception as e:
        logger.error(f"Deployment error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())