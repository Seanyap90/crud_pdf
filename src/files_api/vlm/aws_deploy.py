# aws_deploy.py
"""
AWS Deployment script for VLM+RAG Worker
Handles AWS service setup using moto for local simulation
"""
import os
import boto3
import logging
import time
import json
import argparse
from threading import Thread
from contextlib import contextmanager
import subprocess

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_REGION = "us-east-1"
MOTO_SERVER_PORT = 5000
VPC_CIDR = "10.0.0.0/16"
PUBLIC_SUBNET_CIDR = "10.0.1.0/24"
PRIVATE_SUBNET_CIDR = "10.0.2.0/24"
S3_BUCKET_NAME = "rag-pdf-storage"
SQS_QUEUE_NAME = "rag-task-queue"
ECR_REPO_NAME = "rag-worker"
LAUNCH_TEMPLATE_NAME = "rag-worker-template"
ASG_NAME = "rag-worker-asg"
IAM_ROLE_NAME = "rag-worker-role"
IAM_INSTANCE_PROFILE = "rag-worker-profile"

class AWSDeployer:
    """Manages AWS resource setup using moto server for local simulation"""
    
    def __init__(self, region=DEFAULT_REGION, mode="local-mock"):
        """Initialize deployer with configuration"""
        self.region = region
        self.mode = mode
        self.moto_server = None
        self.resources = {}
        
        # Set environment variables for moto
        os.environ["MOTO_ALLOW_NONEXISTENT_REGION"] = "true"
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_ACCESS_KEY_ID"] = "mock"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
        
        # If in local-mock mode, set endpoint
        if mode == "local-mock":
            self.endpoint_url = f"http://localhost:{MOTO_SERVER_PORT}"
            os.environ["AWS_ENDPOINT_URL"] = self.endpoint_url
        else:
            self.endpoint_url = None
            
        # Initialized in setup
        self.s3_client = None
        self.sqs_client = None
        self.ec2_client = None
        self.iam_client = None
        self.asg_client = None
        self.cloudwatch_client = None
        self.ecr_client = None
        
    def _create_boto3_client(self, service_name):
        """Create boto3 client with appropriate config"""
        if self.mode == "local-mock":
            return boto3.client(
                service_name, 
                region_name=self.region,
                endpoint_url=self.endpoint_url
            )
        else:
            return boto3.client(service_name, region_name=self.region)
    
    def _start_moto_server(self):
        """Start moto server in background thread"""
        if self.mode == "local-mock":
            logger.info(f"Starting moto server on port {MOTO_SERVER_PORT}...")
            
            def run_server():
                try:
                    subprocess.call([
                        "python", "-m", "moto.server", 
                        "-p", str(MOTO_SERVER_PORT)
                    ])
                except Exception as e:
                    logger.error(f"Error in moto server: {str(e)}")
            
            self.moto_server = Thread(target=run_server)
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
                        break
                except Exception:
                    pass
                
                logger.info(f"Waiting for moto server ({i+1}/{max_tries})...")
                time.sleep(2)
    
    def setup_clients(self):
        """Initialize boto3 clients"""
        self.s3_client = self._create_boto3_client('s3')
        self.sqs_client = self._create_boto3_client('sqs')
        self.ec2_client = self._create_boto3_client('ec2')
        self.iam_client = self._create_boto3_client('iam')
        self.asg_client = self._create_boto3_client('autoscaling')
        self.cloudwatch_client = self._create_boto3_client('cloudwatch')
        self.ecr_client = self._create_boto3_client('ecr')
    
    def create_network(self):
        """Setup VPC, subnets, and networking components"""
        logger.info("Setting up VPC and network components...")
        
        # Create VPC
        vpc_response = self.ec2_client.create_vpc(CidrBlock=VPC_CIDR)
        vpc_id = vpc_response['Vpc']['VpcId']
        
        # Create public subnet
        public_subnet = self.ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=PUBLIC_SUBNET_CIDR,
            AvailabilityZone=f"{self.region}a"
        )
        public_subnet_id = public_subnet['Subnet']['SubnetId']
        
        # Create private subnet
        private_subnet = self.ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=PRIVATE_SUBNET_CIDR,
            AvailabilityZone=f"{self.region}b"
        )
        private_subnet_id = private_subnet['Subnet']['SubnetId']
        
        # Create Internet Gateway
        igw_response = self.ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        
        # Attach Internet Gateway to VPC
        self.ec2_client.attach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id
        )
        
        # Create route tables
        public_route_table = self.ec2_client.create_route_table(VpcId=vpc_id)
        public_route_table_id = public_route_table['RouteTable']['RouteTableId']
        
        private_route_table = self.ec2_client.create_route_table(VpcId=vpc_id)
        private_route_table_id = private_route_table['RouteTable']['RouteTableId']
        
        # Create public route to internet
        self.ec2_client.create_route(
            RouteTableId=public_route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        
        # Associate route tables with subnets
        self.ec2_client.associate_route_table(
            RouteTableId=public_route_table_id,
            SubnetId=public_subnet_id
        )
        
        self.ec2_client.associate_route_table(
            RouteTableId=private_route_table_id,
            SubnetId=private_subnet_id
        )
        
        # Create NAT Gateway (requires Elastic IP)
        try:
            eip_response = self.ec2_client.allocate_address(Domain='vpc')
            eip_allocation_id = eip_response['AllocationId']
            
            nat_gateway = self.ec2_client.create_nat_gateway(
                SubnetId=public_subnet_id,
                AllocationId=eip_allocation_id
            )
            nat_gateway_id = nat_gateway['NatGateway']['NatGatewayId']
            
            # Create private route through NAT
            self.ec2_client.create_route(
                RouteTableId=private_route_table_id,
                DestinationCidrBlock='0.0.0.0/0',
                NatGatewayId=nat_gateway_id
            )
        except Exception as e:
            logger.warning(f"Error creating NAT Gateway: {str(e)}")
            logger.warning("Some Moto implementations may not fully support NAT Gateways")
            
        # Store resources
        self.resources['vpc_id'] = vpc_id
        self.resources['public_subnet_id'] = public_subnet_id
        self.resources['private_subnet_id'] = private_subnet_id
        
        logger.info(f"Network setup complete. VPC ID: {vpc_id}")
    
    def create_s3_bucket(self):
        """Create S3 bucket for PDF storage"""
        logger.info(f"Creating S3 bucket: {S3_BUCKET_NAME}")
        
        try:
            self.s3_client.create_bucket(Bucket=S3_BUCKET_NAME)
            self.resources['s3_bucket_name'] = S3_BUCKET_NAME
            logger.info(f"S3 bucket created: {S3_BUCKET_NAME}")
        except Exception as e:
            logger.error(f"Error creating S3 bucket: {str(e)}")
            raise
    
    def create_sqs_queue(self):
        """Create SQS queue for tasks"""
        logger.info(f"Creating SQS queue: {SQS_QUEUE_NAME}")
        
        try:
            # Create queue with visibility timeout appropriate for processing time
            queue_response = self.sqs_client.create_queue(
                QueueName=SQS_QUEUE_NAME,
                Attributes={
                    'VisibilityTimeout': '900',  # 15 minutes for VLM processing
                    'MessageRetentionPeriod': '345600'  # 4 days
                }
            )
            
            # Get queue URL
            queue_url = queue_response['QueueUrl']
            
            # Get queue ARN
            queue_attrs = self.sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['QueueArn']
            )
            queue_arn = queue_attrs['Attributes']['QueueArn']
            
            self.resources['sqs_queue_url'] = queue_url
            self.resources['sqs_queue_arn'] = queue_arn
            
            logger.info(f"SQS queue created: {queue_url}")
        except Exception as e:
            logger.error(f"Error creating SQS queue: {str(e)}")
            raise
    
    def create_iam_role(self):
        """Create IAM role for EC2 instances"""
        logger.info(f"Creating IAM role: {IAM_ROLE_NAME}")
        
        try:
            # Create trust policy for EC2
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
            role_response = self.iam_client.create_role(
                RoleName=IAM_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy)
            )
            
            # Attach policies
            self.iam_client.attach_role_policy(
                RoleName=IAM_ROLE_NAME,
                PolicyArn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
            )
            
            self.iam_client.attach_role_policy(
                RoleName=IAM_ROLE_NAME,
                PolicyArn="arn:aws:iam::aws:policy/AmazonSQSFullAccess"
            )
            
            self.iam_client.attach_role_policy(
                RoleName=IAM_ROLE_NAME,
                PolicyArn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
            )
            
            # Create instance profile
            self.iam_client.create_instance_profile(
                InstanceProfileName=IAM_INSTANCE_PROFILE
            )
            
            # Add role to instance profile
            self.iam_client.add_role_to_instance_profile(
                InstanceProfileName=IAM_INSTANCE_PROFILE,
                RoleName=IAM_ROLE_NAME
            )
            
            # Store resources
            self.resources['iam_role_name'] = IAM_ROLE_NAME
            self.resources['iam_instance_profile'] = IAM_INSTANCE_PROFILE
            
            logger.info(f"IAM role and instance profile created: {IAM_ROLE_NAME}")
        except Exception as e:
            logger.error(f"Error creating IAM role: {str(e)}")
            raise
    
    def create_security_group(self):
        """Create security group for EC2 instances"""
        logger.info("Creating security group for EC2 instances")
        
        try:
            # Create security group
            security_group = self.ec2_client.create_security_group(
                GroupName="rag-worker-sg",
                Description="Security group for RAG worker instances",
                VpcId=self.resources['vpc_id']
            )
            
            security_group_id = security_group['GroupId']
            
            # Allow SSH inbound
            self.ec2_client.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    }
                ]
            )
            
            # Allow HTTP for health checks
            self.ec2_client.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8000,
                        'ToPort': 8000,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    }
                ]
            )
            
            # Store resources
            self.resources['security_group_id'] = security_group_id
            
            logger.info(f"Security group created: {security_group_id}")
        except Exception as e:
            logger.error(f"Error creating security group: {str(e)}")
            raise
    
    def create_ecr_repository(self):
        """Create ECR repository for Docker images"""
        logger.info(f"Creating ECR repository: {ECR_REPO_NAME}")
        
        try:
            # Create ECR repository
            response = self.ecr_client.create_repository(
                repositoryName=ECR_REPO_NAME,
                imageScanningConfiguration={
                    'scanOnPush': True
                }
            )
            
            repository_uri = response['repository']['repositoryUri']
            self.resources['ecr_repository_uri'] = repository_uri
            
            logger.info(f"ECR repository created: {repository_uri}")
        except Exception as e:
            logger.error(f"Error creating ECR repository: {str(e)}")
            raise
    
    def create_launch_template(self):
        """Create launch template for EC2 instances"""
        logger.info(f"Creating launch template: {LAUNCH_TEMPLATE_NAME}")
        
        # User data script (base64 encoded in production)
        user_data = """#!/bin/bash
echo "Starting RAG Worker..."
echo "Configuring AWS credentials..."
export AWS_DEFAULT_REGION={region}
export AWS_ACCESS_KEY_ID=mock
export AWS_SECRET_ACCESS_KEY=mock
export AWS_ENDPOINT_URL={endpoint_url}
export S3_BUCKET_NAME={s3_bucket_name}
export SQS_QUEUE_URL={sqs_queue_url}
export QUEUE_TYPE=cloud
export DISABLE_DUPLICATE_LOADING=true

echo "Pulling Docker image..."
docker pull {ecr_repository_uri}:latest

echo "Starting Docker container..."
docker run -d \\
  --name rag-worker \\
  --restart unless-stopped \\
  --gpus all \\
  -e AWS_DEFAULT_REGION={region} \\
  -e AWS_ACCESS_KEY_ID=mock \\
  -e AWS_SECRET_ACCESS_KEY=mock \\
  -e AWS_ENDPOINT_URL={endpoint_url} \\
  -e S3_BUCKET_NAME={s3_bucket_name} \\
  -e SQS_QUEUE_URL={sqs_queue_url} \\
  -e QUEUE_TYPE=cloud \\
  -e DISABLE_DUPLICATE_LOADING=true \\
  -p 8000:8000 \\
  {ecr_repository_uri}:latest
""".format(
            region=self.region,
            endpoint_url=self.endpoint_url,
            s3_bucket_name=self.resources['s3_bucket_name'],
            sqs_queue_url=self.resources['sqs_queue_url'],
            ecr_repository_uri=self.resources['ecr_repository_uri']
        )
        
        try:
            # Create launch template
            response = self.ec2_client.create_launch_template(
                LaunchTemplateName=LAUNCH_TEMPLATE_NAME,
                VersionDescription='Initial version',
                LaunchTemplateData={
                    'ImageId': 'ami-12345678',  # Placeholder AMI ID
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
            
            launch_template_id = response['LaunchTemplate']['LaunchTemplateId']
            self.resources['launch_template_id'] = launch_template_id
            
            logger.info(f"Launch template created: {launch_template_id}")
        except Exception as e:
            logger.error(f"Error creating launch template: {str(e)}")
            raise
    
    def create_auto_scaling_group(self):
        """Create auto scaling group for EC2 instances"""
        logger.info(f"Creating auto scaling group: {ASG_NAME}")
        
        try:
            # Create ASG
            self.asg_client.create_auto_scaling_group(
                AutoScalingGroupName=ASG_NAME,
                LaunchTemplate={
                    'LaunchTemplateName': LAUNCH_TEMPLATE_NAME,
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
            
            # Store resources
            self.resources['auto_scaling_group_name'] = ASG_NAME
            
            logger.info(f"Auto scaling group created: {ASG_NAME}")
        except Exception as e:
            logger.error(f"Error creating auto scaling group: {str(e)}")
            raise
    
    def create_scaling_policy(self):
        """Create scaling policy based on SQS queue depth"""
        logger.info("Creating scaling policy...")
        
        try:
            # Create scaling policy
            scaling_policy = self.asg_client.put_scaling_policy(
                AutoScalingGroupName=self.resources['auto_scaling_group_name'],
                PolicyName='SQSBasedScalingPolicy',
                PolicyType='TargetTrackingScaling',
                TargetTrackingConfiguration={
                    'PredefinedMetricSpecification': {
                        'PredefinedMetricType': 'SQSQueueMessagesVisible',
                        'ResourceLabel': f'{self.resources["sqs_queue_arn"]}'
                    },
                    'TargetValue': 10.0,  # Target messages per instance
                    'ScaleInCooldown': 300,
                    'ScaleOutCooldown': 60
                }
            )
            
            policy_arn = scaling_policy['PolicyARN']
            self.resources['scaling_policy_arn'] = policy_arn
            
            logger.info(f"Scaling policy created: {policy_arn}")
        except Exception as e:
            logger.error(f"Error creating scaling policy: {str(e)}")
            logger.warning("Some scaling features may not be fully supported in Moto")
    
    def create_cloudwatch_alarms(self):
        """Create CloudWatch alarms for monitoring"""
        logger.info("Creating CloudWatch alarms...")
        
        try:
            # Create alarm for queue length
            self.cloudwatch_client.put_metric_alarm(
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
            
            logger.info("CloudWatch alarms created")
        except Exception as e:
            logger.error(f"Error creating CloudWatch alarms: {str(e)}")
            raise
    
    def setup_all(self):
        """Set up all AWS resources"""
        try:
            # Start moto server if in local-mock mode
            if self.mode == "local-mock":
                self._start_moto_server()
            
            # Initialize clients
            self.setup_clients()
            
            # Create resources
            self.create_network()
            self.create_s3_bucket()
            self.create_sqs_queue()
            self.create_iam_role()
            self.create_security_group()
            self.create_ecr_repository()
            self.create_launch_template()
            self.create_auto_scaling_group()
            self.create_scaling_policy()
            self.create_cloudwatch_alarms()
            
            # Output resource details
            logger.info("AWS deployment setup complete!")
            logger.info("Resource summary:")
            for key, value in self.resources.items():
                logger.info(f"  {key}: {value}")
                
            # Create environment file for worker
            self._create_env_file()
            
            return self.resources
        except Exception as e:
            logger.error(f"Error in AWS deployment setup: {str(e)}")
            raise
    
    def _create_env_file(self):
        """Create .env file with AWS resource information"""
        env_content = f"""# AWS Resource Configuration
AWS_DEFAULT_REGION={self.region}
AWS_ACCESS_KEY_ID=mock
AWS_SECRET_ACCESS_KEY=mock
AWS_ENDPOINT_URL={self.endpoint_url}
S3_BUCKET_NAME={self.resources['s3_bucket_name']}
SQS_QUEUE_URL={self.resources['sqs_queue_url']}
QUEUE_TYPE=cloud
DISABLE_DUPLICATE_LOADING=true
"""
        
        # Write to file
        with open(".env.aws", "w") as f:
            f.write(env_content)
            
        logger.info("Created .env.aws file with AWS resource information")
    
    def cleanup(self):
        """Clean up AWS resources"""
        logger.info("Cleaning up AWS resources...")
        
        # Resources need to be deleted in reverse order of creation
        # due to dependencies
        
        try:
            # Delete CloudWatch alarms
            try:
                self.cloudwatch_client.delete_alarms(AlarmNames=['RAGQueueHighAlarm'])
            except Exception:
                pass
            
            # Delete ASG
            if 'auto_scaling_group_name' in self.resources:
                try:
                    self.asg_client.delete_auto_scaling_group(
                        AutoScalingGroupName=self.resources['auto_scaling_group_name'],
                        ForceDelete=True
                    )
                except Exception:
                    pass
            
            # Delete launch template
            if 'launch_template_id' in self.resources:
                try:
                    self.ec2_client.delete_launch_template(
                        LaunchTemplateId=self.resources['launch_template_id']
                    )
                except Exception:
                    pass
            
            # Delete ECR repository
            if 'ecr_repository_uri' in self.resources:
                try:
                    repo_name = self.resources['ecr_repository_uri'].split('/')[-1]
                    self.ecr_client.delete_repository(
                        repositoryName=repo_name,
                        force=True
                    )
                except Exception:
                    pass
            
            # Delete security group
            if 'security_group_id' in self.resources:
                try:
                    self.ec2_client.delete_security_group(
                        GroupId=self.resources['security_group_id']
                    )
                except Exception:
                    pass
            
            # Delete IAM role
            if 'iam_role_name' in self.resources:
                try:
                    # Remove role from instance profile
                    self.iam_client.remove_role_from_instance_profile(
                        InstanceProfileName=self.resources['iam_instance_profile'],
                        RoleName=self.resources['iam_role_name']
                    )
                    
                    # Detach policies
                    policies = [
                        "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
                        "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
                        "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
                    ]
                    
                    for policy in policies:
                        try:
                            self.iam_client.detach_role_policy(
                                RoleName=self.resources['iam_role_name'],
                                PolicyArn=policy
                            )
                        except Exception:
                            pass
                    
                    # Delete instance profile
                    self.iam_client.delete_instance_profile(
                        InstanceProfileName=self.resources['iam_instance_profile']
                    )
                    
                    # Delete role
                    self.iam_client.delete_role(
                        RoleName=self.resources['iam_role_name']
                    )
                except Exception:
                    pass
            
            # Delete SQS queue
            if 'sqs_queue_url' in self.resources:
                try:
                    self.sqs_client.delete_queue(
                        QueueUrl=self.resources['sqs_queue_url']
                    )
                except Exception:
                    pass
            
            # Delete S3 bucket
            if 's3_bucket_name' in self.resources:
                try:
                    # Empty bucket first
                    s3_resource = boto3.resource(
                        's3', 
                        region_name=self.region,
                        endpoint_url=self.endpoint_url
                    )
                    bucket = s3_resource.Bucket(self.resources['s3_bucket_name'])
                    bucket.objects.all().delete()
                    
                    # Delete bucket
                    self.s3_client.delete_bucket(
                        Bucket=self.resources['s3_bucket_name']
                    )
                except Exception:
                    pass
            
            # Delete network resources
            if 'vpc_id' in self.resources:
                try:
                    # Delete subnets
                    if 'public_subnet_id' in self.resources:
                        self.ec2_client.delete_subnet(
                            SubnetId=self.resources['public_subnet_id']
                        )
                    
                    if 'private_subnet_id' in self.resources:
                        self.ec2_client.delete_subnet(
                            SubnetId=self.resources['private_subnet_id']
                        )
                    
                    # Delete VPC
                    self.ec2_client.delete_vpc(
                        VpcId=self.resources['vpc_id']
                    )
                except Exception:
                    pass
            
            logger.info("AWS resources cleanup complete")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
    
    def stop_moto_server(self):
        """Stop moto server if running"""
        if self.moto_server and self.moto_server.is_alive():
            logger.info("Stopping moto server...")
            # There's no clean way to stop the thread, but we can kill the process
            # This will be terminated when the script exits
            pass

@contextmanager
def deploy_aws_environment(mode="local-mock", cleanup=True):
    """Context manager for AWS deployment"""
    deployer = AWSDeployer(mode=mode)
    try:
        deployer.setup_all()
        yield deployer.resources
    finally:
        if cleanup:
            deployer.cleanup()
            deployer.stop_moto_server()

def main():
    """Main function to deploy AWS environment"""
    parser = argparse.ArgumentParser(description='Deploy AWS environment for VLM+RAG worker')
    parser.add_argument('--mode', choices=['local-mock', 'cloud'], default='local-mock',
                        help='Deployment mode (default: local-mock)')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Skip cleanup of AWS resources')
    args = parser.parse_args()
    
    with deploy_aws_environment(mode=args.mode, cleanup=not args.no_cleanup) as resources:
        logger.info("AWS environment deployed successfully")
        logger.info("Press Ctrl+C to terminate and clean up resources")
        try:
            # Keep running until interrupted
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Terminating AWS environment")

if __name__ == "__main__":
    main()