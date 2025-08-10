"""EC2 Database Manager for SQLite HTTP Server Infrastructure."""
import logging
import json
import time
import os
from pathlib import Path
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EC2DatabaseManager:
    """Manager for EC2-based SQLite HTTP server infrastructure."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.instance_id = None
        self.private_ip = None
        self.security_group_id = None
        
        # SSH key configuration  
        self.key_name = f"{settings.app_name.lower().replace(' ', '-')}-database-key"
        self.key_dir = Path.home() / ".ssh"
        self.private_key_path = self.key_dir / f"{self.key_name}.pem"
        
        logger.info(f"EC2 Database Manager initialized for region: {self.region}")
        logger.info(f"SSH key will be managed as: {self.key_name}")
    
    def ensure_ssh_key_pair(self) -> str:
        """Ensure SSH key pair exists in AWS and is downloaded locally."""
        try:
            # Check if key pair exists in AWS
            existing_key = self._find_existing_key_pair()
            
            if existing_key and self.private_key_path.exists():
                logger.info(f"✅ Using existing SSH key pair: {self.key_name}")
                return self.key_name
            
            # Create new key pair if it doesn't exist in AWS
            if not existing_key:
                logger.info(f"🔑 Creating new SSH key pair: {self.key_name}")
                self._create_aws_key_pair()
            else:
                # Key exists in AWS but not locally - warn user
                logger.warning(f"⚠️  Key pair '{self.key_name}' exists in AWS but private key not found locally")
                logger.warning(f"Expected location: {self.private_key_path}")
                logger.warning("You may need to download the key manually or create a new key pair")
                return self.key_name
            
            return self.key_name
            
        except Exception as e:
            logger.error(f"❌ Failed to ensure SSH key pair: {e}")
            raise
    
    def _find_existing_key_pair(self) -> Optional[Dict[str, Any]]:
        """Check if SSH key pair exists in AWS."""
        try:
            response = self.ec2_client.describe_key_pairs(KeyNames=[self.key_name])
            if response['KeyPairs']:
                key_info = response['KeyPairs'][0]
                logger.debug(f"Found existing key pair: {key_info['KeyName']} (fingerprint: {key_info['KeyFingerprint']})")
                return key_info
            return None
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
                logger.debug(f"Key pair '{self.key_name}' not found in AWS")
                return None
            logger.error(f"Error checking for existing key pair: {e}")
            raise
    
    def _create_aws_key_pair(self) -> None:
        """Create SSH key pair in AWS and save private key locally."""
        try:
            # Ensure .ssh directory exists with proper permissions
            self.key_dir.mkdir(mode=0o700, exist_ok=True)
            logger.debug(f"Ensured .ssh directory exists: {self.key_dir}")
            
            # Create key pair in AWS using verified API
            response = self.ec2_client.create_key_pair(
                KeyName=self.key_name,
                KeyType='rsa',  # RSA key type (verified parameter)
                TagSpecifications=[
                    {
                        'ResourceType': 'key-pair',
                        'Tags': [
                            {'Key': 'Name', 'Value': self.key_name},
                            {'Key': 'Project', 'Value': settings.app_name},
                            {'Key': 'Purpose', 'Value': 'Database-SSH-Access'},
                            {'Key': 'CreatedBy', 'Value': 'EC2DatabaseManager'}
                        ]
                    }
                ]
            )
            
            # Extract private key material from AWS response
            private_key_material = response['KeyMaterial']
            key_fingerprint = response['KeyFingerprint']
            
            # Save private key to local file
            with open(self.private_key_path, 'w', encoding='utf-8') as f:
                f.write(private_key_material)
            
            # Set secure permissions (owner read-only)
            os.chmod(self.private_key_path, 0o400)
            
            logger.info(f"✅ Created SSH key pair successfully!")
            logger.info(f"   🔑 Key name: {self.key_name}")
            logger.info(f"   📁 Saved to: {self.private_key_path}")
            logger.info(f"   🔒 Fingerprint: {key_fingerprint}")
            logger.info(f"   📊 Permissions: 400 (owner read-only)")
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.Duplicate':
                logger.error(f"❌ Key pair '{self.key_name}' already exists in AWS")
                logger.error("Delete the existing key pair first or use a different name")
            else:
                logger.error(f"❌ AWS API error creating key pair: {e}")
            
            # Clean up partial key file if created
            if self.private_key_path.exists():
                self.private_key_path.unlink()
                logger.debug("Cleaned up partial private key file")
            raise
            
        except Exception as e:
            logger.error(f"❌ Unexpected error creating key pair: {e}")
            
            # Clean up partial key file if created
            if self.private_key_path.exists():
                self.private_key_path.unlink()
                logger.debug("Cleaned up partial private key file")
            raise
        
    def validate_subnet_exists(self, subnet_id: str) -> bool:
        """Validate that the subnet exists and is available."""
        try:
            response = self.ec2_client.describe_subnets(SubnetIds=[subnet_id])
            subnets = response['Subnets']
            
            if not subnets:
                logger.error(f"❌ Subnet not found: {subnet_id}")
                return False
            
            subnet = subnets[0]
            if subnet['State'] != 'available':
                logger.error(f"❌ Subnet not available: {subnet_id} (state: {subnet['State']})")
                return False
            
            logger.info(f"✅ Subnet validated: {subnet_id} (CIDR: {subnet['CidrBlock']})")
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidSubnetID.NotFound':
                logger.error(f"❌ Subnet not found: {subnet_id}")
            else:
                logger.error(f"❌ Subnet validation error: {e}")
            return False
    
    def find_existing_database_instance(self) -> Optional[Dict[str, Any]]:
        """Find existing database instance."""
        instance_name = f"{settings.app_name}-database-server"
        return self._find_existing_instance(instance_name)
    
    def create_database_instance(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create EC2 instance for SQLite HTTP server with validation."""
        instance_name = f"{settings.app_name}-database-server"
        subnet_id = vpc_config.get('public_subnet_id')
        security_group_id = vpc_config.get('database_security_group_id')
        
        try:
            # Validate subnet exists before proceeding
            if not subnet_id:
                raise ValueError("Missing public_subnet_id in vpc_config")
            
            if not self.validate_subnet_exists(subnet_id):
                raise ValueError(f"Subnet validation failed: {subnet_id}")
            
            # Validate security group exists
            if not security_group_id:
                raise ValueError("Missing database_security_group_id in vpc_config")
            
            if not self._validate_security_group_exists(security_group_id):
                raise ValueError(f"Security group validation failed: {security_group_id}")
            
            # Check for existing instance
            existing_instance = self.find_existing_database_instance()
            if existing_instance:
                self.instance_id = existing_instance['InstanceId']
                self.private_ip = existing_instance['PrivateIpAddress']
                logger.info(f"✅ Using existing database instance: {self.instance_id}")
                logger.info(f"📋 Manual setup required: Follow deployment/aws/services/README.md")
                
                # Ensure SSH key pair exists even for existing instances
                key_name = self.ensure_ssh_key_pair()
                
                return {
                    'instance_id': self.instance_id,
                    'private_ip': self.private_ip,
                    'public_ip': existing_instance.get('PublicIpAddress'),
                    'instance_state': existing_instance['State']['Name'],
                    'ssh_key_name': key_name,
                    'ssh_private_key_path': str(self.private_key_path),
                    'ssh_command': f'ssh -i {self.private_key_path} ubuntu@{existing_instance.get("PublicIpAddress", self.private_ip)}',
                    'manual_setup_required': True,
                    'setup_guide': 'deployment/aws/services/README.md'
                }
            
            # Ensure SSH key pair exists before creating instance
            key_name = self.ensure_ssh_key_pair()
            logger.info(f"SSH key pair ready: {key_name}")
            
            # Use console-created security group (no need to create new one)
            self.security_group_id = security_group_id
            logger.info(f"Using console-created security group: {security_group_id}")
            
            # Launch EC2 instance (no user data - manual setup)
            logger.info(f"Launching EC2 instance in subnet: {subnet_id}")
            response = self.ec2_client.run_instances(
                ImageId=self._get_ubuntu_22_ami(),
                InstanceType='t3.small',  # Optimal for SQLite HTTP server (2 vCPU, 2GB RAM)
                KeyName=key_name,  # Use the ensured SSH key
                MinCount=1,
                MaxCount=1,
                NetworkInterfaces=[{
                    'AssociatePublicIpAddress': True,
                    'DeviceIndex': 0,
                    'SubnetId': subnet_id,  # Public subnet for direct access
                    'Groups': [security_group_id]
                }],
                IamInstanceProfile={
                    'Name': self._get_or_create_instance_profile()
                },
                TagSpecifications=[{
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': instance_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'SQLite-Database-Server'},
                        {'Key': 'Component', 'Value': 'Database'}
                    ]
                }],
                Monitoring={'Enabled': True}  # Enable detailed monitoring
            )
            
            instance = response['Instances'][0]
            self.instance_id = instance['InstanceId']
            self.private_ip = instance['PrivateIpAddress']
            
            logger.info(f"Created database instance: {self.instance_id}")
            
            # Wait for instance to be running
            logger.info("Waiting for database instance to be running...")
            waiter = self.ec2_client.get_waiter('instance_running')
            waiter.wait(InstanceIds=[self.instance_id])
            
            # Get public IP after instance is running
            instance_info = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
            public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            
            # Manual setup required - no user data script
            logger.info("✅ EC2 instance running - Manual SQLite setup required")
            logger.info("📋 Next steps: Follow deployment/aws/services/README.md")
            logger.info(f"🔗 SSH command: ssh -i {self.private_key_path} ubuntu@{public_ip}")
            
            return {
                'instance_id': self.instance_id,
                'private_ip': self.private_ip,
                'public_ip': public_ip,
                'security_group_id': self.security_group_id,
                'instance_state': 'running',
                'ssh_key_name': key_name,
                'ssh_private_key_path': str(self.private_key_path),
                'ssh_command': f'ssh -i {self.private_key_path} ubuntu@{public_ip}',
                'manual_setup_required': True,
                'setup_guide': 'deployment/aws/services/README.md'
            }
            
        except ClientError as e:
            logger.error(f"Failed to create database instance: {e}")
            raise
    
    def _validate_security_group_exists(self, security_group_id: str) -> bool:
        """Validate security group exists."""
        try:
            response = self.ec2_client.describe_security_groups(GroupIds=[security_group_id])
            security_groups = response['SecurityGroups']
            
            if not security_groups:
                logger.error(f"❌ Security group not found: {security_group_id}")
                return False
            
            sg = security_groups[0]
            logger.info(f"✅ Security group validated: {security_group_id} (Name: {sg['GroupName']})")
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroupId.NotFound':
                logger.error(f"❌ Security group not found: {security_group_id}")
            else:
                logger.error(f"❌ Security group validation error: {e}")
            return False
    
    def wait_for_manual_setup_completion(self, host: str, port: int = 8080, timeout_minutes: int = 30) -> bool:
        """
        Wait for manual SQLite server setup to complete by testing connectivity.
        
        Args:
            host: Database host (private IP)
            port: Database port (default 8080)
            timeout_minutes: Maximum time to wait
            
        Returns:
            bool: True if setup completed successfully
        """
        import requests
        import time
        
        logger.info(f"⏳ Waiting for manual SQLite setup completion on {host}:{port}")
        logger.info("📋 Please complete setup following: deployment/aws/services/README.md")
        
        timeout_seconds = timeout_minutes * 60
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                # Test health endpoint
                response = requests.get(f"http://{host}:{port}/health", timeout=5)
                if response.status_code == 200:
                    logger.info("✅ SQLite HTTP server is responding - setup completed!")
                    return True
                    
            except (requests.RequestException, ConnectionError):
                # Expected until setup is complete
                pass
            
            # Wait before retrying
            time.sleep(30)
            elapsed = int(time.time() - start_time)
            remaining = int(timeout_seconds - elapsed)
            logger.info(f"⏳ Still waiting for setup... ({elapsed}s elapsed, {remaining}s remaining)")
        
        logger.warning(f"⚠️ Timeout waiting for manual setup completion after {timeout_minutes} minutes")
        logger.info("You can continue manually and validate later with:")
        logger.info(f"  curl http://{host}:{port}/health")
        return False
    
    def setup_user_data_script(self) -> str:
        """Return empty user data - manual setup required."""
        return ""
    
    def _get_ubuntu_22_ami(self) -> str:
        """Get the latest Ubuntu 22.04 LTS AMI ID."""
        try:
            response = self.ec2_client.describe_images(
                Filters=[
                    {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*']},
                    {'Name': 'state', 'Values': ['available']}
                ],
                Owners=['099720109477']  # Canonical - official Ubuntu publisher
            )
            
            # Sort by creation date and get the latest
            images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
            if not images:
                raise Exception("No Ubuntu 22.04 LTS AMI found")
            
            ami_id = images[0]['ImageId']
            logger.info(f"Using Ubuntu 22.04 LTS AMI: {ami_id}")
            return ami_id
            
        except ClientError as e:
            logger.error(f"Failed to get Amazon Linux AMI: {e}")
            raise
    
    def _create_database_security_group(self, vpc_config: Dict[str, Any]) -> str:
        """Create security group for database server."""
        sg_name = f"{settings.app_name}-database-sg"
        
        try:
            # Check for existing security group
            try:
                response = self.ec2_client.describe_security_groups(
                    Filters=[
                        {'Name': 'group-name', 'Values': [sg_name]},
                        {'Name': 'vpc-id', 'Values': [vpc_config['vpc_id']]}
                    ]
                )
                if response['SecurityGroups']:
                    sg_id = response['SecurityGroups'][0]['GroupId']
                    logger.info(f"Using existing database security group: {sg_id}")
                    return sg_id
            except ClientError:
                pass
            
            # Create new security group
            sg_response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description="Security group for SQLite HTTP database server",
                VpcId=vpc_config['vpc_id'],
                TagSpecifications=[{
                    'ResourceType': 'security-group',
                    'Tags': [
                        {'Key': 'Name', 'Value': sg_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'Database-Server'}
                    ]
                }]
            )
            sg_id = sg_response['GroupId']
            
            # Add inbound rules: Allow HTTP 8080 and SSH 22 from internet
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8080,
                        'ToPort': 8080,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'SQLite HTTP API'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'SSH access'}]
                    }
                ]
            )
            
            logger.info(f"Created database security group: {sg_id}")
            return sg_id
            
        except ClientError as e:
            logger.error(f"Failed to create database security group: {e}")
            raise
    
    def _get_or_create_instance_profile(self) -> str:
        """Get or create IAM instance profile for EC2 database server."""
        profile_name = f"{settings.app_name}-database-instance-profile"
        role_name = f"{settings.app_name}-database-instance-role"
        
        try:
            from deployment.aws.utils.aws_clients import get_iam_client
            iam_client = get_iam_client()
            
            # Check for existing instance profile
            try:
                response = iam_client.get_instance_profile(InstanceProfileName=profile_name)
                logger.info(f"Using existing instance profile: {profile_name}")
                return profile_name
            except ClientError:
                pass
            
            # Create IAM role for EC2 instance
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
                iam_client.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="IAM role for SQLite database server EC2 instance",
                    Tags=[
                        {'Key': 'Name', 'Value': role_name},
                        {'Key': 'Project', 'Value': settings.app_name}
                    ]
                )
                logger.info(f"Created IAM role: {role_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise
            
            # Attach basic EC2 permissions (CloudWatch, SSM for monitoring)
            iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy'
            )
            
            # Create instance profile
            try:
                iam_client.create_instance_profile(
                    InstanceProfileName=profile_name,
                    Tags=[
                        {'Key': 'Name', 'Value': profile_name},
                        {'Key': 'Project', 'Value': settings.app_name}
                    ]
                )
                logger.info(f"Created instance profile: {profile_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise
            
            # Add role to instance profile
            try:
                iam_client.add_role_to_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role_name
                )
            except ClientError as e:
                if e.response['Error']['Code'] != 'LimitExceeded':
                    raise
            
            return profile_name
            
        except ClientError as e:
            logger.error(f"Failed to create instance profile: {e}")
            raise
    
    def _find_existing_instance(self, instance_name: str) -> Optional[Dict[str, Any]]:
        """Find existing EC2 instance by name tag."""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:Name', 'Values': [instance_name]},
                    {'Name': 'tag:Project', 'Values': [settings.app_name]},
                    {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
                ]
            )
            
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    if instance['State']['Name'] in ['running', 'pending']:
                        return instance
            return None
            
        except ClientError:
            return None
    
    def get_instance_private_ip(self, instance_id: str) -> str:
        """Get private IP address of the database instance."""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            instance = response['Reservations'][0]['Instances'][0]
            return instance['PrivateIpAddress']
        except (ClientError, KeyError, IndexError) as e:
            logger.error(f"Failed to get instance private IP: {e}")
            raise
    
    def get_instance_public_ip(self, instance_id: str) -> str:
        """Get public IP address of the database instance."""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            instance = response['Reservations'][0]['Instances'][0]
            public_ip = instance.get('PublicIpAddress')
            if not public_ip:
                raise ValueError(f"Instance {instance_id} does not have a public IP address")
            return public_ip
        except (ClientError, KeyError, IndexError) as e:
            logger.error(f"Failed to get instance public IP: {e}")
            raise
    
    def cleanup_database_instance(self) -> None:
        """Clean up database instance and related resources."""
        try:
            if self.instance_id:
                # Terminate instance
                self.ec2_client.terminate_instances(InstanceIds=[self.instance_id])
                logger.info(f"Terminated database instance: {self.instance_id}")
                
                # Wait for termination
                waiter = self.ec2_client.get_waiter('instance_terminated')
                waiter.wait(InstanceIds=[self.instance_id])
            
            if self.security_group_id:
                # Delete security group
                try:
                    self.ec2_client.delete_security_group(GroupId=self.security_group_id)
                    logger.info(f"Deleted security group: {self.security_group_id}")
                except ClientError as e:
                    logger.warning(f"Failed to delete security group: {e}")
            
        except ClientError as e:
            logger.error(f"Failed to cleanup database instance: {e}")
            raise