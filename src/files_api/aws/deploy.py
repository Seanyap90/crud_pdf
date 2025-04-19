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

# Constants
DEFAULT_REGION = "us-east-1"
MOTO_SERVER_PORT = 5000
S3_BUCKET_NAME = "rag-pdf-storage"
SQS_QUEUE_NAME = "rag-task-queue"
ECR_REPO_NAME = "rag-worker"
IAM_ROLE_NAME = "rag-worker-role"
IAM_INSTANCE_PROFILE = "rag-worker-profile"

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

class LocalMockStrategy(DeploymentStrategy):
    """Strategy for local-mock deployment mode."""
    
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
    
    def start_moto_server(self) -> None:
        """Start moto server in background thread."""
        logger.info(f"Starting moto server on port {MOTO_SERVER_PORT}...")
        
        def run_server():
            try:
                subprocess.call([
                    "python", "-m", "moto.server", 
                    "-p", str(MOTO_SERVER_PORT)
                ])
            except Exception as e:
                logger.error(f"Error in moto server: {str(e)}")
        
        self.moto_server = threading.Thread(target=run_server)
        self.moto_server.daemon = True
        self.moto_server.start()
        
        # Wait for server to start
        max_tries = 10
        for i in range(max_tries):
            try:
                import requests
                response = requests.get(f"{self.endpoint_url}/moto-api/")
                if response.status_code == 200:
                    logger.info("Moto server started successfully")
                    return
            except Exception:
                pass
            
            logger.info(f"Waiting for moto server ({i+1}/{max_tries})...")
            time.sleep(2)
    
    def stop_moto_server(self) -> None:
        """Stop moto server if running."""
        if self.moto_server and self.moto_server.is_alive():
            logger.info("Stopping moto server...")
            # There's no clean way to stop the thread, but we signal this
            # when the main script exits the daemon thread will terminate
    
    def get_user_data_script(self, resources: Dict[str, Any]) -> str:
        """Get user data script for EC2 instances in mock mode."""
        return f"""#!/bin/bash
echo "Starting RAG Worker in local-mock mode..."
export AWS_DEFAULT_REGION={DEFAULT_REGION}
export AWS_ACCESS_KEY_ID=mock
export AWS_SECRET_ACCESS_KEY=mock
export AWS_ENDPOINT_URL={self.endpoint_url}
export S3_BUCKET_NAME={resources['s3_bucket_name']}
export SQS_QUEUE_URL={resources['sqs_queue_url']}
export QUEUE_TYPE=local-mock
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
  -e QUEUE_TYPE=local-mock \\
  -e DISABLE_DUPLICATE_LOADING=true \\
  -p 8000:8000 \\
  {resources['ecr_repository_uri']}:latest
"""

class CloudStrategy(DeploymentStrategy):
    """Strategy for cloud deployment mode."""
    
    def setup_clients(self) -> None:
        """Set up environment for cloud AWS clients."""
        # Standard boto3 will use environment credentials
        pass
    
    def get_user_data_script(self, resources: Dict[str, Any]) -> str:
        """Get user data script for EC2 instances in cloud mode."""
        return f"""#!/bin/bash
echo "Starting RAG Worker in cloud mode..."

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
  -e QUEUE_TYPE=cloud \\
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

# Builder pattern for AWS environment
class AWSEnvironmentBuilder:
    """Builder for AWS deployment environment.
    
    Implements the Builder pattern to provide a structured way to create
    AWS resources for the application deployment.
    """
    
    def __init__(self, mode: str = "local-mock", region: str = DEFAULT_REGION):
        """Initialize the builder.
        
        Args:
            mode: Deployment mode ('local-mock' or 'cloud')
            region: AWS region
        """
        self.region = region
        self.mode = mode
        self.resources = {}
        
        # Create appropriate strategy based on mode
        if mode == "local-mock":
            self.strategy = LocalMockStrategy()
        else:
            self.strategy = CloudStrategy()
        
        # Initialize environment
        self.strategy.setup_clients()
        
        # Start moto server if needed
        if mode == "local-mock":
            self.strategy.start_moto_server()
    
    @log_operation("Setting up network resources")
    def build_network(self) -> 'AWSEnvironmentBuilder':
        """Build VPC and network components."""
        # Skip network setup in local-mock mode
        if self.mode == "local-mock":
            logger.info("Skipping detailed network setup in local-mock mode")
            self.resources['vpc_id'] = "mock-vpc"
            self.resources['public_subnet_id'] = "mock-public-subnet"
            self.resources['private_subnet_id'] = "mock-private-subnet"
            return self
        
        # Get EC2 client
        ec2_client = get_ec2_client()
        
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
        
        ec2_client.attach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id
        )
        
        # Create route tables and routes
        public_route_table = ec2_client.create_route_table(VpcId=vpc_id)
        public_route_table_id = public_route_table['RouteTable']['RouteTableId']
        
        private_route_table = ec2_client.create_route_table(VpcId=vpc_id)
        private_route_table_id = private_route_table['RouteTable']['RouteTableId']
        
        # Create public route
        ec2_client.create_route(
            RouteTableId=public_route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        
        # Associate route tables
        ec2_client.associate_route_table(
            RouteTableId=public_route_table_id,
            SubnetId=public_subnet_id
        )
        
        ec2_client.associate_route_table(
            RouteTableId=private_route_table_id,
            SubnetId=private_subnet_id
        )
        
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
            self.resources['sqs_queue_url'] = queue_url
            
            # Get queue ARN
            queue_arn = get_queue_arn(queue_url)
            if queue_arn:
                self.resources['sqs_queue_arn'] = queue_arn
        
        return self
    
    @log_operation("Creating IAM role")
    def build_iam_role(self) -> 'AWSEnvironmentBuilder':
        """Build IAM role for EC2 instances."""
        # Skip detailed IAM setup in local-mock mode
        if self.mode == "local-mock":
            logger.info("Using simplified IAM setup for local-mock mode")
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
        # Skip detailed setup in local-mock mode
        if self.mode == "local-mock":
            logger.info("Using simplified security group for local-mock mode")
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
        # Use mock for local-mock mode
        if self.mode == "local-mock":
            logger.info("Using mock ECR repository URI for local-mock mode")
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
        # Skip for local-mock mode
        if self.mode == "local-mock":
            logger.info("Skipping launch template for local-mock mode")
            self.resources['launch_template_id'] = "mock-lt"
            return self
        
        ec2_client = get_ec2_client()
        
        # Get user data script from the strategy
        user_data = self.strategy.get_user_data_script(self.resources)
        
        try:
            # Create launch template
            response = ec2_client.create_launch_template(
                LaunchTemplateName="rag-worker-template",
                VersionDescription='Initial version',
                LaunchTemplateData={
                    'ImageId': 'ami-12345678',  # This should be a real AMI in cloud mode
                    'InstanceType': 'g4dn.xlarge',  # GPU instance
                    'KeyName': 'rag-worker-key',
                    'SecurityGroupIds': [self.resources['security_group_id']],
                    'IamInstanceProfile': {
                        'Name': self.resources['iam_instance_profile']
                    },
                    'UserData': user_data,
                    'BlockDeviceMappings': [
                        {
                            'DeviceName': '/dev/sda1',
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
            
        except ec2_client.exceptions.ClientError as e:
            if 'InvalidLaunchTemplateName.AlreadyExistsException' in str(e):
                logger.info("Launch template already exists")
                # Get existing template ID
                response = ec2_client.describe_launch_templates(
                    LaunchTemplateNames=["rag-worker-template"]
                )
                template_id = response['LaunchTemplates'][0]['LaunchTemplateId']
                self.resources['launch_template_id'] = template_id
            else:
                raise
        
        return self
    
    @log_operation("Creating auto scaling group")
    def build_auto_scaling_group(self) -> 'AWSEnvironmentBuilder':
        """Build auto scaling group for worker instances."""
        # Skip for local-mock mode
        if self.mode == "local-mock":
            logger.info("Skipping ASG for local-mock mode")
            self.resources['auto_scaling_group_name'] = "mock-asg"
            return self
        
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
        # Skip detailed setup for local-mock mode
        if self.mode == "local-mock":
            logger.info("Skipping CloudWatch alarms for local-mock mode")
            return self
        
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
    
    def create_env_file(self, path: str = ".env.aws") -> 'AWSEnvironmentBuilder':
        """Create environment file with resource information."""
        # Create content
        env_content = f"""# AWS Resource Configuration - Generated on {time.strftime('%Y-%m-%d %H:%M:%S')}
AWS_DEFAULT_REGION={self.region}
AWS_ACCESS_KEY_ID={"mock" if self.mode == "local-mock" else ""}
AWS_SECRET_ACCESS_KEY={"mock" if self.mode == "local-mock" else ""}
"""
        
        # Add endpoint URL for local-mock mode
        if self.mode == "local-mock":
            env_content += f"AWS_ENDPOINT_URL={self.strategy.endpoint_url}\n"
        
        # Add resource info
        env_content += f"S3_BUCKET_NAME={self.resources.get('s3_bucket_name', '')}\n"
        env_content += f"SQS_QUEUE_URL={self.resources.get('sqs_queue_url', '')}\n"
        env_content += f"QUEUE_TYPE={self.mode}\n"
        env_content += "DISABLE_DUPLICATE_LOADING=true\n"
        
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
        if self.mode != "local-mock":
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
def aws_environment(mode: str = "local-mock", cleanup: bool = True):
    """Context manager for AWS environment.
    
    Creates a complete AWS environment for the application and cleans up
    when done.
    
    Args:
        mode: Deployment mode ('local-mock' or 'cloud')
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
               .create_env_file()
        
        yield builder.get_resources()
    finally:
        if cleanup:
            builder.cleanup()

def deploy_environment(mode: str = "local-mock", no_cleanup: bool = False, keep_running: bool = False):
    """Deploy AWS environment and optionally keep it running.
    
    Args:
        mode: Deployment mode ('local-mock' or 'cloud')
        no_cleanup: If True, don't clean up resources
        keep_running: If True, keep resources running until interrupted
    """
    with aws_environment(mode=mode, cleanup=not no_cleanup) as resources:
        logger.info("AWS environment deployed successfully")
        
        if mode == "local-mock":
            # Print instructions for local-mock mode
            logger.info("\nLocal mock AWS environment ready!")
            logger.info("You can use the following environment variables in your application:")
            logger.info(f"  AWS_DEFAULT_REGION={DEFAULT_REGION}")
            logger.info(f"  AWS_ENDPOINT_URL=http://localhost:{MOTO_SERVER_PORT}")
            logger.info(f"  S3_BUCKET_NAME={resources.get('s3_bucket_name')}")
            logger.info(f"  SQS_QUEUE_URL={resources.get('sqs_queue_url')}")
            logger.info("\nThese variables have been saved to .env.aws")
        else:
            # Print instructions for cloud mode
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
    parser.add_argument('--mode', choices=['local-mock', 'cloud'], default='local-mock',
                        help='Deployment mode (default: local-mock)')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Skip cleanup of AWS resources')
    parser.add_argument('--keep-running', action='store_true',
                        help='Keep the environment running until Ctrl+C')
    args = parser.parse_args()
    
    deploy_environment(
        mode=args.mode,
        no_cleanup=args.no_cleanup,
        keep_running=args.keep_running
    )

if __name__ == "__main__":
    main()