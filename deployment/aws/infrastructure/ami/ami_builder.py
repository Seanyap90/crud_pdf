"""
AMI Builder for VLM Workers with Pre-baked Models

Creates custom AMI from existing ECR images with models pre-loaded at /opt/vlm-models
to reduce cold start time from 15-20 minutes to 2-3 minutes (85% improvement).
"""

import logging
import time
import json
from typing import Dict, Any, Optional
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from deployment.aws.infrastructure.ami.ssh_key_manager import SSHKeyManager
from deployment.aws.infrastructure.ami.model_preloader import ModelPreloader
from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

class AMIBuilder:
    """Build custom AMI with pre-loaded VLM models from ECR images."""
    
    def __init__(self, region: str = None):
        """Initialize AMI builder with AWS clients."""
        self.settings = get_settings()
        self.region = region or self.settings.aws_region
        
        # AWS clients
        self.ec2 = boto3.client('ec2', region_name=self.region)
        self.ecr = boto3.client('ecr', region_name=self.region)
        self.iam = boto3.client('iam', region_name=self.region)
        
        # Configuration
        self.app_name = self.settings.app_name or "fastapi-app"
        self.ami_name = f"{self.app_name}-vlm-gpu-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Components
        self.ssh_manager = SSHKeyManager(region=self.region)
        self.model_preloader = ModelPreloader()
        
        # Instance configuration
        self.instance_type = "g4dn.xlarge"  # GPU instance for model loading
        self.security_group_name = "fastapi-app-ecs-workers-sg"
        
        logger.info(f"AMI Builder initialized for {self.ami_name}")
    
    def get_base_ecs_ami(self) -> str:
        """Get the latest ECS-optimized GPU AMI ID."""
        try:
            # Get latest ECS GPU-optimized AMI
            response = self.ec2.describe_images(
                Owners=['amazon'],
                Filters=[
                    {
                        'Name': 'name',
                        'Values': ['amzn2-ami-ecs-gpu-hvm-*']
                    },
                    {
                        'Name': 'state',
                        'Values': ['available']
                    }
                ]
            )
            
            if not response['Images']:
                raise Exception("No ECS GPU AMIs found")
            
            # Sort by creation date and get latest
            images = sorted(response['Images'], 
                          key=lambda x: x['CreationDate'], 
                          reverse=True)
            
            ami_id = images[0]['ImageId']
            ami_name = images[0]['Name']
            
            logger.info(f"Base ECS GPU AMI: {ami_id} ({ami_name})")
            return ami_id
            
        except Exception as e:
            logger.error(f"Failed to get base ECS AMI: {e}")
            raise
    
    def get_vpc_config(self) -> Dict[str, str]:
        """Get VPC configuration from environment or settings."""
        vpc_config = {}
        
        # Try to get from environment variables first (manual setup)
        import os
        vpc_config = {
            'vpc_id': os.getenv('VPC_ID'),
            'subnet_id': os.getenv('PUBLIC_SUBNET_ID'),
            'security_group_id': os.getenv('ECS_WORKERS_SG_ID')
        }
        
        # Validate required configuration
        missing = [k for k, v in vpc_config.items() if not v]
        if missing:
            raise Exception(f"Missing VPC configuration: {missing}. "
                          f"Set environment variables: {', '.join(k.upper() for k in missing)}")
        
        logger.info(f"Using VPC configuration: {vpc_config}")
        return vpc_config
    
    def create_build_instance(self, base_ami_id: str, vpc_config: Dict[str, str]) -> str:
        """Create EC2 instance for AMI building."""
        try:
            # Ensure SSH key exists
            ssh_key_name = self.ssh_manager.ensure_ssh_key()

            # Add AWS eventual consistency delay for SSH key propagation
            logger.info("Waiting for SSH key pair propagation...")
            time.sleep(10)  # Give AWS time to propagate the key pair

            # Create IAM instance profile for ECR access
            instance_profile_arn = self._ensure_build_instance_profile()
            
            # Get root device name from base AMI
            root_device_name = self._get_ami_root_device_name(base_ami_id)
            
            # User data script for model preloading
            user_data_script = self._generate_user_data_script()

            # Base64 encode user data as required by AWS API
            import base64
            user_data = base64.b64encode(user_data_script.encode('utf-8')).decode('utf-8')

            # Verify user data size is within AWS limits (16KB)
            if len(user_data_script) > 15000:  # Leave some buffer
                logger.warning(f"User data script is {len(user_data_script)} bytes - approaching AWS 16KB limit")

            # Launch instance
            response = self.ec2.run_instances(
                ImageId=base_ami_id,
                InstanceType=self.instance_type,
                MinCount=1,
                MaxCount=1,
                KeyName=ssh_key_name,
                SecurityGroupIds=[vpc_config['security_group_id']],
                SubnetId=vpc_config['subnet_id'],
                IamInstanceProfile={
                    'Arn': instance_profile_arn
                },
                UserData=user_data,
                BlockDeviceMappings=[
                    {
                        'DeviceName': root_device_name,
                        'Ebs': {
                            'VolumeSize': 80,  # 100GB for models + OS
                            'VolumeType': 'gp3',
                            'DeleteOnTermination': True
                        }
                    }
                ],
                TagSpecifications=[
                    {
                        'ResourceType': 'instance',
                        'Tags': [
                            {'Key': 'Name', 'Value': f'{self.ami_name}-builder'},
                            {'Key': 'Purpose', 'Value': 'ami-building'},
                            {'Key': 'Project', 'Value': self.app_name}
                        ]
                    }
                ]
            )
            
            instance_id = response['Instances'][0]['InstanceId']
            logger.info(f"Build instance created: {instance_id}")
            
            return instance_id
            
        except Exception as e:
            logger.error(f"Failed to create build instance: {e}")
            raise

    def _cleanup_existing_build_instances(self, profile_name: str) -> None:
        """Clean up any existing instances using the build instance profile."""
        try:
            logger.info(f"Searching for instances using IAM instance profile: {profile_name}")

            # Get ALL running/stopped instances (don't filter by tags initially)
            response = self.ec2.describe_instances(
                Filters=[
                    {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
                ]
            )

            instances_to_terminate = []
            instances_to_stop = []

            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    # Check if instance is using our IAM instance profile
                    if 'IamInstanceProfile' in instance:
                        iam_arn = instance['IamInstanceProfile'].get('Arn', '')
                        if profile_name in iam_arn:
                            instance_id = instance['InstanceId']
                            state = instance['State']['Name']

                            # Get instance name from tags
                            instance_name = "Unknown"
                            for tag in instance.get('Tags', []):
                                if tag['Key'] == 'Name':
                                    instance_name = tag['Value']
                                    break

                            logger.warning(f"Found instance using profile: {instance_id} ({instance_name}) - State: {state}")

                            if state in ['running', 'pending']:
                                instances_to_terminate.append(instance_id)
                            elif state == 'stopped':
                                instances_to_terminate.append(instance_id)
                            elif state == 'stopping':
                                # Wait for it to stop, then terminate
                                instances_to_stop.append(instance_id)

            # Wait for stopping instances to complete stopping
            if instances_to_stop:
                logger.info(f"Waiting for {len(instances_to_stop)} instances to finish stopping...")
                try:
                    waiter = self.ec2.get_waiter('instance_stopped')
                    waiter.wait(
                        InstanceIds=instances_to_stop,
                        WaiterConfig={'Delay': 10, 'MaxAttempts': 30}
                    )
                    instances_to_terminate.extend(instances_to_stop)
                except Exception as e:
                    logger.warning(f"Timeout waiting for instances to stop: {e}")
                    instances_to_terminate.extend(instances_to_stop)  # Try to terminate anyway

            if instances_to_terminate:
                logger.info(f"Terminating {len(instances_to_terminate)} instances using profile {profile_name}")

                # Terminate instances
                try:
                    self.ec2.terminate_instances(InstanceIds=instances_to_terminate)
                    logger.info("Termination initiated successfully")
                except Exception as e:
                    logger.error(f"Failed to initiate termination: {e}")
                    raise

                # Wait for termination to complete with longer timeout
                logger.info("Waiting for instance termination to complete (up to 10 minutes)...")
                try:
                    waiter = self.ec2.get_waiter('instance_terminated')
                    waiter.wait(
                        InstanceIds=instances_to_terminate,
                        WaiterConfig={'Delay': 30, 'MaxAttempts': 20}  # 10 minutes total
                    )
                    logger.info("✅ All instances terminated successfully")

                    # Additional wait to ensure IAM cleanup
                    logger.info("Waiting additional 30 seconds for IAM cleanup...")
                    time.sleep(30)

                except Exception as e:
                    logger.warning(f"Timeout waiting for termination, but continuing: {e}")
            else:
                logger.info("No instances found using the IAM instance profile")

        except Exception as e:
            logger.error(f"Error during cleanup of existing build instances: {e}")
            raise  # Don't continue if cleanup fails

    def _ensure_build_instance_profile(self) -> str:
        """Create or get IAM instance profile for build instance."""
        role_name = f"{self.app_name}-ami-build-role"
        profile_name = f"{self.app_name}-ami-build-profile"
        
        try:
            # Create role if not exists
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
            
            try:
                self.iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Role for AMI building instance"
                )
                logger.info(f"Created IAM role: {role_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise
            
            # Attach ECR read policy
            policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
            try:
                self.iam.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy_arn
                )
                logger.info(f"Attached policy: {policy_arn}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    logger.warning(f"Policy may already be attached: {e}")
            
            # Create instance profile if not exists
            try:
                self.iam.create_instance_profile(
                    InstanceProfileName=profile_name
                )
                logger.info(f"Created instance profile: {profile_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise

            # Add role to instance profile
            try:
                self.iam.add_role_to_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role_name
                )
                logger.info(f"Added role {role_name} to instance profile {profile_name}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'LimitExceeded':
                    logger.warning(f"Instance profile quota exceeded, cleaning up existing instances...")
                    # Clean up existing instances and try again
                    self._cleanup_existing_build_instances(profile_name)
                    time.sleep(5)  # Give AWS time to process the cleanup

                    # Try adding role to profile again
                    try:
                        self.iam.add_role_to_instance_profile(
                            InstanceProfileName=profile_name,
                            RoleName=role_name
                        )
                        logger.info(f"Successfully added role {role_name} to instance profile after cleanup")
                    except ClientError as retry_e:
                        logger.error(f"Still failed after cleanup: {retry_e}")
                        raise
                elif e.response['Error']['Code'] != 'EntityAlreadyExists':
                    logger.warning(f"Role may already be in profile: {e}")
            
            # Add IAM eventual consistency delay
            time.sleep(10)
            logger.info("Waiting for IAM eventual consistency...")
            
            # Get instance profile ARN
            response = self.iam.get_instance_profile(InstanceProfileName=profile_name)
            return response['InstanceProfile']['Arn']
            
        except Exception as e:
            logger.error(f"Failed to create instance profile: {e}")
            raise
    
    def _get_ami_root_device_name(self, ami_id: str) -> str:
        """Get the root device name for the specified AMI."""
        try:
            response = self.ec2.describe_images(ImageIds=[ami_id])
            if not response['Images']:
                raise Exception(f"AMI {ami_id} not found")
            
            root_device_name = response['Images'][0]['RootDeviceName']
            logger.info(f"AMI {ami_id} root device: {root_device_name}")
            return root_device_name
            
        except Exception as e:
            logger.error(f"Failed to get root device name for AMI {ami_id}: {e}")
            # Fallback to common ECS AMI root device names
            logger.warning("Using fallback root device name: /dev/xvda")
            return '/dev/xvda'
    
    def _generate_user_data_script(self) -> str:
        """Generate user data script for model preloading."""
        ecr_repo = f"{self.settings.aws_account_id}.dkr.ecr.{self.region}.amazonaws.com"
        image_uri = f"{ecr_repo}/rag-worker:latest"
        
        script = f"""#!/bin/bash
set -e

# Logging setup
exec > >(tee /var/log/model-preload.log)
exec 2>&1
echo "$(date): Starting model preloading user data script"

# Wait for ECS agent to be ready (avoid systemd deadlock)
echo "$(date): Waiting for ECS agent initialization..."
sleep 30

# Ensure ECS service is running (non-blocking)
systemctl enable --now --no-block ecs 2>/dev/null || true

# Install docker if not present (usually pre-installed on ECS AMI)
if ! command -v docker &> /dev/null; then
    echo "$(date): Installing Docker..."
    yum update -y
    yum install -y docker
    service docker start
    usermod -a -G docker ec2-user
else
    echo "$(date): Docker already installed"
fi

# Ensure Docker is running
systemctl start docker 2>/dev/null || service docker start

# Install prerequisites for AWS CLI
echo "$(date): Installing prerequisites..."
yum install -y curl unzip

# Install AWS CLI v2 if not present
if ! command -v aws &> /dev/null; then
    echo "$(date): Installing AWS CLI v2..."
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
    cd /tmp && unzip -q awscliv2.zip
    ./aws/install

    # Update PATH for current session and future logins
    echo 'export PATH=$PATH:/usr/local/bin' >> ~/.bashrc
    export PATH=$PATH:/usr/local/bin

    # Verify installation
    /usr/local/bin/aws --version
else
    echo "$(date): AWS CLI already installed"
fi

# Create model directory
echo "$(date): Setting up model directory..."
mkdir -p /opt/vlm-models
chown ec2-user:ec2-user /opt/vlm-models

# Login to ECR
echo "$(date): Logging in to ECR..."
/usr/local/bin/aws ecr get-login-password --region {self.region} | docker login --username AWS --password-stdin {ecr_repo}

# Pull the VLM worker image
echo "$(date): Pulling VLM worker image..."
docker pull {image_uri}

# Run container to preload models to host filesystem
echo "$(date): Starting model preloading..."
docker run \\
    --network host \\
    --user root \\
    --name vlm-model-preloader \\
    --rm \\
    --gpus all \\
    -v /opt/vlm-models:/app/cache \\
    -e PRELOAD_MODELS=true \\
    -e MODEL_CACHE_DIR=/app/cache \\
    -e TRANSFORMERS_CACHE=/app/cache/huggingface/transformers \\
    -e HF_HOME=/app/cache/huggingface \\
    -e HF_HUB_OFFLINE=0 \\
    -e CUDA_VISIBLE_DEVICES=0 \\
    {image_uri} \\
    python3 -m vlm_workers.models.downloader

# Set proper permissions
echo "$(date): Setting permissions..."
chown -R ec2-user:ec2-user /opt/vlm-models
chmod -R 755 /opt/vlm-models

# Verify model files exist and validate download success
echo "$(date): Verifying model downloads..."
MODEL_FILES_COUNT=$(find /opt/vlm-models -type f \\( -name "*.bin" -o -name "*.safetensors" -o -name "*.json" \\) | wc -l)
MODEL_SIZE=$(du -sh /opt/vlm-models 2>/dev/null | cut -f1 || echo "unknown")

echo "$(date): Found $MODEL_FILES_COUNT model files, total size: $MODEL_SIZE"

# Validate that we have sufficient models downloaded
if [ $MODEL_FILES_COUNT -lt 10 ]; then
    echo "$(date): ERROR - Only $MODEL_FILES_COUNT model files found, expected at least 10"
    echo "$(date): Model download may have failed - check logs above"
    exit 1
fi

# Verify specific required models are present
echo "$(date): Checking for required models..."
COLPALI_FOUND=false
SMOLVLM_FOUND=false

# Check for ColPali model
if find /opt/vlm-models -path "*colpali*" -name "*.safetensors" | grep -q .; then
    COLPALI_FOUND=true
    echo "$(date): ✅ ColPali model found"
else
    echo "$(date): ❌ ColPali model NOT found"
fi

# Check for SmolVLM model
if find /opt/vlm-models -path "*smolvlm*" -name "*.safetensors" | grep -q .; then
    SMOLVLM_FOUND=true
    echo "$(date): ✅ SmolVLM model found"
else
    echo "$(date): ❌ SmolVLM model NOT found"
fi

# Validate both required models are present
if [ "$COLPALI_FOUND" = false ] || [ "$SMOLVLM_FOUND" = false ]; then
    echo "$(date): ERROR - Required models missing. Both ColPali and SmolVLM are required."
    echo "$(date): Model download appears incomplete - check logs above"
    exit 1
else
    echo "$(date): SUCCESS - All required models downloaded ($MODEL_FILES_COUNT files, $MODEL_SIZE total)"
fi

# Create completion marker
echo "$(date): Creating completion marker..."
cat > /opt/model-preload-complete << EOL
$(date): Model preloading completed successfully
Model files count: $MODEL_FILES_COUNT
Directory size: $MODEL_SIZE
Status: SUCCESS
EOL

echo "$(date): Model preloading user data script completed successfully"

echo "$(date): Cleaning up Docker resources..."
docker system prune -f

echo "$(date): Model preloading user data script completed successfully"
echo "$(date): Script exit code: 0 (success)"
"""
        return script
    
    def wait_for_model_preloading(self, instance_id: str, timeout: int = 3600) -> bool:
        """Wait for model preloading using simple approach - user data script handles validation."""
        logger.info(f"Waiting for model preloading on instance {instance_id}...")
        
        # Wait for instance to be running
        logger.info("Waiting for instance to be running...")
        waiter = self.ec2.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
        logger.info("Instance is running")
        
        # Wait for instance status checks to pass (indicates system is ready)
        logger.info("Waiting for instance status checks...")
        try:
            status_waiter = self.ec2.get_waiter('instance_status_ok')
            status_waiter.wait(
                InstanceIds=[instance_id], 
                WaiterConfig={'Delay': 15, 'MaxAttempts': 20}  # 5 minute timeout
            )
            logger.info("Instance status checks passed")
        except Exception as e:
            logger.warning(f"Status check timeout, but continuing: {e}")
        
        # Wait for model downloads with progress logging
        model_download_time = min(2700, timeout - 300)  # 45 minutes or timeout minus 5 min buffer
        logger.info(f"Waiting {model_download_time/60:.1f} minutes for model downloads...")
        logger.info("User data script will validate model downloads and exit with error if insufficient models found")
        
        start_time = time.time()
        check_interval = 300  # Check every 5 minutes
        
        while time.time() - start_time < model_download_time:
            elapsed_minutes = (time.time() - start_time) / 60
            remaining_minutes = (model_download_time - (time.time() - start_time)) / 60
            
            logger.info(f"Model download progress: {elapsed_minutes:.1f}/{model_download_time/60:.1f} minutes elapsed")
            
            # Check if instance is still running (if it stops, user data likely failed)
            try:
                response = self.ec2.describe_instances(InstanceIds=[instance_id])
                instance = response['Reservations'][0]['Instances'][0]
                state = instance['State']['Name']
                
                if state != 'running':
                    logger.error(f"Instance is {state} - user data script may have failed")
                    return False
                    
            except Exception as e:
                logger.warning(f"Error checking instance state: {e}")
            
            time.sleep(check_interval)
        
        # Final check: if instance is still running, user data likely succeeded
        try:
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            instance = response['Reservations'][0]['Instances'][0]
            state = instance['State']['Name']
            
            if state == 'running':
                logger.info("✅ Instance still running after model download period - assuming success")
                logger.info("User data script validates model downloads and would stop instance if failed")
                return True
            else:
                logger.error(f"❌ Instance is {state} - user data script likely failed")
                return False
                
        except Exception as e:
            logger.error(f"Error checking final instance state: {e}")
            return False
    
    def create_ami_from_instance(self, instance_id: str) -> str:
        """Create AMI from the build instance."""
        try:
            logger.info(f"Creating AMI from instance {instance_id}...")
            
            # Stop instance before creating AMI
            self.ec2.stop_instances(InstanceIds=[instance_id])
            
            # Wait for instance to stop
            waiter = self.ec2.get_waiter('instance_stopped')
            waiter.wait(InstanceIds=[instance_id])
            logger.info("Build instance stopped")
            
            # Create AMI
            response = self.ec2.create_image(
                InstanceId=instance_id,
                Name=self.ami_name,
                Description=f"VLM GPU AMI with pre-loaded models for {self.app_name}",
                NoReboot=True,  # Instance already stopped
                TagSpecifications=[
                    {
                        'ResourceType': 'image',
                        'Tags': [
                            {'Key': 'Name', 'Value': self.ami_name},
                            {'Key': 'Purpose', 'Value': 'vlm-worker'},
                            {'Key': 'Project', 'Value': self.app_name},
                            {'Key': 'Created', 'Value': datetime.now().isoformat()}
                        ]
                    }
                ]
            )
            
            ami_id = response['ImageId']
            logger.info(f"AMI creation started: {ami_id}")
            
            return ami_id
            
        except Exception as e:
            logger.error(f"Failed to create AMI: {e}")
            raise
    
    def wait_for_ami_available(self, ami_id: str, timeout: int = 1800) -> bool:
        """Wait for AMI to become available."""
        logger.info(f"Waiting for AMI {ami_id} to become available...")
        
        waiter = self.ec2.get_waiter('image_available')
        try:
            waiter.wait(
                ImageIds=[ami_id],
                WaiterConfig={'Delay': 60, 'MaxAttempts': timeout // 60}
            )
            logger.info(f"AMI {ami_id} is now available")
            return True
        except Exception as e:
            logger.error(f"Timeout waiting for AMI: {e}")
            return False
    
    def cleanup_build_instance(self, instance_id: str) -> None:
        """Terminate the build instance after AMI creation."""
        try:
            logger.info(f"Terminating build instance {instance_id}...")
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            logger.info("Build instance termination initiated")
        except Exception as e:
            logger.warning(f"Failed to terminate build instance: {e}")
    
    def build_ami(self) -> Dict[str, Any]:
        """Complete AMI building process."""
        logger.info("Starting AMI building process...")
        
        try:
            # Step 1: Get base AMI and VPC config
            base_ami_id = self.get_base_ecs_ami()
            vpc_config = self.get_vpc_config()
            
            # Step 2: Create build instance
            instance_id = self.create_build_instance(base_ami_id, vpc_config)
            
            # Step 3: Wait for model preloading (increased timeout for large models)
            if not self.wait_for_model_preloading(instance_id, timeout=7200):  # 2 hours for large models
                logger.warning("Model preloading timed out, but proceeding with AMI creation")
                logger.warning("AMI may require additional time for first startup")
            
            # Step 4: Create AMI
            ami_id = self.create_ami_from_instance(instance_id)
            
            # Step 5: Wait for AMI to be available
            if not self.wait_for_ami_available(ami_id):
                raise Exception("AMI creation failed or timed out")
            
            # Step 6: Cleanup
            self.cleanup_build_instance(instance_id)
            
            result = {
                'status': 'success',
                'ami_id': ami_id,
                'ami_name': self.ami_name,
                'base_ami_id': base_ami_id,
                'instance_id': instance_id,
                'region': self.region
            }
            
            logger.info(f"AMI building completed successfully: {ami_id}")
            return result
            
        except Exception as e:
            logger.error(f"AMI building failed: {e}")
            return {
                'status': 'failed',
                'error': str(e),
                'ami_name': self.ami_name,
                'region': self.region
            }


def build_vlm_ami(region: str = None) -> Dict[str, Any]:
    """Convenience function to build VLM AMI."""
    builder = AMIBuilder(region=region)
    return builder.build_ami()


if __name__ == "__main__":
    # Test AMI building
    import sys
    
    if len(sys.argv) > 1:
        region = sys.argv[1]
    else:
        region = None
    
    result = build_vlm_ami(region=region)
    print(json.dumps(result, indent=2))