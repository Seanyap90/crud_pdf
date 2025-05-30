"""AWS Deployment builder for Files API application resources."""
import os
import logging
import json
import time
import subprocess
import threading
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from contextlib import contextmanager
from files_api.settings import get_settings

# Import AWS utilities
from files_api.aws.utils import (
    AWSClientManager,
    get_s3_client,
    get_sqs_client,
    get_ec2_client,
    get_iam_client,
    get_asg_client,
    get_ecr_client,
    get_cloudwatch_client,
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
MOTO_SERVER_PORT = 5000  # This can stay hardcoded as it's moto-specific
S3_BUCKET_NAME = settings.s3_bucket_name
SQS_QUEUE_NAME = settings.sqs_queue_name
ECR_REPO_NAME = settings.ecr_repo_name
IAM_ROLE_NAME = settings.iam_role_name
IAM_INSTANCE_PROFILE = settings.iam_instance_profile

# Strategy pattern for different deployment modes
class DeploymentStrategy:
    """Base class for deployment mode strategies."""
    
    def setup_clients(self) -> None:
        """Set up AWS clients."""
        pass
    
    def start_moto_server(self) -> None:
        """Start moto server if needed."""
        pass
    
    def stop_moto_server(self) -> None:
        """Stop moto server if needed."""
        pass
    
    def get_user_data_script(self, resources: Dict[str, Any]) -> str:
        """Get user data script for EC2 instances."""
        pass

class MockAWSStrategy(DeploymentStrategy):
    """Strategy for aws-mock deployment mode."""
    
    def __init__(self):
        self.endpoint_url = f"http://localhost:{MOTO_SERVER_PORT}"
        self.moto_server = None
    
    def setup_clients(self) -> None:
        """Set up environment for mock AWS clients."""
        os.environ["MOTO_ALLOW_NONEXISTENT_REGION"] = "true"
        os.environ["AWS_DEFAULT_REGION"] = DEFAULT_REGION
        os.environ["AWS_ACCESS_KEY_ID"] = "mock"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
        os.environ["AWS_ENDPOINT_URL"] = self.endpoint_url
    
    def get_user_data_script(self, resources: Dict[str, Any]) -> str:
        """Get user data script for EC2 instances in mock mode."""
        return f"""#!/bin/bash
echo "Starting RAG Worker in aws-mock mode..."
export AWS_DEFAULT_REGION={DEFAULT_REGION}
export AWS_ACCESS_KEY_ID=mock
export AWS_SECRET_ACCESS_KEY=mock
export AWS_ENDPOINT_URL=http://host.docker.internal:5000
export S3_BUCKET_NAME={resources['s3_bucket_name']}
export SQS_QUEUE_URL={resources['sqs_queue_url']}
export QUEUE_TYPE=aws-mock
export DISABLE_DUPLICATE_LOADING=true

echo "Starting Docker container..."
docker run -d \\
  --name rag-worker \\
  --restart unless-stopped \\
  -e AWS_DEFAULT_REGION={DEFAULT_REGION} \\
  -e AWS_ACCESS_KEY_ID=mock \\
  -e AWS_SECRET_ACCESS_KEY=mock \\
  -e AWS_ENDPOINT_URL={self.endpoint_url} \\
  -e S3_BUCKET_NAME={resources['s3_bucket_name']} \\
  -e SQS_QUEUE_URL={resources['sqs_queue_url']} \\
  -e QUEUE_TYPE=aws-mock \\
  -e DISABLE_DUPLICATE_LOADING=true \\
  -p 8000:8000 \\
  {resources['ecr_repository_uri']}:latest
"""
    def setup_asg(self, resources):
        """Setup mock auto scaling group with Docker container integration."""
        asg_client = get_asg_client()
        ec2_client = get_ec2_client()
        
        # Get or create VPC and subnet
        vpc_id = resources.get('vpc_id')
        if not vpc_id:
            # Create VPC
            vpc_response = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
            vpc_id = vpc_response['Vpc']['VpcId']
            resources['vpc_id'] = vpc_id
            
            # Create private subnet
            subnet_response = ec2_client.create_subnet(
                VpcId=vpc_id,
                CidrBlock="10.0.2.0/24",
                AvailabilityZone="us-east-1a"
            )
            subnet_id = subnet_response['Subnet']['SubnetId']
            resources['private_subnet_id'] = subnet_id
            
            logger.info(f"Created mock VPC {vpc_id} with private subnet {subnet_id}")
        
        # Create launch configuration
        asg_client.create_launch_configuration(
            LaunchConfigurationName="rag-worker-config",
            ImageId="ami-mock",
            InstanceType="t2.large"
        )
        
        # Create auto scaling group
        asg_client.create_auto_scaling_group(
            AutoScalingGroupName="rag-worker-asg",
            LaunchConfigurationName="rag-worker-config",
            MinSize=1,
            MaxSize=5,
            DesiredCapacity=2,  # Start with 2 worker containers
            VPCZoneIdentifier=resources['private_subnet_id']
        )
        
        # Create scaling policies
        asg_client.put_scaling_policy(
            AutoScalingGroupName="rag-worker-asg",
            PolicyName="scale-out-policy",
            PolicyType="SimpleScaling",
            AdjustmentType="ChangeInCapacity",
            ScalingAdjustment=1
        )
        
        asg_client.put_scaling_policy(
            AutoScalingGroupName="rag-worker-asg",
            PolicyName="scale-in-policy",
            PolicyType="SimpleScaling",
            AdjustmentType="ChangeInCapacity",
            ScalingAdjustment=-1
        )
        
        logger.info("Created auto scaling group and policies")
        resources['asg_name'] = "rag-worker-asg"
        
        # Start container scaler
        from files_api.aws.container_scaler import ContainerScaler
        container_scaler = ContainerScaler(asg_name="rag-worker-asg")
        container_scaler.start()
        resources['container_scaler'] = container_scaler
        
        return resources

class ProductionAWSStrategy(DeploymentStrategy):
    """Strategy for cloud deployment mode."""
    
    def setup_clients(self) -> None:
        """Set up environment for cloud AWS clients."""
        # Standard boto3 will use environment credentials
        pass
    
    def get_user_data_script(self, resources: Dict[str, Any]) -> str:
        """Get user data script for EC2 instances in aws-prod mode."""
        return f"""#!/bin/bash
echo "Starting RAG Worker in aws-prod mode..."

# Get instance region from metadata service
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
export AWS_DEFAULT_REGION=$REGION

# Install CloudWatch agent
yum install -y amazon-cloudwatch-agent
amazon-cloudwatch-agent-ctl -a start

# Pull ECR image
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin {resources['ecr_repository_uri'].split('/')[0]}
docker pull {resources['ecr_repository_uri']}:latest

# Start container
docker run -d \\
  --name rag-worker \\
  --restart unless-stopped \\
  --gpus all \\
  -e AWS_DEFAULT_REGION=$REGION \\
  -e S3_BUCKET_NAME={resources['s3_bucket_name']} \\
  -e SQS_QUEUE_URL={resources['sqs_queue_url']} \\
  -e QUEUE_TYPE=aws-prod \\
  -e DISABLE_DUPLICATE_LOADING=true \\
  -p 8000:8000 \\
  {resources['ecr_repository_uri']}:latest
"""

# Decorator for timing and logging operations
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

def get_ami_id(mode: str, region: str) -> str:
    """Select an appropriate AMI ID based on deployment mode and region."""
    if mode == "aws-mock":
        # For moto mock testing - use one of the valid AMIs we found
        if region == "us-west-2":
            return "ami-0bae969d1b995ad03"  # Amazon Linux 2 AMI in us-west-2
        else:
            return "ami-12345678"  # Fallback mock AMI
    else:
        # Production AMIs by region with GPU support
        gpu_amis = {
            "us-west-2": "ami-0a1b648e2a346a535",  # Deep Learning AMI with GPU support
            "us-east-1": "ami-0b0af3577fe5e3532",
            # Add more regions as needed
        }
        return gpu_amis.get(region, "ami-0a1b648e2a346a535")  # Default to us-west-2 if region not found

# Builder pattern for AWS environment
class AWSEnvironmentBuilder:
    """Builder for AWS deployment environment.
    
    Implements the Builder pattern to provide a structured way to create
    AWS resources for the application deployment.
    """

    def export_environment(self) -> 'AWSEnvironmentBuilder':
        """Export configuration to environment variables instead of file."""
        # Set environment variables for docker-compose and other tools
        os.environ['DEPLOYMENT_MODE'] = self.mode
        os.environ['AWS_DEFAULT_REGION'] = self.region
        os.environ['S3_BUCKET_NAME'] = self.resources.get('s3_bucket_name', S3_BUCKET_NAME)
        os.environ['SQS_QUEUE_URL'] = self.resources.get('sqs_queue_url', '')
        os.environ['SQS_QUEUE_NAME'] = SQS_QUEUE_NAME
        os.environ['ASG_NAME'] = self.resources.get('asg_name', '')
        
        if self.mode == "aws-mock":
            os.environ['AWS_ENDPOINT_URL'] = self.strategy.endpoint_url
            os.environ['AWS_ACCESS_KEY_ID'] = 'mock'
            os.environ['AWS_SECRET_ACCESS_KEY'] = 'mock'
        
        # Additional configuration
        os.environ['MODEL_MEMORY_LIMIT'] = settings.model_memory_limit
        os.environ['DISABLE_DUPLICATE_LOADING'] = str(settings.disable_duplicate_loading).lower()
        os.environ['LOG_LEVEL'] = settings.log_level
        os.environ['STORAGE_DIR'] = settings.storage_dir
        
        # Decoupling-specific variables
        os.environ['SQS_RESULT_QUEUE_URL'] = self.resources.get('sqs_queue_url', '').replace('task-queue', 'result-queue')
        os.environ['DB_ACCESS_ENABLED'] = 'false'
        
        logger.info("Exported configuration to environment variables")
        return self
    
    def __init__(self, mode: str = "aws-mock", region: str = DEFAULT_REGION):
        """Initialize the builder.
        
        Args:
            mode: Deployment mode ('aws-mock' or 'aws-prod')
            region: AWS region
        """
        self.region = region
        self.mode = mode
        self.resources = {}
        
        # Create appropriate strategy based on mode
        if mode == "aws-mock":
            self.strategy = MockAWSStrategy()
        else:
            self.strategy = ProductionAWSStrategy()
        
        # Initialize environment
        self.strategy.setup_clients()
        
        # Start moto server if needed
        if mode == "aws-mock":
            self.strategy.start_moto_server()
    
    @log_operation("Setting up network resources")
    def build_network(self) -> 'AWSEnvironmentBuilder':
        """Build VPC and network components."""
        # Skip network setup in aws-mock mode
        # if self.mode == "aws-mock":
        #     logger.info("Skipping detailed network setup in aws-mock mode")
        #     self.resources['vpc_id'] = "mock-vpc"
        #     self.resources['public_subnet_id'] = "mock-public-subnet"
        #     self.resources['private_subnet_id'] = "mock-private-subnet"
        #     return self
        
        """Build VPC and network components."""
        ec2_client = get_ec2_client()  # Assumes this returns a Moto-configured client in aws-mock mode
        
        # Create VPC
        vpc_response = ec2_client.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc_response['Vpc']['VpcId']
        
        # Create public subnet
        public_subnet = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock="10.0.1.0/24",
            AvailabilityZone=f"{self.region}a"
        )
        public_subnet_id = public_subnet['Subnet']['SubnetId']
        
        # Create private subnet
        private_subnet = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock="10.0.2.0/24",
            AvailabilityZone=f"{self.region}b"
        )
        private_subnet_id = private_subnet['Subnet']['SubnetId']
        
        # Create and attach Internet Gateway
        igw_response = ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        ec2_client.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        
        # Create route tables and routes
        public_route_table = ec2_client.create_route_table(VpcId=vpc_id)
        public_route_table_id = public_route_table['RouteTable']['RouteTableId']
        
        private_route_table = ec2_client.create_route_table(VpcId=vpc_id)
        private_route_table_id = private_route_table['RouteTable']['RouteTableId']
        
        ec2_client.create_route(
            RouteTableId=public_route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        
        # Associate route tables
        ec2_client.associate_route_table(RouteTableId=public_route_table_id, SubnetId=public_subnet_id)
        ec2_client.associate_route_table(RouteTableId=private_route_table_id, SubnetId=private_subnet_id)
        
        # Store resources
        self.resources['vpc_id'] = vpc_id
        self.resources['public_subnet_id'] = public_subnet_id
        self.resources['private_subnet_id'] = private_subnet_id
        
        logger.info(f"Network setup complete. VPC ID: {vpc_id}")
        return self
    
    @log_operation("Creating S3 bucket")
    def build_s3_bucket(self, bucket_name: str = S3_BUCKET_NAME) -> 'AWSEnvironmentBuilder':
        """Build S3 bucket for PDF storage."""
        if create_s3_bucket(bucket_name):
            self.resources['s3_bucket_name'] = bucket_name
        return self
    
    @log_operation("Creating SQS queue")
    def build_sqs_queue(self, queue_name: str = SQS_QUEUE_NAME) -> 'AWSEnvironmentBuilder':
        """Build SQS queue for task processing."""
        queue_url = create_sqs_queue(queue_name)
        if queue_url:
            # Extract just the queue name from the URL
            # Original format: http://localhost:5000/123456789012/rag-task-queue
            queue_name = queue_url.split('/')[-1]
            
            # Create a normalized URL that will work in Docker network
            if self.mode == "aws-mock":
                normalized_url = f"http://localhost:5000/{queue_name}"
            else:
                # For production, use the original URL
                normalized_url = queue_url
                
            # Store the normalized URL
            self.resources['sqs_queue_url'] = normalized_url
            logger.info(f"Using normalized SQS queue URL: {normalized_url}")
            
            # Get queue ARN (still using original URL for this API call)
            queue_arn = get_queue_arn(queue_url)
            if queue_arn:
                self.resources['sqs_queue_arn'] = queue_arn
        
        return self
    
    @log_operation("Creating IAM role")
    def build_iam_role(self) -> 'AWSEnvironmentBuilder':
        """Build IAM role for EC2 instances."""
        # Skip detailed IAM setup in aws-mock mode
        if self.mode == "aws-mock":
            logger.info("Using simplified IAM setup for aws-mock mode")
            self.resources['iam_role_name'] = IAM_ROLE_NAME
            self.resources['iam_instance_profile'] = IAM_INSTANCE_PROFILE
            return self
        
        iam_client = get_iam_client()
        
        # Create trust policy
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        # Create role
        try:
            role_response = iam_client.create_role(
                RoleName=IAM_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy)
            )
            
            # Attach policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
                "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
                "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
            ]
            
            for policy in policies:
                iam_client.attach_role_policy(
                    RoleName=IAM_ROLE_NAME,
                    PolicyArn=policy
                )
            
            # Create instance profile
            iam_client.create_instance_profile(
                InstanceProfileName=IAM_INSTANCE_PROFILE
            )
            
            # Add role to instance profile
            iam_client.add_role_to_instance_profile(
                InstanceProfileName=IAM_INSTANCE_PROFILE,
                RoleName=IAM_ROLE_NAME
            )
            
            # Store resources
            self.resources['iam_role_name'] = IAM_ROLE_NAME
            self.resources['iam_instance_profile'] = IAM_INSTANCE_PROFILE
            
        except iam_client.exceptions.EntityAlreadyExistsException:
            logger.info(f"IAM role {IAM_ROLE_NAME} already exists")
            self.resources['iam_role_name'] = IAM_ROLE_NAME
            self.resources['iam_instance_profile'] = IAM_INSTANCE_PROFILE
        
        return self
    
    @log_operation("Creating security group")
    def build_security_group(self) -> 'AWSEnvironmentBuilder':
        """Build security group for EC2 instances."""
        # Skip detailed setup in aws-mock mode
        if self.mode == "aws-mock":
            logger.info("Using simplified security group for aws-mock mode")
            self.resources['security_group_id'] = "mock-sg"
            return self
        
        ec2_client = get_ec2_client()
        
        # Create security group
        security_group = ec2_client.create_security_group(
            GroupName="rag-worker-sg",
            Description="Security group for RAG worker instances",
            VpcId=self.resources['vpc_id']
        )
        
        security_group_id = security_group['GroupId']
        
        # Add inbound rules
        ec2_client.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 22,
                    'ToPort': 22,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 8000,
                    'ToPort': 8000,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }
            ]
        )
        
        # Store resource
        self.resources['security_group_id'] = security_group_id
        
        return self
    
    @log_operation("Creating ECR repository")
    def build_ecr_repository(self) -> 'AWSEnvironmentBuilder':
        """Build ECR repository for Docker images."""
        # Use mock for aws-mock mode
        if self.mode == "aws-mock":
            logger.info("Using mock ECR repository URI for aws-mock mode")
            self.resources['ecr_repository_uri'] = f"{DEFAULT_REGION}.dkr.ecr.mock/{ECR_REPO_NAME}"
            return self
        
        ecr_client = get_ecr_client()
        
        try:
            # Create repository
            response = ecr_client.create_repository(
                repositoryName=ECR_REPO_NAME,
                imageScanningConfiguration={
                    'scanOnPush': True
                }
            )
            
            repository_uri = response['repository']['repositoryUri']
            self.resources['ecr_repository_uri'] = repository_uri
            
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            # Get existing repository URI
            response = ecr_client.describe_repositories(
                repositoryNames=[ECR_REPO_NAME]
            )
            
            repository_uri = response['repositories'][0]['repositoryUri']
            self.resources['ecr_repository_uri'] = repository_uri
            logger.info(f"Using existing ECR repository: {repository_uri}")
        
        return self
    
    @log_operation("Creating launch template")
    def build_launch_template(self) -> 'AWSEnvironmentBuilder':
        """Build launch template for EC2 instances."""
        ec2_client = get_ec2_client()
        
        # Get user data script from the strategy
        user_data = self.strategy.get_user_data_script(self.resources)
        
        # Get appropriate AMI ID
        ami_id = get_ami_id(self.mode, self.region)
        
        # Select instance type based on deployment mode
        instance_type = 'g4dn.xlarge' if self.mode == "aws-prod" else 't2.micro'
        
        logger.info(f"Creating launch template with AMI: {ami_id} and instance type: {instance_type}")
        
        try:
            # Create launch template
            response = ec2_client.create_launch_template(
                LaunchTemplateName="rag-worker-template",
                VersionDescription='Initial version',
                LaunchTemplateData={
                    'ImageId': ami_id,
                    'InstanceType': instance_type,
                    'KeyName': 'rag-worker-key',
                    'SecurityGroupIds': [self.resources['security_group_id']],
                    'IamInstanceProfile': {
                        'Name': self.resources['iam_instance_profile']
                    },
                    'UserData': user_data,
                    'BlockDeviceMappings': [
                        {
                            'DeviceName': '/dev/xvda' if self.mode == "aws-mock" else '/dev/sda1',
                            'Ebs': {
                                'VolumeSize': 100,  # GB
                                'VolumeType': 'gp3',
                                'DeleteOnTermination': True
                            }
                        }
                    ]
                }
            )
            
            template_id = response['LaunchTemplate']['LaunchTemplateId']
            self.resources['launch_template_id'] = template_id
            logger.info(f"Created launch template with ID: {template_id}")
            
        except ec2_client.exceptions.ClientError as e:
            if 'InvalidLaunchTemplateName.AlreadyExistsException' in str(e):
                logger.info("Launch template already exists")
                # Get existing template ID
                response = ec2_client.describe_launch_templates(
                    LaunchTemplateNames=["rag-worker-template"]
                )
                template_id = response['LaunchTemplates'][0]['LaunchTemplateId']
                self.resources['launch_template_id'] = template_id
                logger.info(f"Using existing launch template with ID: {template_id}")
            else:
                logger.error(f"Error creating launch template: {str(e)}")
                raise
        
        return self
    
    @log_operation("Creating auto scaling group")
    def build_auto_scaling_group(self) -> 'AWSEnvironmentBuilder':
        """Build auto scaling group for worker instances."""
        # Skip for aws-mock mode
        # if self.mode == "aws-mock":
        #     logger.info("Skipping ASG for aws-mock mode")
        #     self.resources['auto_scaling_group_name'] = "mock-asg"
        #     return self
        
        asg_client = get_asg_client()
        
        try:
            # Create ASG
            asg_client.create_auto_scaling_group(
                AutoScalingGroupName="rag-worker-asg",
                LaunchTemplate={
                    'LaunchTemplateId': self.resources['launch_template_id'],
                    'Version': '$Latest'
                },
                MinSize=1,
                MaxSize=5,
                DesiredCapacity=1,
                VPCZoneIdentifier=f"{self.resources['private_subnet_id']}",
                HealthCheckType='EC2',
                HealthCheckGracePeriod=300,
                Tags=[
                    {
                        'Key': 'Name',
                        'Value': 'RAG Worker',
                        'PropagateAtLaunch': True
                    }
                ]
            )
            
            self.resources['auto_scaling_group_name'] = "rag-worker-asg"
            
        except asg_client.exceptions.AlreadyExistsFault:
            logger.info("Auto scaling group already exists")
            self.resources['auto_scaling_group_name'] = "rag-worker-asg"
    
        return self
    
    @log_operation("Creating CloudWatch alarms")
    def build_cloudwatch_alarms(self) -> 'AWSEnvironmentBuilder':
        """Build CloudWatch alarms for monitoring."""
        # Skip detailed setup for aws-mock mode
        # if self.mode == "aws-mock":
        #     logger.info("Skipping CloudWatch alarms for aws-mock mode")
        #     return self
        
        cloudwatch_client = get_cloudwatch_client()
        
        try:
            # Create alarm for queue length
            cloudwatch_client.put_metric_alarm(
                AlarmName='RAGQueueHighAlarm',
                ComparisonOperator='GreaterThanThreshold',
                EvaluationPeriods=1,
                MetricName='ApproximateNumberOfMessagesVisible',
                Namespace='AWS/SQS',
                Period=60,
                Statistic='Average',
                Threshold=100,
                ActionsEnabled=True,
                AlarmDescription='Alarm when queue depth exceeds 100 messages',
                Dimensions=[
                    {
                        'Name': 'QueueName',
                        'Value': SQS_QUEUE_NAME
                    }
                ]
            )
            
            # Worker health alarm
            cloudwatch_client.put_metric_alarm(
                AlarmName='RAGWorkerHealthAlarm',
                ComparisonOperator='LessThanThreshold',
                EvaluationPeriods=1,
                MetricName='WorkerHealthy',
                Namespace='FilesAPI/Worker',
                Period=60,
                Statistic='Average',
                Threshold=1,
                ActionsEnabled=True,
                AlarmDescription='Alarm when worker is not healthy',
            )
            
        except cloudwatch_client.exceptions.ResourceNotFoundException:
            logger.warning("CloudWatch alarm resource not found - this is expected in some moto implementations")
        
        return self
    
    # Add the new build method here, after all individual build methods
    def build(self) -> 'AWSEnvironmentBuilder':
        """Build all AWS resources."""
        try:
            self.build_network()
            self.build_s3_bucket()
            self.build_sqs_queue()
            
            if self.mode == "aws-mock":
                # Initialize ASG and container scaler for mock mode
                self.strategy.setup_asg(self.resources)
            else:
                # Normal AWS deployment
                self.build_iam_role()
                self.build_security_group()
                self.build_ecr_repository()
                self.build_launch_template()
                self.build_auto_scaling_group()
            
            self.build_cloudwatch_alarms()
            
            # Export to environment variables instead of file
            self.export_environment()
            
            # Optionally create env file if requested
            if os.environ.get('CREATE_ENV_FILE', 'false').lower() == 'true':
                self.create_env_file()
            
            return self
        except Exception as e:
            logger.error(f"Error building AWS environment: {e}")
            raise
    
    def create_env_file(self, path: str = ".env.aws") -> 'AWSEnvironmentBuilder':
        """Create environment file with resource information."""
        # Create content
        env_content = f"""# AWS Resource Configuration - Generated on {time.strftime('%Y-%m-%d %H:%M:%S')}
    AWS_DEFAULT_REGION={self.region}
    AWS_ACCESS_KEY_ID={"mock" if self.mode == "aws-mock" else ""}
    AWS_SECRET_ACCESS_KEY={"mock" if self.mode == "aws-mock" else ""}
    """
        
        # Add endpoint URL for aws-mock mode
        if self.mode == "aws-mock":
            env_content += f"AWS_ENDPOINT_URL={self.strategy.endpoint_url}\n"
        
        # Add resource info
        env_content += f"S3_BUCKET_NAME={self.resources.get('s3_bucket_name', '')}\n"
        env_content += f"SQS_QUEUE_URL={self.resources.get('sqs_queue_url', '')}\n"
        env_content += f"QUEUE_TYPE={self.mode}\n"
        env_content += "DISABLE_DUPLICATE_LOADING=true\n"
        
        # Add decoupling-specific variables
        env_content += f"SQS_RESULT_QUEUE_URL={self.resources.get('sqs_queue_url', '').replace('task-queue', 'result-queue')}\n"
        env_content += "DB_ACCESS_ENABLED=false\n"  # Disable direct DB access in containers
        env_content += "STORAGE_DIR=storage\n"      # For local-dev fallback
        env_content += "LOG_LEVEL=INFO\n"           # Set logging level
        
        # Write to file
        with open(path, "w") as f:
            f.write(env_content)
        
        logger.info(f"Created environment file at {path}")
        return self
    
    def get_resources(self) -> Dict[str, Any]:
        """Get the created resources."""
        return self.resources
    
    def cleanup(self) -> None:
        """Clean up all created resources."""
        logger.info("Cleaning up AWS resources...")
        
        # Clean up in reverse order
        if self.mode != "aws-mock":
            self._cleanup_cloudwatch_alarms()
            self._cleanup_auto_scaling_group()
            self._cleanup_launch_template()
            self._cleanup_security_group()
            self._cleanup_iam_role()
            self._cleanup_network()
        
        # These always need cleanup
        self._cleanup_sqs_queue()
        self._cleanup_s3_bucket()
        
        # Stop moto server if running
        self.strategy.stop_moto_server()
        
        logger.info("Cleanup complete")
    
    def _cleanup_cloudwatch_alarms(self) -> None:
        """Clean up CloudWatch alarms."""
        try:
            cloudwatch_client = get_cloudwatch_client()
            cloudwatch_client.delete_alarms(
                AlarmNames=['RAGQueueHighAlarm', 'RAGWorkerHealthAlarm']
            )
            logger.info("Deleted CloudWatch alarms")
        except Exception as e:
            logger.warning(f"Error cleaning up CloudWatch alarms: {str(e)}")
    
    def _cleanup_auto_scaling_group(self) -> None:
        """Clean up auto scaling group."""
        if 'auto_scaling_group_name' not in self.resources:
            return
        
        try:
            asg_client = get_asg_client()
            asg_client.delete_auto_scaling_group(
                AutoScalingGroupName=self.resources['auto_scaling_group_name'],
                ForceDelete=True
            )
            logger.info("Deleted auto scaling group")
        except Exception as e:
            logger.warning(f"Error cleaning up auto scaling group: {str(e)}")
    
    def _cleanup_launch_template(self) -> None:
        """Clean up launch template."""
        if 'launch_template_id' not in self.resources:
            return
        
        try:
            ec2_client = get_ec2_client()
            ec2_client.delete_launch_template(
                LaunchTemplateId=self.resources['launch_template_id']
            )
            logger.info("Deleted launch template")
        except Exception as e:
            logger.warning(f"Error cleaning up launch template: {str(e)}")
    
    def _cleanup_security_group(self) -> None:
        """Clean up security group."""
        if 'security_group_id' not in self.resources:
            return
        
        try:
            ec2_client = get_ec2_client()
            ec2_client.delete_security_group(
                GroupId=self.resources['security_group_id']
            )
            logger.info("Deleted security group")
        except Exception as e:
            logger.warning(f"Error cleaning up security group: {str(e)}")
    
    def _cleanup_iam_role(self) -> None:
        """Clean up IAM role."""
        if 'iam_role_name' not in self.resources:
            return
        
        try:
            iam_client = get_iam_client()
            
            # Detach policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
                "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
                "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
            ]
            
            for policy in policies:
                try:
                    iam_client.detach_role_policy(
                        RoleName=self.resources['iam_role_name'],
                        PolicyArn=policy
                    )
                except Exception:
                    pass
            
            # Remove role from instance profile
            try:
                iam_client.remove_role_from_instance_profile(
                    InstanceProfileName=self.resources['iam_instance_profile'],
                    RoleName=self.resources['iam_role_name']
                )
            except Exception:
                pass
            
            # Delete instance profile
            try:
                iam_client.delete_instance_profile(
                    InstanceProfileName=self.resources['iam_instance_profile']
                )
            except Exception:
                pass
            
            # Delete role
            iam_client.delete_role(
                RoleName=self.resources['iam_role_name']
            )
            
            logger.info("Deleted IAM role and instance profile")
        except Exception as e:
            logger.warning(f"Error cleaning up IAM role: {str(e)}")
    
    def _cleanup_network(self) -> None:
        """Clean up network resources."""
        if 'vpc_id' not in self.resources:
            return
        
        ec2_client = get_ec2_client()
        
        # Delete subnets
        if 'public_subnet_id' in self.resources:
            try:
                ec2_client.delete_subnet(
                    SubnetId=self.resources['public_subnet_id']
                )
                logger.info("Deleted public subnet")
            except Exception as e:
                logger.warning(f"Error deleting public subnet: {str(e)}")
        
        if 'private_subnet_id' in self.resources:
            try:
                ec2_client.delete_subnet(
                    SubnetId=self.resources['private_subnet_id']
                )
                logger.info("Deleted private subnet")
            except Exception as e:
                logger.warning(f"Error deleting private subnet: {str(e)}")
        
        # Delete VPC
        try:
            ec2_client.delete_vpc(
                VpcId=self.resources['vpc_id']
            )
            logger.info("Deleted VPC")
        except Exception as e:
            logger.warning(f"Error deleting VPC: {str(e)}")
    
    def _cleanup_sqs_queue(self) -> None:
        """Clean up SQS queue."""
        if 'sqs_queue_url' not in self.resources:
            return
        
        try:
            sqs_client = get_sqs_client()
            sqs_client.delete_queue(
                QueueUrl=self.resources['sqs_queue_url']
            )
            logger.info("Deleted SQS queue")
        except Exception as e:
            logger.warning(f"Error deleting SQS queue: {str(e)}")
    
    def _cleanup_s3_bucket(self) -> None:
        """Clean up S3 bucket."""
        if 's3_bucket_name' not in self.resources:
            return
        
        try:
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
                logger.info("Emptied S3 bucket")
            except Exception as e:
                logger.warning(f"Error emptying S3 bucket: {str(e)}")
            
            # Delete bucket
            s3_client.delete_bucket(
                Bucket=self.resources['s3_bucket_name']
            )
            logger.info("Deleted S3 bucket")
        except Exception as e:
            logger.warning(f"Error deleting S3 bucket: {str(e)}")

@contextmanager
def aws_environment(mode: str = "aws-mock", cleanup: bool = True):
    """Context manager for AWS environment.
    
    Creates a complete AWS environment for the application and cleans up
    when done.
    
    Args:
        mode: Deployment mode ('aws-mock' or 'aws-prod')
        cleanup: Whether to clean up resources when done
    
    Yields:
        Dictionary of created resources
    """
    builder = AWSEnvironmentBuilder(mode=mode)
    try:
        # Build resources
        builder.build_network() \
               .build_s3_bucket() \
               .build_sqs_queue() \
               .build_iam_role() \
               .build_security_group() \
               .build_ecr_repository() \
               .build_launch_template() \
               .build_auto_scaling_group() \
               .build_cloudwatch_alarms() \
               .build() \
               .create_env_file()
        
        yield builder.get_resources()
    finally:
        if cleanup:
            builder.cleanup()

def deploy_environment(mode: str = "aws-mock", no_cleanup: bool = False, keep_running: bool = False):
    """Deploy AWS environment and optionally keep it running.
    
    Args:
        mode: Deployment mode ('aws-mock' or 'aws-prod')
        no_cleanup: If True, don't clean up resources
        keep_running: If True, keep resources running until interrupted
    """
    with aws_environment(mode=mode, cleanup=not no_cleanup) as resources:
        logger.info("AWS environment deployed successfully")
        
        if mode == "aws-mock":
            # Print instructions for aws-mock mode
            logger.info("\nLocal mock AWS environment ready!")
            logger.info("You can use the following environment variables in your application:")
            logger.info(f"  AWS_DEFAULT_REGION={DEFAULT_REGION}")
            logger.info(f"  AWS_ENDPOINT_URL=http://localhost:{MOTO_SERVER_PORT}")
            logger.info(f"  S3_BUCKET_NAME={resources.get('s3_bucket_name')}")
            logger.info(f"  SQS_QUEUE_URL={resources.get('sqs_queue_url')}")
            logger.info("\nThese variables have been saved to .env.aws")
        else:
            # Print instructions for aws-prod mode
            logger.info("\nCloud AWS environment ready!")
            logger.info(f"Auto Scaling Group: {resources.get('auto_scaling_group_name')}")
            logger.info(f"S3 Bucket: {resources.get('s3_bucket_name')}")
            logger.info(f"SQS Queue: {resources.get('sqs_queue_url')}")
            logger.info("\nThese resources will be accessible to your application")
        
        if keep_running:
            logger.info("\nKeeping environment running. Press Ctrl+C to terminate and clean up resources.")
            try:
                # Keep running until interrupted
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Terminating AWS environment")

def main():
    """Command-line interface for AWS deployment."""
    parser = argparse.ArgumentParser(description='Deploy AWS environment for Files API application')
    parser.add_argument('--mode', choices=['aws-mock', 'aws-prod'], default='aws-mock',
                        help='Deployment mode (default: aws-mock)')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Skip cleanup of AWS resources')
    parser.add_argument('--keep-running', action='store_true',
                        help='Keep the environment running until Ctrl+C')
    parser.add_argument('--create-env-file', action='store_true',
                        help='Create .env.aws file (optional)')
    args = parser.parse_args()
    
    # Set environment variable if --create-env-file is specified
    if args.create_env_file:
        os.environ['CREATE_ENV_FILE'] = 'true'
    
    deploy_environment(
        mode=args.mode,
        no_cleanup=args.no_cleanup,
        keep_running=args.keep_running
    )

if __name__ == "__main__":
    main()