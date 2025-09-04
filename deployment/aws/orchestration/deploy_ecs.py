"""AWS ECS deployment for VLM+RAG worker with MongoDB and GPU support."""
import logging
import json
import time
import subprocess
import argparse
import boto3
from typing import Dict, Any, List

# Import settings
from src.files_api.settings import get_settings

# Import AWS utilities
from deployment.aws.utils.aws_clients import (
    get_ecr_client,
    create_s3_bucket,
    create_sqs_queue,
    get_queue_arn
)

# Import ECS infrastructure components
from deployment.aws.infrastructure.vpc import VPCNetworkBuilder
from deployment.aws.infrastructure.efs import EFSManager
from deployment.aws.infrastructure.ecs import ECSClusterManager
from deployment.aws.infrastructure.ecs_services import ECSServiceManager
from deployment.aws.infrastructure.ec2_database import EC2DatabaseManager
from deployment.aws.infrastructure.console_validator import ConsoleResourceDetector

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
        self.database_manager = None
        self.console_validator = None
        self.deployment_config = {}
    
    def validate_gpu_quota(self, region: str) -> bool:
        """Check if we have sufficient GPU quota before deployment."""
        try:
            import boto3
            client = boto3.client('service-quotas', region_name=region)
            # Check Spot quota since we're using Spot instances  
            response = client.get_service_quota(
                ServiceCode='ec2',
                QuotaCode='L-3819A6DF'  # All G and VT Spot Instance Requests
            )
            
            current_quota = response['Quota']['Value']
            required_quota = 8.0  # 2× g4dn.xlarge = 8 vCPUs (adjusted for current quota)
            
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
        self.database_manager = EC2DatabaseManager(self.settings.aws_region)
        self.console_validator = ConsoleResourceDetector(self.settings.aws_region)
        
        logger.info("Initialized ECS infrastructure managers")
    
    @log_operation("Unified AWS deployment")
    def deploy(self) -> Dict[str, Any]:
        """Deploy complete AWS infrastructure with resource reuse detection."""
        try:
            logger.info("🚀 Starting unified AWS deployment...")
            
            # Pre-deployment validation
            if not self.validate_gpu_quota(self.settings.aws_region):
                raise Exception("Insufficient GPU quota - request increase first")
            
            # Phase 1: VPC + Networking (reuse if exists)
            logger.info("📡 Phase 1: VPC + Networking")
            vpc_config = self._setup_vpc_infrastructure()
            
            # Phase 2: EFS Storage (reuse if exists)
            logger.info("💾 Phase 2: EFS Storage")
            efs_config = self._setup_efs_storage(vpc_config)
            
            # Phase 3: EC2 Database Server (reuse if exists)
            logger.info("🗄️ Phase 3: EC2 Database Server")
            database_config = self._setup_database_infrastructure(vpc_config)
            
            # Phase 4: ECR Repository + Image (build if missing)
            logger.info("🐳 Phase 4: ECR Repository + Image")
            if not self._ensure_ecr_repository_and_image():
                raise Exception("ECR repository and image setup failed")
            
            # Phase 5: ECS Cluster + Auto Scaling (reuse if exists)
            logger.info("🏗️ Phase 5: ECS Cluster + Auto Scaling")
            cluster_config = self._setup_ecs_cluster(vpc_config)
            
            # Phase 6: ECS Tasks + Services (initial deployment without Lambda URL)
            logger.info("🔧 Phase 6: ECS Tasks + Services")
            services_config = self._deploy_services(vpc_config, efs_config)
            
            # Phase 7: Lambda Layer + Function (with all infrastructure ready)
            logger.info("⚡ Phase 7: Lambda Functions")
            lambda_config = self._deploy_lambda_functions(vpc_config, database_config)
            
            # Phase 8: Update ECS Services (add Lambda Function URL to workers)
            logger.info("🔄 Phase 8: Update ECS with Lambda Function URL")
            lambda_function_url = lambda_config.get('function_url')
            if lambda_function_url:
                self._update_ecs_with_lambda_url(services_config, lambda_function_url)
            
            # Deployment Summary
            self.deployment_config = {
                "status": "success",
                "mode": "aws-prod",
                "region": self.settings.aws_region,
                "vpc": vpc_config,
                "efs": efs_config,
                "database": database_config,
                "cluster": cluster_config,
                "services": services_config,
                "lambda": lambda_config,
                "endpoints": {
                    "lambda_function_url": lambda_function_url,
                    "database_host": database_config.get('public_ip', database_config.get('private_ip')),
                    "database_port": "8080"
                },
                "deployment_time": time.time()
            }
            
            logger.info("✅ Unified AWS deployment completed successfully!")
            self._print_deployment_summary(self.deployment_config)
            return self.deployment_config
            
        except Exception as e:
            logger.error(f"❌ Unified deployment failed: {e}")
            self.deployment_config = {
                "status": "failed",
                "error": str(e),
                "mode": "aws-prod"
            }
            raise
    
    def deploy_infrastructure_only(self) -> Dict[str, Any]:
        """Deploy only infrastructure components without services."""
        try:
            # Phase 1: Core Infrastructure
            logger.info("Infrastructure-only mode: Setting up core infrastructure")
            vpc_config = self._setup_vpc_infrastructure()
            efs_config = self._setup_efs_storage(vpc_config)
            
            # Phase 2: Database Infrastructure
            logger.info("Infrastructure-only mode: Setting up database infrastructure")
            database_config = self._setup_database_infrastructure(vpc_config)
            
            # Phase 3: ECS Infrastructure
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
                "database": database_config,
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
    
    def deploy_hybrid_console_code(self) -> Dict[str, Any]:
        """Deploy using hybrid console+code approach with console validation."""
        try:
            logger.info("🔍 Starting hybrid console+code deployment...")
            
            # Phase 1: Validate console-created resources
            logger.info("📋 Phase 1: Console Resource Validation")
            console_config = self._validate_console_prerequisites()
            
            # Phase 2: Create ECS cluster and ensure ECR image
            logger.info("🏗️ Phase 2: ECS Cluster Setup")
            validated_resources = console_config['validated_resources']
            ecs_vpc_config = {
                'vpc_id': validated_resources['vpc']['vpc_id'],
                'public_subnet_id': validated_resources['vpc']['public_subnet_id'],
                'database_security_group_id': console_config['config']['DATABASE_SG_ID'],
                'ecs_workers_security_group_id': console_config['config']['ECS_WORKERS_SG_ID'],
                'efs_security_group_id': console_config['config']['EFS_SG_ID']
            }
            cluster_config = self._setup_ecs_cluster(ecs_vpc_config)
            
            # Phase 3: Ensure ECR image exists
            logger.info("🐳 Phase 3: ECR Image Preparation")
            if not self._ensure_ecr_repository_and_image():
                raise Exception("ECR repository and image setup failed")
            
            # Phase 4: EC2 Database Instance (automated creation)
            logger.info("🗄️ Phase 4: EC2 Database Instance Creation")
            database_config = self._setup_database_infrastructure(ecs_vpc_config)
            
            # Phase 4.5: Update .env.aws-prod with DATABASE_HOST for ECS services
            logger.info("📝 Phase 4.5: Update environment configuration for ECS")
            if database_config and database_config.get('public_ip'):
                database_public_ip = database_config['public_ip']
                self._export_updated_configuration(database_public_ip, ecs_vpc_config, database_config)
                logger.info(f"Updated .env.aws-prod with DATABASE_HOST={database_public_ip}")
                # Update console config to use the new database host
                console_config['config']['DATABASE_HOST'] = database_public_ip
            else:
                logger.info("Using existing DATABASE_HOST from .env.aws-prod")
            
            # Phase 5: Code-based service deployment (ECS only)
            logger.info("🚀 Phase 5: Code-based Services Deployment")  
            services_config = self._deploy_services_only(console_config)
            
            # Note: Lambda deployment handled by parallel make target (aws-prod-lambda)
            logger.info("ℹ️  Lambda functions will be deployed in parallel by make aws-prod-lambda")
            
            # Deployment Summary
            hybrid_config = {
                "status": "success",
                "mode": "hybrid-console-code", 
                "region": self.settings.aws_region,
                "console_resources": console_config,
                "database": database_config,
                "services": services_config,
                "deployment_time": time.time()
            }
            
            logger.info("✅ Hybrid console+code deployment completed successfully!")
            self._print_hybrid_deployment_summary(hybrid_config)
            return hybrid_config
            
        except Exception as e:
            logger.error(f"❌ Hybrid deployment failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "mode": "hybrid-console-code"
            }
    
    def _validate_console_prerequisites(self) -> Dict[str, Any]:
        """Validate that console-created resources exist and are properly configured."""
        import os
        
        # Get environment variables for console resources
        console_config = {
            'VPC_ID': os.getenv('VPC_ID'),
            'PUBLIC_SUBNET_ID': os.getenv('PUBLIC_SUBNET_ID'),
            'EFS_FILE_SYSTEM_ID': os.getenv('EFS_FILE_SYSTEM_ID'),
            'EFS_ACCESS_POINT_ID': os.getenv('EFS_ACCESS_POINT_ID'),
            'S3_BUCKET_NAME': os.getenv('S3_BUCKET_NAME'),
            'SQS_QUEUE_URL': os.getenv('SQS_QUEUE_URL'),
            'DATABASE_SG_ID': os.getenv('DATABASE_SG_ID'),
            'EFS_SG_ID': os.getenv('EFS_SG_ID'),
            'ECS_WORKERS_SG_ID': os.getenv('ECS_WORKERS_SG_ID')
        }
        
        # Validate all prerequisites exist
        if not self.console_validator.validate_all_prerequisites(console_config):
            raise Exception("Console prerequisites validation failed - check environment variables and AWS resources")
        
        # Get validation results with resource details
        validation_results = self.console_validator.get_validation_results()
        
        logger.info("✅ Console prerequisites validated successfully")
        return {
            'config': console_config,
            'validated_resources': validation_results['resources']
        }
    
    def _deploy_services_only(self, console_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy ECS services using console-created infrastructure."""
        # Extract validated resources from console config
        validated_resources = console_config['validated_resources']
        
        # Create simplified config for ECS deployment
        ecs_vpc_config = {
            'vpc_id': validated_resources['vpc']['vpc_id'],
            'public_subnet_id': validated_resources['vpc']['public_subnet_id'],
            'ecs_workers_security_group_id': console_config['config']['ECS_WORKERS_SG_ID'],
            'efs_security_group_id': console_config['config']['EFS_SG_ID']
        }
        
        ecs_efs_config = {
            'shared_models': {
                'file_system_id': validated_resources['efs']['file_system_id'],
                'access_point_id': validated_resources['efs']['access_point_id']
            }
        }
        
        # Deploy ECS services with database host from console config  
        database_host = console_config['config'].get('DATABASE_HOST', 'localhost')
        deployment_result = self.service_manager.deploy_all_services(ecs_vpc_config, ecs_efs_config, database_host)
        
        if deployment_result['status'] == 'success':
            logger.info("✅ ECS services deployed successfully using console infrastructure")
            return deployment_result
        else:
            raise Exception(f"ECS services deployment failed: {deployment_result.get('error')}")
    
    def _deploy_lambda_no_vpc(self, console_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy Lambda functions without VPC attachment (optimized architecture)."""
        from deployment.aws.services.lambda_deploy import LambdaDeployer
        
        deployer = LambdaDeployer(region=self.settings.aws_region)
        
        # Deploy Files API Lambda without VPC (direct internet access to database)
        lambda_config = deployer.deploy_files_api_lambda_no_vpc(
            database_host=console_config['config'].get('DATABASE_HOST', 'localhost'),
            database_port=8080
        )
        
        logger.info(f"Lambda deployed without VPC: {lambda_config['function_name']}")
        if 'function_url' in lambda_config:
            logger.info(f"Function URL: {lambda_config['function_url']}")
        
        return lambda_config
    
    def _print_hybrid_deployment_summary(self, config: Dict[str, Any]) -> None:
        """Print hybrid deployment summary."""
        logger.info("=" * 60)
        logger.info("🎉 HYBRID DEPLOYMENT SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Status: {config['status']}")
        logger.info(f"Mode: {config['mode']}")
        logger.info(f"Region: {config['region']}")
        
        if 'console_resources' in config:
            console_resources = config['console_resources']['validated_resources']
            logger.info(f"Console VPC: {console_resources['vpc']['vpc_id']}")
            logger.info(f"Console EFS: {console_resources['efs']['file_system_id']}")
            logger.info(f"Console Storage: S3={console_resources['storage']['s3_bucket']}")
        
        if 'lambda' in config:
            lambda_info = config['lambda']
            logger.info(f"Lambda Function: {lambda_info['function_name']}")
            if 'function_url' in lambda_info:
                logger.info(f"Function URL: {lambda_info['function_url']}")
        
        logger.info("=" * 60)
    
    @log_operation("VPC and networking setup")
    def _setup_vpc_infrastructure(self) -> Dict[str, Any]:
        """Set up VPC infrastructure."""
        # Build VPC with single AZ design
        vpc_config = (self.vpc_builder
                     .build_vpc("10.0.0.0/16")
                     .build_subnets()  # Single AZ for cost optimization
                     .build_internet_gateway()
                     .build_nat_gateway()
                     .build_route_tables()
                     .build_security_groups()
                     .get_network_config())
        
        logger.info(f"VPC created: {vpc_config['vpc_id']}")
        return vpc_config
    
    @log_operation("EFS storage setup")
    def _setup_efs_storage(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up EFS file system for shared model storage only (optimized architecture)."""
        # Create single shared models EFS (for both model-downloader and vlm-worker)
        shared_models_efs = self.efs_manager.create_shared_models_efs(
            vpc_config['vpc_id'],
            vpc_config['public_subnet_id'],  # Use public subnet in optimized architecture
            vpc_config['efs_security_group_id']
        )
        
        # Wait for mount targets to be available
        self.efs_manager.wait_for_mount_targets_available()
        
        efs_config = {
            "shared_models": shared_models_efs
        }
        
        logger.info(f"EFS shared models storage created: {shared_models_efs['file_system_id']}")
        return efs_config
    
    @log_operation("EC2 database server setup")
    def _setup_database_infrastructure(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up EC2 SQLite HTTP database server in public subnet (using EC2 boot volume)."""
        # Use public subnet for database deployment with console-created security group
        database_config = self.database_manager.create_database_instance(vpc_config)
        
        # Wait for manual setup completion if this is a new instance
        if database_config.get('manual_setup_required'):
            logger.info("📋 Manual SQLite setup required - see deployment/aws/services/README.md")
            logger.info(f"🔗 SSH command: {database_config.get('ssh_command', 'N/A')}")
            
            # Optionally wait for manual setup completion
            setup_complete = self.database_manager.wait_for_manual_setup_completion(
                database_config['private_ip'], timeout_minutes=5  # Short timeout for automated deployment
            )
            
            if not setup_complete:
                logger.warning("⚠️ Manual setup not completed - continuing deployment")
                logger.info("You can complete setup later using the SSH command above")
        
        logger.info(f"Database server ready: {database_config['instance_id']} at {database_config.get('public_ip', database_config['private_ip'])}")
        
        return database_config
    
    @log_operation("Lambda functions deployment")
    def _deploy_lambda_functions(self, vpc_config: Dict[str, Any], database_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy Lambda functions with VPC and database configuration."""
        from deployment.aws.services.lambda_deploy import LambdaDeployer
        
        deployer = LambdaDeployer(region=self.settings.aws_region)
        
        # Deploy Files API Lambda with VPC and database configuration
        lambda_config = deployer.deploy_files_api_lambda(
            vpc_config=vpc_config,
            database_host=database_config['private_ip'],
            database_port=8080
        )
        
        logger.info(f"Lambda deployed: {lambda_config['function_name']}")
        if 'function_url' in lambda_config:
            logger.info(f"Function URL: {lambda_config['function_url']}")
        
        return lambda_config
    
    @log_operation("ECS cluster setup")
    def _setup_ecs_cluster(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up ECS cluster with GPU capacity provider."""
        cluster_config = self.cluster_manager.create_cluster(
            vpc_config['vpc_id'],
            [vpc_config['public_subnet_id']]
        )
        
        logger.info(f"ECS cluster created: {cluster_config['clusterName']}")
        return self.cluster_manager.get_cluster_info()
    
    def _check_image_exists(self, ecr_client) -> bool:
        """Check if ECR image already exists."""
        try:
            response = ecr_client.describe_images(
                repositoryName=ECR_REPO_NAME,
                imageIds=[{'imageTag': 'latest'}]
            )
            images = response.get('imageDetails', [])
            if images:
                image_size_mb = images[0].get('imageSizeInBytes', 0) / (1024 * 1024)
                logger.info(f"Found existing image: {image_size_mb:.1f} MB, pushed {images[0].get('imagePushedAt', 'unknown time')}")
                return True
            return False
        except ecr_client.exceptions.ImageNotFoundException:
            logger.info("No existing image found")
            return False
        except Exception as e:
            logger.warning(f"Could not check for existing image: {e}")
            return False
    
    def _deploy_services(self, vpc_config: Dict[str, Any], efs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy ECS services with Lambda Function URL integration."""
        logger.info(f"✅ ECR image {ECR_REPO_NAME}:latest ready for deployment")
        
        # Deploy VLM worker services initially (Lambda Function URL added later)
        logger.info("📦 Deploying VLM workers (Lambda Function URL will be added later)")
        
        # Pass database host from earlier database deployment (prefer public IP)
        database_host = self.deployment_config.get('endpoints', {}).get('database_host', 'localhost')
        deployment_result = self.service_manager.deploy_all_services(vpc_config, efs_config, database_host)
        
        if deployment_result['status'] == 'success':
            services_deployed = deployment_result['services_deployed']
            logger.info(f"✅ VLM Workers: {services_deployed['vlm_workers']['service_name']}")
            
            return {
                'vlm_workers': services_deployed['vlm_workers'],
                'deployment_summary': deployment_result
            }
        else:
            logger.error(f"Service deployment failed: {deployment_result.get('error')}")
            raise Exception(f"ECS services deployment failed: {deployment_result.get('error')}")

    def _update_ecs_with_lambda_url(self, services_config: Dict[str, Any], lambda_function_url: str) -> None:
        """Update ECS task definitions to include Lambda Function URL."""
        try:
            vlm_service_info = services_config.get('vlm_workers', {})
            service_name = vlm_service_info.get('service_name')
            
            if not service_name:
                logger.warning("No VLM service found - skipping Lambda URL update")
                return
            
            logger.info(f"🔗 Adding Lambda Function URL to ECS service: {service_name}")
            logger.info(f"Function URL: {lambda_function_url}")
            
            # For now, log that the URL is available for manual API Gateway setup
            # In a production system, you might update the task definition here
            # But since we're manually creating API Gateway, we'll skip automatic updates
            
            logger.info("✅ Lambda Function URL available for API Gateway integration")
            logger.info("Note: You can manually create API Gateway and point it to this Lambda Function URL")
            
        except Exception as e:
            logger.warning(f"Failed to update ECS with Lambda URL: {e}")
    
    def _print_deployment_summary(self, config: Dict[str, Any]) -> None:
        """Print deployment summary."""
        logger.info("=" * 60)
        logger.info("🎉 DEPLOYMENT SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Status: {config['status']}")
        logger.info(f"Mode: {config['mode']}")
        logger.info(f"Region: {config['region']}")
        
        if 'endpoints' in config:
            endpoints = config['endpoints']
            logger.info(f"Lambda Function URL: {endpoints.get('lambda_function_url', 'N/A')}")
            logger.info(f"Database: {endpoints.get('database_host', 'N/A')}:{endpoints.get('database_port', 'N/A')}")
        
        if 'vpc' in config:
            logger.info(f"VPC: {config['vpc'].get('vpc_id', 'N/A')}")
        
        if 'cluster' in config:
            logger.info(f"ECS Cluster: {config['cluster'].get('cluster_name', 'N/A')}")
        
        logger.info("=" * 60)
    
    @log_operation("Auto-scaling simulator setup")
    def _setup_auto_scaling(self, services_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up SQS-based scaling simulator (proven main branch approach)."""
        try:
            vlm_service_info = services_config.get('vlm_workers', {})
            vlm_service_name = vlm_service_info.get('service_name', 'vlm-workers')
            
            # Get SQS queue URL
            queue_url = settings.sqs_queue_url
            if not queue_url:
                logger.warning("SQS queue URL not available - will use fallback URL")
                queue_url = f"https://sqs.{settings.aws_region}.amazonaws.com/{settings.account_id}/{settings.sqs_queue_name}"
            
            logger.info(f"Setting up SQS-based scaling simulator for {vlm_service_name}")
            logger.info(f"Queue URL: {queue_url}")
            
            # Create scaling configuration (using proven main branch approach)
            scaling_config = {
                "vlm_workers": {
                    "service_name": vlm_service_name,
                    "cluster_name": ECS_CLUSTER_NAME,
                    "queue_url": queue_url,
                    "min_capacity": 0,  # Scale to zero
                    "max_capacity": 2,  # Adjusted for GPU quota
                    "scale_up_threshold": 1,  # 1 message = scale up
                    "scale_down_threshold": 0,  # 0 messages = scale to zero
                    "cooldown_period": 300,  # 5 minutes
                    "evaluation_interval": 15,  # Check every 15 seconds
                    "scaling_method": "sqs_based_simulation",  # Use proven approach
                    "status": "configured"
                }
            }
            
            logger.info(f"✅ SQS-based scaling configured for {vlm_service_name}")
            logger.info(f"   Instance range: 0-2 (scale-to-zero enabled)")
            logger.info(f"   Thresholds: scale_up=1 msg, scale_down=0 msg")
            logger.info(f"   Evaluation: every 15s, cooldown=300s")
            
            return scaling_config
            
        except Exception as e:
            logger.error(f"Failed to setup SQS-based scaling: {e}")
            
            # Minimal fallback configuration
            scaling_config = {
                "vlm_workers": {
                    "service_name": vlm_service_name,
                    "status": "fallback_configuration",
                    "error": str(e)
                }
            }
            
            logger.warning(f"Using fallback scaling configuration for {vlm_service_name}")
            return scaling_config

    def _check_ecr_repository_exists(self, ecr_client) -> bool:
        """Check if ECR repository exists."""
        try:
            ecr_client.describe_repositories(repositoryNames=[ECR_REPO_NAME])
            logger.info(f"ECR repository '{ECR_REPO_NAME}' exists")
            return True
        except ecr_client.exceptions.RepositoryNotFoundException:
            logger.info(f"ECR repository '{ECR_REPO_NAME}' does not exist - will create")
            return False
        except Exception as e:
            logger.warning(f"Could not check ECR repository: {e}")
            return False
    
    def _ensure_ecr_repository_and_image(self) -> bool:
        """Ensure ECR repository exists and has the required image."""
        ecr_client = boto3.client('ecr', region_name=self.settings.aws_region)
        
        # Step 1: Check if repository exists
        if not self._check_ecr_repository_exists(ecr_client):
            logger.info("Creating ECR repository...")
            self._create_ecr_repository(ecr_client)
        
        # Step 2: Check if image exists in repository
        if self._check_image_exists(ecr_client):
            logger.info("✅ ECR image validation successful - using existing image")
            return True
        
        # Step 3: Build and push image if not found
        logger.info("📦 ECR image not found - building and pushing new image")
        self._build_and_push_ecr_image()
        
        # Step 4: Verify image was pushed successfully
        if self._check_image_exists(ecr_client):
            logger.info("✅ ECR image build and push successful")
            return True
        else:
            logger.error("❌ ECR image build/push failed - image still not found")
            return False
    
    def _create_ecr_repository(self, ecr_client) -> None:
        """Create ECR repository if it doesn't exist."""
        try:
            ecr_client.create_repository(
                repositoryName=ECR_REPO_NAME,
                imageScanningConfiguration={'scanOnPush': True},
                tags=[
                    {'Key': 'Project', 'Value': self.settings.app_name},
                    {'Key': 'Component', 'Value': 'VLM-Worker'},
                    {'Key': 'Purpose', 'Value': 'Container-Registry'}
                ]
            )
            logger.info(f"Created ECR repository: {ECR_REPO_NAME}")
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            logger.info(f"ECR repository {ECR_REPO_NAME} already exists")
        except Exception as e:
            logger.error(f"Failed to create ECR repository: {e}")
            raise
    
    def _build_and_push_ecr_image(self) -> None:
        """Build and push Docker image to ECR with proper error handling."""
        try:
            # Get ECR login token
            ecr_client = boto3.client('ecr', region_name=self.settings.aws_region)
            token_response = ecr_client.get_authorization_token()
            token_data = token_response['authorizationData'][0]
            
            # Find Docker path (VLM worker)
            docker_image_path = self._find_docker_path()
            
            # Build and push image
            self._build_and_push_image(docker_image_path, token_data)
            
        except Exception as e:
            logger.error(f"ECR build and push failed: {e}")
            raise
    
    def _find_docker_path(self) -> str:
        """Find the Docker image path for VLM worker."""
        from pathlib import Path
        
        # Look for VLM worker Dockerfile in the correct location
        project_root = Path(__file__).parent.parent.parent.parent
        vlm_docker_path = project_root / "deployment" / "docker" / "vlm-worker"
        
        if (vlm_docker_path / "Dockerfile").exists():
            logger.info(f"Found VLM Dockerfile at: {vlm_docker_path}")
            return str(vlm_docker_path)
        else:
            # Fallback: try the old location in case project structure varies
            fallback_path = project_root / "src" / "files_api" / "vlm"
            if (fallback_path / "Dockerfile").exists():
                logger.info(f"Found VLM Dockerfile at fallback location: {fallback_path}")
                return str(fallback_path)
            
            raise Exception(f"VLM Dockerfile not found at: {vlm_docker_path} or {fallback_path}")
    
    def _check_image_exists(self, ecr_client) -> bool:
        """Check if ECR image already exists."""
        try:
            response = ecr_client.describe_images(
                repositoryName=ECR_REPO_NAME,
                imageIds=[{'imageTag': 'latest'}]
            )
            images = response.get('imageDetails', [])
            if images:
                image_size_mb = images[0].get('imageSizeInBytes', 0) / (1024 * 1024)
                logger.info(f"Found existing image: {image_size_mb:.1f} MB, pushed {images[0].get('imagePushedAt', 'unknown time')}")
                return True
            return False
        except ecr_client.exceptions.ImageNotFoundException:
            logger.info("No existing image found - will build new one")
            return False
        except Exception as e:
            logger.warning(f"Could not check for existing image: {e} - will build new one")
            return False
    
    def _build_and_push_image(self, docker_path: str, token_data: Dict[str, Any]) -> None:
        """Build and push Docker image to ECR."""
        import base64
        import subprocess
        from pathlib import Path
        
        # Decode ECR token
        token = base64.b64decode(token_data['authorizationToken']).decode('utf-8')
        username, password = token.split(':')
        
        # Build ECR URI
        account_id = token_data['proxyEndpoint'].split('.')[0].split('//')[-1]
        ecr_uri = f"{account_id}.dkr.ecr.{self.settings.aws_region}.amazonaws.com/{ECR_REPO_NAME}:latest"
        
        # Docker login
        subprocess.run([
            "docker", "login", "--username", username, "--password-stdin",
            token_data['proxyEndpoint']
        ], input=password.encode(), check=True)
        
        # Build image from project root
        project_root = Path(__file__).parent.parent.parent.parent
        subprocess.run([
            "docker", "build", "-t", ecr_uri, "-f", f"{docker_path}/Dockerfile", "."
        ], check=True, cwd=project_root)
        
        # Push image
        subprocess.run([
            "docker", "push", ecr_uri
        ], check=True)
        
        logger.info(f"Pushed image to ECR: {ecr_uri}")
    
    def _update_lambda_environment_variables(self, database_public_ip: str) -> None:
        """Update Lambda environment variables with actual database IP."""
        try:
            lambda_client = boto3.client('lambda', region_name=self.settings.aws_region)
            function_name = f"{self.settings.app_name}-files-api"
            
            # Get current environment variables
            response = lambda_client.get_function_configuration(FunctionName=function_name)
            
            # Update with database public IP
            env_vars = response.get('Environment', {}).get('Variables', {})
            env_vars.update({
                'DATABASE_HOST': database_public_ip,
                'DATABASE_PORT': '8080',
                'DEPLOYMENT_MODE': 'aws-prod'
            })
            
            # Update Lambda function
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Environment={'Variables': env_vars}
            )
            
            logger.info(f"Updated Lambda environment variables: DATABASE_HOST={database_public_ip}")
            
        except Exception as e:
            logger.error(f"Failed to update Lambda environment variables: {e}")
            raise
    
    def _configure_post_deployment_environment(self, vpc_config: Dict[str, Any], database_config: Dict[str, Any]) -> None:
        """Configure environment variables after all resources are created."""
        try:
            # Get database public IP
            database_public_ip = database_config.get('public_ip')
            if not database_public_ip:
                database_public_ip = self.database_manager.get_instance_public_ip(
                    database_config['instance_id']
                )
            
            # Update Lambda environment variables
            self._update_lambda_environment_variables(database_public_ip)
            
            # Update .env.aws-prod file
            self._export_updated_configuration(database_public_ip, vpc_config, database_config)
            
            logger.info("Post-deployment environment configuration completed")
            
        except Exception as e:
            logger.error(f"Failed to configure post-deployment environment: {e}")
            raise
    
    def _export_updated_configuration(self, database_public_ip: str, vpc_config: Dict[str, Any], database_config: Dict[str, Any]) -> None:
        """Update .env.aws-prod file with database public IP."""
        try:
            import os
            export_file = ".env.aws-prod"
            
            # Read existing configuration if it exists
            config_lines = []
            if os.path.exists(export_file):
                with open(export_file, 'r') as f:
                    config_lines = f.readlines()
            
            # Update or add database configuration
            updated_lines = []
            database_host_updated = False
            database_public_ip_updated = False
            
            for line in config_lines:
                if line.startswith('DATABASE_HOST='):
                    updated_lines.append(f"DATABASE_HOST={database_public_ip}\n")
                    database_host_updated = True
                elif line.startswith('DATABASE_PUBLIC_IP='):
                    updated_lines.append(f"DATABASE_PUBLIC_IP={database_public_ip}\n")
                    database_public_ip_updated = True
                else:
                    updated_lines.append(line)
            
            # Add missing configuration
            if not database_host_updated:
                updated_lines.append(f"DATABASE_HOST={database_public_ip}\n")
            if not database_public_ip_updated:
                updated_lines.append(f"DATABASE_PUBLIC_IP={database_public_ip}\n")
            
            # Write updated configuration
            with open(export_file, 'w') as f:
                f.writelines(updated_lines)
            
            logger.info(f"Updated {export_file} with database public IP: {database_public_ip}")
            
        except Exception as e:
            logger.error(f"Failed to export updated configuration: {e}")
            raise


class ECSDeploymentBuilder:
    """Builder for ECS deployment with different strategies."""
    
    def __init__(self, mode: str = None, infrastructure_only: bool = False, hybrid_console: bool = False):
        self.mode = mode or settings.deployment_mode
        self.infrastructure_only = infrastructure_only
        self.hybrid_console = hybrid_console
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
        docker_image_path = docker_image_path or "deployment/docker/vlm-worker"
        
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
            
            # Check if image already exists
            if self._check_image_exists(ecr_client):
                logger.info(f"✅ Using existing ECR image: {self.ecr_uri}")
            else:
                logger.info(f"📦 Building and pushing new ECR image: {self.ecr_uri}")
                # Docker build and push operations
                self._build_and_push_image(docker_image_path, token_data)
            
            logger.info(f"ECR image ready: {self.ecr_uri}")
            
        except Exception as e:
            logger.error(f"ECR preparation failed: {e}")
            raise
        
        return self
    
    def _check_ecr_repository_exists(self, ecr_client) -> bool:
        """Check if ECR repository exists."""
        try:
            ecr_client.describe_repositories(repositoryNames=[ECR_REPO_NAME])
            logger.info(f"ECR repository '{ECR_REPO_NAME}' exists")
            return True
        except ecr_client.exceptions.RepositoryNotFoundException:
            logger.info(f"ECR repository '{ECR_REPO_NAME}' does not exist - will create")
            return False
        except Exception as e:
            logger.warning(f"Could not check ECR repository: {e}")
            return False
    
    def _ensure_ecr_repository_and_image(self) -> bool:
        """Ensure ECR repository exists and has the required image."""
        ecr_client = boto3.client('ecr', region_name=self.settings.aws_region)
        
        # Step 1: Check if repository exists
        if not self._check_ecr_repository_exists(ecr_client):
            logger.info("Creating ECR repository...")
            self._create_ecr_repository(ecr_client)
        
        # Step 2: Check if image exists in repository
        if self._check_image_exists(ecr_client):
            logger.info("✅ ECR image validation successful - using existing image")
            return True
        
        # Step 3: Build and push image if not found
        logger.info("📦 ECR image not found - building and pushing new image")
        self._build_and_push_ecr_image()
        
        # Step 4: Verify image was pushed successfully
        if self._check_image_exists(ecr_client):
            logger.info("✅ ECR image build and push successful")
            return True
        else:
            logger.error("❌ ECR image build/push failed - image still not found")
            return False
    
    def _create_ecr_repository(self, ecr_client) -> None:
        """Create ECR repository if it doesn't exist."""
        try:
            ecr_client.create_repository(
                repositoryName=ECR_REPO_NAME,
                imageScanningConfiguration={'scanOnPush': True},
                tags=[
                    {'Key': 'Project', 'Value': self.settings.app_name},
                    {'Key': 'Component', 'Value': 'VLM-Worker'},
                    {'Key': 'Purpose', 'Value': 'Container-Registry'}
                ]
            )
            logger.info(f"Created ECR repository: {ECR_REPO_NAME}")
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            logger.info(f"ECR repository {ECR_REPO_NAME} already exists")
        except Exception as e:
            logger.error(f"Failed to create ECR repository: {e}")
            raise
    
    def _build_and_push_ecr_image(self) -> None:
        """Build and push Docker image to ECR with proper error handling."""
        try:
            # Get ECR login token
            ecr_client = boto3.client('ecr', region_name=self.settings.aws_region)
            token_response = ecr_client.get_authorization_token()
            token_data = token_response['authorizationData'][0]
            
            # Find Docker path (VLM worker)
            docker_image_path = self._find_docker_path()
            
            # Build and push image
            self._build_and_push_image(docker_image_path, token_data)
            
        except Exception as e:
            logger.error(f"ECR build and push failed: {e}")
            raise
    
    def _find_docker_path(self) -> str:
        """Find the Docker image path for VLM worker."""
        from pathlib import Path
        
        # Look for VLM worker Dockerfile in the correct location
        project_root = Path(__file__).parent.parent.parent.parent
        vlm_docker_path = project_root / "deployment" / "docker" / "vlm-worker"
        
        if (vlm_docker_path / "Dockerfile").exists():
            logger.info(f"Found VLM Dockerfile at: {vlm_docker_path}")
            return str(vlm_docker_path)
        else:
            # Fallback: try the old location in case project structure varies
            fallback_path = project_root / "src" / "files_api" / "vlm"
            if (fallback_path / "Dockerfile").exists():
                logger.info(f"Found VLM Dockerfile at fallback location: {fallback_path}")
                return str(fallback_path)
            
            raise Exception(f"VLM Dockerfile not found at: {vlm_docker_path} or {fallback_path}")
    
    def _check_image_exists(self, ecr_client) -> bool:
        """Check if ECR image already exists."""
        try:
            response = ecr_client.describe_images(
                repositoryName=ECR_REPO_NAME,
                imageIds=[{'imageTag': 'latest'}]
            )
            images = response.get('imageDetails', [])
            if images:
                image_size_mb = images[0].get('imageSizeInBytes', 0) / (1024 * 1024)
                logger.info(f"Found existing image: {image_size_mb:.1f} MB, pushed {images[0].get('imagePushedAt', 'unknown time')}")
                return True
            return False
        except ecr_client.exceptions.ImageNotFoundException:
            logger.info("No existing image found - will build new one")
            return False
        except Exception as e:
            logger.warning(f"Could not check for existing image: {e} - will build new one")
            return False
    
    def _build_and_push_image(self, docker_path: str, token_data: Dict[str, Any]) -> None:
        """Build and push Docker image to ECR."""
        import base64
        import os
        
        # Decode ECR token
        token = base64.b64decode(token_data['authorizationToken']).decode('utf-8')
        username, password = token.split(':')
        
        # Docker login
        subprocess.run([
            "docker", "login", "--username", username, "--password-stdin",
            token_data['proxyEndpoint']
        ], input=password.encode(), check=True)
        
        # Get the project root directory (where src/ directory exists)
        current_dir = os.getcwd()
        project_root = current_dir
        
        # If we're not in the project root, find it
        while not os.path.exists(os.path.join(project_root, "src")):
            parent = os.path.dirname(project_root)
            if parent == project_root:  # Reached filesystem root
                raise Exception("Could not find project root (directory containing 'src' folder)")
            project_root = parent
        
        # Build image from project root
        subprocess.run([
            "docker", "build", "-t", self.ecr_uri, "-f", f"{docker_path}/Dockerfile", "."
        ], check=True, cwd=project_root)
        
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
        
        if self.hybrid_console and hasattr(self.strategy, 'deploy_hybrid_console_code'):
            return self.strategy.deploy_hybrid_console_code()
        elif self.infrastructure_only and hasattr(self.strategy, 'deploy_infrastructure_only'):
            return self.strategy.deploy_infrastructure_only()
        else:
            return self.strategy.deploy()


def export_infrastructure_config(result: Dict[str, Any], export_file: str) -> None:
    """Export infrastructure configuration to environment file for docker-compose."""
    if result.get("status") != "success":
        logger.error("Cannot export config from failed deployment")
        return
    
    try:
        import os
        config_lines = [
            "# AWS Infrastructure Configuration for Docker Compose",
            "# Generated by deploy_ecs.py --infrastructure-only",
            "",
            f"# AWS Credentials (from environment)",
            f"AWS_ACCESS_KEY_ID={os.environ.get('AWS_ACCESS_KEY_ID', '')}",
            f"AWS_SECRET_ACCESS_KEY={os.environ.get('AWS_SECRET_ACCESS_KEY', '')}",
            "",
            f"# Deployment Info",
            f"APP_NAME=\"{settings.app_name}\"",
            f"AWS_DEFAULT_REGION={result['region']}",
            f"AWS_ACCOUNT_ID={settings.account_id}",
            f"DEPLOYMENT_MODE=aws-prod",
            "",
            f"# ECS Configuration",
            f"ECS_CLUSTER_NAME={result['cluster_name']}",
            f"ECS_SERVICE_NAME=vlm-worker",
            "",
            f"# VPC Configuration (Public Subnet Only Architecture)",
            f"VPC_ID={result['vpc']['vpc_id']}",
            f"PUBLIC_SUBNET_ID={result['vpc']['public_subnet_id']}",
            "",
            f"# EFS Configuration (Optimized Architecture)",
            f"EFS_SHARED_MODELS_ID={result['efs']['shared_models']['file_system_id']}",
            f"EFS_SHARED_MODELS_ACCESS_POINT_ID={result['efs']['shared_models']['access_point_id']}",
            f"EFS_SHARED_MODELS_MOUNT_PATH=/app/cache",
            "",
            f"# S3 and SQS Configuration", 
            f"S3_BUCKET_NAME={result['s3_bucket']}",
            f"SQS_QUEUE_NAME={result['sqs_queue']}",
            f"SQS_QUEUE_URL=https://sqs.{result['region']}.amazonaws.com/{settings.account_id}/{result['sqs_queue']}",
            "",
            f"# Database Configuration",
            f"DATABASE_HOST={result['database'].get('public_ip', result['database']['private_ip'])}",
            f"DATABASE_PORT=8080",
            f"DATABASE_PUBLIC_IP={result['database'].get('public_ip', 'N/A')}",
            f"DATABASE_INSTANCE_ID={result['database']['instance_id']}",
            "",
            f"# Docker Compose Configuration",
            f"COMPOSE_NETWORK_SUBNET=172.20.0.0/16",
            f"ECR_REPO_NAME={settings.ecr_repo_name}",
            f"ECR_REGISTRY={settings.ecr_registry}",
            f"IMAGE_TAG=latest",
            f"VLM_WORKER_REPLICAS=0",
            "",
            f"# CloudWatch Logging",
            f"CLOUDWATCH_LOG_GROUP=\"/ecs/{settings.app_name}\"",
            "",
            f"# Usage:",
            f"# 1. ECS tasks automatically mount EFS to /app/cache inside containers",
            f"# 2. For manual testing/debugging, mount EFS access point:",
            f"#    sudo mkdir -p /mnt/efs/cache",
            f"#    sudo mount -t efs -o tls,accesspoint={result['efs']['shared_models']['access_point_id']} {result['efs']['shared_models']['file_system_id']}:/ /mnt/efs/cache",
            f"# 2. Database server is running at: http://{result['database']['private_ip']}:8080",
            f"# 3. Database files stored on EC2 boot volume (no EFS needed)",
            f"# 4. Run: docker-compose -f docker-compose.aws-prod.yml up"
        ]
        
        with open(export_file, 'w') as f:
            f.write('\n'.join(config_lines))
        
        logger.info(f"Infrastructure configuration exported to: {export_file}")
        print(f"\nInfrastructure configuration exported to: {export_file}")
        print(f"Next steps:")
        print(f"1. Source the config: source {export_file}")
        print(f"2. Mount shared models EFS (see comments in {export_file})")
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
            database_manager = EC2DatabaseManager(settings.aws_region)
            efs_manager = EFSManager(settings.aws_region)
            vpc_builder = VPCNetworkBuilder(settings.aws_region)
            
            # Clean up in reverse order (ECS + ASG scaling cleanup is handled automatically by AWS)
            logger.info("Phase 1: Cleaning up ECS services")
            service_manager.cleanup_services_resources()
            
            logger.info("Phase 2: Cleaning up ECS cluster")
            cluster_manager.cleanup_cluster_resources()
            
            logger.info("Phase 3: Cleaning up database infrastructure")
            database_manager.cleanup_database_instance()
            
            logger.info("Phase 4: Cleaning up EFS resources")
            efs_manager.cleanup_efs_resources()
            
            logger.info("Phase 5: Cleaning up VPC resources")
            vpc_builder.cleanup_vpc_resources()
            
            logger.info("✅ Production deployment cleaned up successfully")
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
    parser.add_argument("--docker-path", default="deployment/docker/vlm-worker",
                       help="Path to Docker build context")
    parser.add_argument("--infrastructure-only", action="store_true",
                       help="Deploy only infrastructure and export config for docker-compose")
    parser.add_argument("--deploy-services", action="store_true",
                       help="Deploy ECS services (full deployment with containers)")
    parser.add_argument("--hybrid-console", action="store_true",
                       help="Deploy using hybrid console+code approach")
    parser.add_argument("--export-config", default=".env.aws-prod",
                       help="File to export infrastructure configuration")
    parser.add_argument("--validate-only", action="store_true",
                       help="Only validate console infrastructure, don't deploy")
    
    args = parser.parse_args()
    
    try:
        if args.cleanup:
            cleanup_deployment(args.mode)
            return
            
        if args.validate_only:
            # Use existing resource validator for console infrastructure
            logger.info("Validating console infrastructure...")
            import subprocess
            import sys
            import os
            
            # Debug: Print key environment variables
            required_vars = ['VPC_ID', 'PUBLIC_SUBNET_ID', 'EFS_FILE_SYSTEM_ID', 'EFS_ACCESS_POINT_ID', 
                           'S3_BUCKET_NAME', 'SQS_QUEUE_URL', 'DATABASE_SG_ID', 'EFS_SG_ID', 'ECS_WORKERS_SG_ID']
            print("🔍 Environment variables for validation:")
            for var in required_vars:
                value = os.getenv(var, 'NOT_SET')
                print(f"  {var}={value}")
            print()
            
            # Pass current environment (includes variables from .env.aws-prod)
            result = subprocess.run([
                sys.executable, "-m", "deployment.aws.monitoring.resource_validator", 
                "--check", "console"
            ], capture_output=True, text=True, env=os.environ.copy())
            
            if result.returncode == 0:
                logger.info("✅ Console infrastructure validation successful!")
                print(result.stdout)
            else:
                logger.error("❌ Console infrastructure validation failed!")
                print(result.stderr)
            
            sys.exit(result.returncode)
        
        # Execute deployment
        builder = ECSDeploymentBuilder(args.mode, args.infrastructure_only, args.hybrid_console)
        
        # Choose deployment type based on flags
        if args.hybrid_console:
            # Hybrid console+code deployment
            logger.info("Using hybrid console+code deployment approach")
            result = builder.deploy()
        elif args.infrastructure_only:
            # Infrastructure only - no ECR or services
            result = (builder
                     .setup_supporting_services()
                     .deploy())
        elif args.deploy_services:
            # Full deployment with ECR images and services
            result = (builder
                     .setup_supporting_services()
                     .prepare_ecr_image(args.docker_path)
                     .deploy())
        else:
            # Default: infrastructure only for backward compatibility
            result = (builder
                     .setup_supporting_services()
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
                if result.get('mode') == 'hybrid-console-code':
                    print(f"Console VPC: {result['console_resources']['validated_resources']['vpc']['vpc_id']}")
                    print(f"Console EFS: {result['console_resources']['validated_resources']['efs']['file_system_id']}")
                    print(f"Lambda Function: {result['lambda']['function_name']}")
                    if 'function_url' in result['lambda']:
                        print(f"Function URL: {result['lambda']['function_url']}")
                else:
                    print(f"VPC ID: {result['vpc']['vpc_id']}")
                    print(f"Database Server: {result['database']['instance_id']} at {result['database']['private_ip']}:8080")
                    print(f"Shared Models EFS: {result['efs']['shared_models']['file_system_id']}")
                    
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