"""ECS cluster management with GPU-optimized capacity provider."""
import base64
import json
import logging
import os
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

from src.files_api.settings import get_settings
from deployment.aws.utils.aws_clients import get_ecs_client, get_ec2_client, get_iam_client, get_asg_client

logger = logging.getLogger(__name__)
settings = get_settings()


class ECSClusterManager:
    """Manager for ECS cluster with GPU-optimized capacity provider."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ecs_client = get_ecs_client()
        self.ec2_client = get_ec2_client()
        self.iam_client = get_iam_client()
        self.asg_client = get_asg_client()
        self.cluster_name = f"{settings.app_name.lower().replace(' ', '-')}-ecs-cluster"
        self.cluster_arn = None
        self.capacity_providers = {}
        
    def create_cluster(self, vpc_id: str, subnet_ids: List[str]) -> Dict[str, Any]:
        """Create ECS cluster with GPU capacity provider."""
        # Use environment variables if not provided (for .env.aws-prod integration)
        if not vpc_id:
            vpc_id = os.getenv('VPC_ID')
            if not vpc_id:
                raise ValueError("VPC_ID must be provided as parameter or environment variable")
        
        if not subnet_ids:
            public_subnet = os.getenv('PUBLIC_SUBNET_ID')
            if public_subnet:
                subnet_ids = [public_subnet]
            else:
                raise ValueError("PUBLIC_SUBNET_ID must be provided as parameter or environment variable")
        
        logger.info(f"Creating ECS cluster in VPC {vpc_id}, subnets: {subnet_ids}")
        
        try:
            # Check for existing cluster
            existing_cluster = self._find_existing_cluster()
            if existing_cluster:
                self.cluster_arn = existing_cluster['clusterArn']
                logger.info(f"Using existing ECS cluster: {self.cluster_name}")
                
                # Check if GPU capacity provider exists for this cluster
                cp_name = f"{self.cluster_name}-gpu-cp"
                existing_cp = self._find_existing_capacity_provider(cp_name)
                if not existing_cp:
                    logger.info(f"GPU capacity provider not found, creating: {cp_name}")
                    # Create GPU capacity provider for existing cluster
                    gpu_capacity_provider = self._create_gpu_capacity_provider(
                        vpc_id, subnet_ids
                    )
                    # Associate capacity providers with cluster
                    self._associate_capacity_providers([gpu_capacity_provider['name']])
                else:
                    logger.info(f"Using existing GPU capacity provider: {cp_name}")
                    self.capacity_providers['gpu'] = existing_cp
                
                return existing_cluster
            
            # Create cluster
            cluster_response = self.ecs_client.create_cluster(
                clusterName=self.cluster_name,
                tags=[
                    {'key': 'Name', 'value': self.cluster_name},
                    {'key': 'Project', 'value': settings.app_name},
                    {'key': 'Purpose', 'value': 'VLM-GPU-Processing'}
                ],
                settings=[
                    {
                        'name': 'containerInsights',
                        'value': 'enabled'
                    }
                ]
            )
            
            self.cluster_arn = cluster_response['cluster']['clusterArn']
            logger.info(f"Created ECS cluster: {self.cluster_name}")
            
            # Create GPU capacity provider
            gpu_capacity_provider = self._create_gpu_capacity_provider(
                vpc_id, subnet_ids
            )
            
            # Associate capacity providers with cluster
            self._associate_capacity_providers([gpu_capacity_provider['name']])
            
            return cluster_response['cluster']
            
        except ClientError as e:
            logger.error(f"Failed to create ECS cluster: {e}")
            raise
    
    def _create_gpu_capacity_provider(self, vpc_id: str, subnet_ids: List[str]) -> Dict[str, Any]:
        """Create GPU-optimized capacity provider with Auto Scaling Group."""
        cp_name = f"{self.cluster_name}-gpu-cp"
        asg_name = f"{self.cluster_name}-gpu-asg"
        
        try:
            # Check for existing capacity provider
            existing_cp = self._find_existing_capacity_provider(cp_name)
            if existing_cp:
                logger.info(f"Using existing GPU capacity provider: {cp_name}")
                self.capacity_providers['gpu'] = existing_cp
                return existing_cp
            
            # Create launch template for GPU instances
            launch_template = self._create_gpu_launch_template(vpc_id)
            
            # Create Auto Scaling Group
            asg = self._create_auto_scaling_group(
                asg_name, launch_template['LaunchTemplateId'], subnet_ids
            )
            
            # Create capacity provider
            cp_response = self.ecs_client.create_capacity_provider(
                name=cp_name,
                autoScalingGroupProvider={
                    'autoScalingGroupArn': asg['AutoScalingGroupARN'],
                    'managedScaling': {
                        'status': 'DISABLED'  # Explicitly disable ECS managed scaling - Lambda controls all scaling
                    },
                    'managedTerminationProtection': 'DISABLED'  # Allow Lambda to manage instance lifecycle
                },
                tags=[
                    {'key': 'Name', 'value': cp_name},
                    {'key': 'Project', 'value': settings.app_name}
                ]
            )
            
            capacity_provider = {
                'name': cp_name,
                'arn': cp_response['capacityProvider']['capacityProviderArn'],
                'asg_name': asg_name,
                'launch_template_id': launch_template['LaunchTemplateId']
            }
            
            self.capacity_providers['gpu'] = capacity_provider
            logger.info(f"Created GPU capacity provider: {cp_name}")
            
            return capacity_provider
            
        except ClientError as e:
            logger.error(f"Failed to create GPU capacity provider: {e}")
            raise
    
    def _create_or_get_key_pair(self) -> str:
        """Create or get existing SSH key pair using SSH key manager."""
        try:
            from deployment.aws.infrastructure.ssh_key_manager import SSHKeyManager
            
            ssh_manager = SSHKeyManager(region=self.region)
            key_name = ssh_manager.ensure_ssh_key()
            
            logger.info(f"SSH key ensured for GPU instances: {key_name}")
            return key_name
            
        except Exception as e:
            logger.error(f"Failed to ensure SSH key: {e}")
            # Fallback to old method if SSH manager fails
            logger.warning("Falling back to legacy SSH key creation")
            return self._create_legacy_key_pair()
    
    def _create_legacy_key_pair(self) -> str:
        """Legacy SSH key creation method (fallback)."""
        key_pair_name = f"{self.cluster_name}-gpu-key-legacy"
        
        try:
            # Check if key pair already exists
            response = self.ec2_client.describe_key_pairs(KeyNames=[key_pair_name])
            if response['KeyPairs']:
                logger.info(f"Using existing legacy key pair: {key_pair_name}")
                return key_pair_name
        except ClientError as e:
            if e.response['Error']['Code'] != 'InvalidKeyPair.NotFound':
                raise
        
        # Create new key pair
        response = self.ec2_client.create_key_pair(
            KeyName=key_pair_name,
            KeyType='rsa'
        )
        
        logger.warning(f"Created legacy key pair: {key_pair_name} (private key not saved locally)")
        return key_pair_name
    
    def _create_gpu_launch_template(self, vpc_id: str) -> Dict[str, Any]:
        """Create launch template for GPU instances."""
        template_name = f"{self.cluster_name}-gpu-template"
        
        try:
            # Check for existing launch template and validate its security groups
            existing_template = self._find_existing_launch_template(template_name)
            if existing_template:
                # Validate that the launch template's security groups are still valid for current VPC
                if self._validate_launch_template_for_vpc(existing_template['LaunchTemplateId'], vpc_id):
                    logger.info(f"Using existing launch template: {template_name}")
                    return existing_template
                else:
                    logger.warning(f"Launch template {template_name} has invalid security groups for VPC {vpc_id}, deleting and recreating")
                    try:
                        self.ec2_client.delete_launch_template(LaunchTemplateId=existing_template['LaunchTemplateId'])
                        logger.info(f"Deleted invalid launch template: {template_name}")
                    except ClientError as e:
                        logger.warning(f"Failed to delete invalid launch template: {e}")
            
            # Get ECS-optimized GPU AMI
            gpu_ami_id = self._get_ecs_gpu_ami()
            
            # Use existing security group from manual setup or create new one
            gpu_sg_id = self._get_or_create_gpu_security_group(vpc_id)
            
            # Create IAM instance profile for ECS instances
            instance_profile_arn = self._create_ecs_instance_profile()
            instance_profile_name = f"{self.cluster_name}-instance-profile"
            
            # Wait for IAM instance profile to propagate
            import time
            logger.info("Waiting for IAM instance profile to propagate...")
            time.sleep(10)  # Wait 10 seconds for IAM propagation
            
            # Create or get SSH key pair for instance access
            key_pair_name = self._create_or_get_key_pair()
            
            # User data script for ECS agent configuration
            # ECS GPU-optimized AMI has NVIDIA drivers and Docker GPU runtime pre-configured
            user_data = f"""#!/bin/bash
echo ECS_CLUSTER={self.cluster_name} >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
"""
            
            # Create launch template
            template_response = self.ec2_client.create_launch_template(
                LaunchTemplateName=template_name,
                LaunchTemplateData={
                    'ImageId': gpu_ami_id,
                    'InstanceType': settings.primary_instance_type,
                    'KeyName': key_pair_name,
                    'IamInstanceProfile': {
                        'Name': instance_profile_name
                    },
                    'SecurityGroupIds': [gpu_sg_id],
                    'UserData': base64.b64encode(user_data.encode('utf-8')).decode('utf-8'),  # Must be Base64 encoded for launch templates
                    'TagSpecifications': [
                        {
                            'ResourceType': 'instance',
                            'Tags': [
                                {'Key': 'Name', 'Value': f"{self.cluster_name}-gpu-instance"},
                                {'Key': 'Project', 'Value': settings.app_name},
                                {'Key': 'Purpose', 'Value': 'VLM-GPU-Processing'}
                            ]
                        }
                    ],
                    'BlockDeviceMappings': [
                        {
                            'DeviceName': '/dev/xvda',
                            'Ebs': {
                                'VolumeSize': 80,   # 80GB for custom AMI with pre-loaded models
                                'VolumeType': 'gp3',
                                'Iops': 3000,       # High IOPS for fast model access
                                'Throughput': 500,  # High throughput for large model files
                                'DeleteOnTermination': True,
                                'Encrypted': True
                            }
                        }
                    ]
                }
            )
            
            launch_template = template_response['LaunchTemplate']
            logger.info(f"Created GPU launch template: {template_name}")
            
            return launch_template
            
        except ClientError as e:
            logger.error(f"Failed to create GPU launch template: {e}")
            raise
    
    def _create_auto_scaling_group(self, asg_name: str, launch_template_id: str, 
                                  subnet_ids: List[str]) -> Dict[str, Any]:
        """Create Auto Scaling Group for GPU instances."""
        try:
            # Check for existing ASG
            existing_asg = self._find_existing_asg(asg_name)
            if existing_asg:
                logger.info(f"Using existing Auto Scaling Group: {asg_name}")
                return existing_asg
            
            # Create ASG (simplified - no warm pools or lifecycle hooks for AMI approach)
            asg_response = self.asg_client.create_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                LaunchTemplate={
                    'LaunchTemplateId': launch_template_id,
                    'Version': '$Latest'
                },
                MinSize=0,          # Allow scaling to zero
                MaxSize=1,          # Max 2 instances (quota limit)
                DesiredCapacity=1,  # Keep desired at 2 (instances will be stopped/started, not terminated)
                VPCZoneIdentifier=','.join(subnet_ids),
                DefaultCooldown=300,  # 5 minutes cooldown
                Tags=[
                    {
                        'Key': 'Name',
                        'Value': asg_name,
                        'PropagateAtLaunch': True,
                        'ResourceId': asg_name,
                        'ResourceType': 'auto-scaling-group'
                    },
                    {
                        'Key': 'Project', 
                        'Value': settings.app_name,
                        'PropagateAtLaunch': True,
                        'ResourceId': asg_name,
                        'ResourceType': 'auto-scaling-group'
                    },
                    {
                        'Key': 'AMIIntegration',
                        'Value': 'true',
                        'PropagateAtLaunch': True,
                        'ResourceId': asg_name,
                        'ResourceType': 'auto-scaling-group'
                    }
                ]
            )
            
            # Get ASG details
            asg_details = self._get_asg_details(asg_name)
            logger.info(f"Created Auto Scaling Group: {asg_name} (AMI-based, no warm pools)")
            
            # NOTE: No lifecycle hooks or warm pools for AMI approach
            # Instances will be stopped/started by Lambda scaling functions
            
            return asg_details
            
        except ClientError as e:
            logger.error(f"Failed to create Auto Scaling Group: {e}")
            raise
    
    def _get_or_create_gpu_security_group(self, vpc_id: str) -> str:
        """Get existing ECS workers security group or create new one."""
        # First, try to get security group ID from environment (manual setup)
        existing_sg_id = os.getenv('ECS_WORKERS_SG_ID')
        
        if existing_sg_id:
            logger.info(f"Using existing ECS workers security group from environment: {existing_sg_id}")
            # Validate that the security group exists and belongs to the correct VPC
            try:
                sg_response = self.ec2_client.describe_security_groups(GroupIds=[existing_sg_id])
                security_groups = sg_response.get('SecurityGroups', [])
                
                if security_groups and security_groups[0]['VpcId'] == vpc_id:
                    sg_name = security_groups[0]['GroupName']
                    logger.info(f"Validated existing security group: {sg_name} ({existing_sg_id})")
                    return existing_sg_id
                else:
                    logger.warning(f"Security group {existing_sg_id} not found or wrong VPC, creating new one")
            except Exception as e:
                logger.warning(f"Could not validate security group {existing_sg_id}: {e}, creating new one")
        
        # Look for fastapi-app-ecs-workers-sg by name  
        target_sg_name = "fastapi-app-ecs-workers-sg"
        existing_sg = self._find_existing_security_group(target_sg_name, vpc_id)
        if existing_sg:
            logger.info(f"Found existing target security group: {target_sg_name}")
            return existing_sg['GroupId']
        
        # Fallback: create new security group with the target name
        sg_name = target_sg_name
        
        try:
            # Check for existing security group
            existing_sg = self._find_existing_security_group(sg_name, vpc_id)
            if existing_sg:
                logger.info(f"Using existing GPU security group: {sg_name}")
                return existing_sg['GroupId']
            
            # Create security group
            sg_response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description=f"Security group for ECS VLM workers with GPU support",
                VpcId=vpc_id,
                TagSpecifications=[
                    {
                        'ResourceType': 'security-group',
                        'Tags': [
                            {'Key': 'Name', 'Value': sg_name},
                            {'Key': 'Project', 'Value': settings.app_name},
                            {'Key': 'Purpose', 'Value': 'ecs-vlm-workers'}
                        ]
                    }
                ]
            )
            
            sg_id = sg_response['GroupId']
            
            # Add ingress rules for AMI-based ECS workers
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8080,
                        'ToPort': 8080,
                        'IpRanges': [{'CidrIp': '10.0.0.0/16', 'Description': 'Database access within VPC'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 443,
                        'ToPort': 443,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTPS outbound for S3/SQS'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 80,
                        'ToPort': 80,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTP outbound'}]
                    }
                ]
            )
            
            logger.info(f"Created ECS workers security group: {sg_name}")
            logger.info(f"Security group ID: {sg_id}")
            logger.info(f"Security group will be used by ASG launch template for AMI-based instances")
            return sg_id
            
        except ClientError as e:
            logger.error(f"Failed to create GPU security group: {e}")
            raise
    
    def _create_ecs_instance_profile(self) -> str:
        """Create IAM instance profile for ECS instances."""
        role_name = f"{self.cluster_name}-instance-role"
        profile_name = f"{self.cluster_name}-instance-profile"
        
        try:
            # Check for existing role and profile
            existing_profile_arn = self._find_existing_instance_profile(profile_name)
            if existing_profile_arn:
                logger.info(f"Using existing instance profile: {profile_name}")
                return existing_profile_arn
            
            # Create IAM role
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
            
            role_response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=str(trust_policy).replace("'", '"'),
                Tags=[
                    {'Key': 'Name', 'Value': role_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Attach ECS instance policy
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role'
            )
            
            # Create instance profile
            profile_response = self.iam_client.create_instance_profile(
                InstanceProfileName=profile_name,
                Tags=[
                    {'Key': 'Name', 'Value': profile_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Add role to instance profile
            self.iam_client.add_role_to_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name
            )
            
            profile_arn = profile_response['InstanceProfile']['Arn']
            logger.info(f"Created ECS instance profile: {profile_name}")
            
            return profile_arn
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                # Get existing profile ARN
                profile_response = self.iam_client.get_instance_profile(
                    InstanceProfileName=profile_name
                )
                return profile_response['InstanceProfile']['Arn']
            logger.error(f"Failed to create ECS instance profile: {e}")
            raise
    
    def _associate_capacity_providers(self, capacity_provider_names: List[str]) -> None:
        """Associate capacity providers with the cluster."""
        try:
            self.ecs_client.put_cluster_capacity_providers(
                cluster=self.cluster_name,
                capacityProviders=capacity_provider_names,
                defaultCapacityProviderStrategy=[
                    {
                        'capacityProvider': capacity_provider_names[0],
                        'weight': 1,
                        'base': 0
                    }
                ]
            )
            logger.info(f"Associated capacity providers with cluster (Lambda-managed scaling): {capacity_provider_names}")

        except ClientError as e:
            logger.error(f"Failed to associate capacity providers: {e}")
            raise
    
    def get_cluster_info(self) -> Dict[str, Any]:
        """Get complete cluster configuration."""
        return {
            'cluster_name': self.cluster_name,
            'cluster_arn': self.cluster_arn,
            'capacity_providers': self.capacity_providers
        }
    
    # Helper methods for finding existing resources
    
    def _find_existing_cluster(self) -> Optional[Dict[str, Any]]:
        """Find existing ECS cluster."""
        try:
            response = self.ecs_client.describe_clusters(clusters=[self.cluster_name])
            clusters = response.get('clusters', [])
            for cluster in clusters:
                if cluster['status'] == 'ACTIVE':
                    return cluster
            return None
        except ClientError:
            return None
    
    def _find_existing_capacity_provider(self, name: str) -> Optional[Dict[str, Any]]:
        """Find existing capacity provider and verify its ASG and AMI are current."""
        try:
            response = self.ecs_client.describe_capacity_providers(
                capacityProviders=[name]
            )
            providers = response.get('capacityProviders', [])
            if providers and providers[0]['status'] == 'ACTIVE':
                # Verify the ASG referenced by this capacity provider still exists
                asg_provider = providers[0].get('autoScalingGroupProvider', {})
                if asg_provider:
                    asg_arn = asg_provider.get('autoScalingGroupArn', '')
                    if asg_arn:
                        # Extract ASG name from ARN and check if it exists
                        asg_name = asg_arn.split('/')[-1]
                        asg_details = self._get_asg_details(asg_name)
                        if not asg_details:
                            logger.warning(f"Capacity provider {name} references deleted ASG {asg_name}. Will recreate.")
                            # Delete the orphaned capacity provider so it can be recreated
                            try:
                                self.ecs_client.delete_capacity_provider(capacityProvider=name)
                                logger.info(f"Deleted orphaned capacity provider: {name}")
                            except ClientError as e:
                                logger.warning(f"Failed to delete orphaned capacity provider {name}: {e}")
                            return None
                        
                        # Verify the ASG's launch template uses the latest ECS GPU AMI
                        launch_template_info = asg_details.get('LaunchTemplate', {})
                        if launch_template_info:
                            launch_template_id = launch_template_info.get('LaunchTemplateId')
                            if launch_template_id and not self._validate_launch_template_ami(launch_template_id):
                                logger.warning(f"Capacity provider {name} uses outdated AMI. Will recreate.")
                                # Delete the orphaned capacity provider so it can be recreated
                                try:
                                    self.ecs_client.delete_capacity_provider(capacityProvider=name)
                                    logger.info(f"Deleted capacity provider with outdated AMI: {name}")
                                except ClientError as e:
                                    logger.warning(f"Failed to delete outdated capacity provider {name}: {e}")
                                return None
                
                return {
                    'name': providers[0]['name'],
                    'arn': providers[0]['capacityProviderArn']
                }
            return None
        except ClientError:
            return None
    
    def _find_existing_launch_template(self, name: str) -> Optional[Dict[str, Any]]:
        """Find existing launch template."""
        try:
            response = self.ec2_client.describe_launch_templates(
                LaunchTemplateNames=[name]
            )
            templates = response.get('LaunchTemplates', [])
            if templates:
                return templates[0]
            return None
        except ClientError:
            return None
    
    def _validate_launch_template_for_vpc(self, launch_template_id: str, vpc_id: str) -> bool:
        """Validate that launch template's security groups are valid for the given VPC."""
        try:
            # Get launch template details
            response = self.ec2_client.describe_launch_template_versions(
                LaunchTemplateId=launch_template_id,
                Versions=['$Latest']
            )
            
            if not response.get('LaunchTemplateVersions'):
                return False
            
            template_data = response['LaunchTemplateVersions'][0]['LaunchTemplateData']
            security_group_ids = template_data.get('SecurityGroupIds', [])
            
            if not security_group_ids:
                return False
            
            # Check if all security groups exist and belong to the target VPC
            for sg_id in security_group_ids:
                try:
                    sg_response = self.ec2_client.describe_security_groups(GroupIds=[sg_id])
                    security_groups = sg_response.get('SecurityGroups', [])
                    
                    if not security_groups:
                        logger.warning(f"Security group {sg_id} not found")
                        return False
                    
                    sg_vpc_id = security_groups[0]['VpcId']
                    if sg_vpc_id != vpc_id:
                        logger.warning(f"Security group {sg_id} belongs to VPC {sg_vpc_id}, expected {vpc_id}")
                        return False
                        
                except ClientError as e:
                    logger.warning(f"Failed to validate security group {sg_id}: {e}")
                    return False
            
            logger.info(f"Launch template {launch_template_id} security groups are valid for VPC {vpc_id}")
            return True
            
        except ClientError as e:
            logger.warning(f"Failed to validate launch template {launch_template_id}: {e}")
            return False
    
    def _validate_launch_template_ami(self, launch_template_id: str) -> bool:
        """Validate that launch template uses the latest ECS GPU AMI."""
        try:
            # Get launch template's current AMI
            response = self.ec2_client.describe_launch_template_versions(
                LaunchTemplateId=launch_template_id,
                Versions=['$Latest']
            )
            
            if not response.get('LaunchTemplateVersions'):
                return False
            
            template_data = response['LaunchTemplateVersions'][0]['LaunchTemplateData']
            current_ami = template_data.get('ImageId')
            
            if not current_ami:
                return False
            
            # Get the latest ECS GPU AMI
            latest_ami = self._get_ecs_gpu_ami()
            
            if current_ami == latest_ami:
                logger.info(f"Launch template {launch_template_id} uses current AMI: {current_ami}")
                return True
            else:
                logger.warning(f"Launch template {launch_template_id} uses outdated AMI {current_ami}, latest is {latest_ami}")
                return False
            
        except ClientError as e:
            logger.warning(f"Failed to validate launch template AMI {launch_template_id}: {e}")
            return False
    
    def _find_existing_asg(self, name: str) -> Optional[Dict[str, Any]]:
        """Find existing Auto Scaling Group."""
        return self._get_asg_details(name)
    
    def _find_existing_security_group(self, name: str, vpc_id: str) -> Optional[Dict[str, Any]]:
        """Find existing security group."""
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'group-name', 'Values': [name]},
                    {'Name': 'vpc-id', 'Values': [vpc_id]}
                ]
            )
            groups = response.get('SecurityGroups', [])
            if groups:
                return groups[0]
            return None
        except ClientError:
            return None
    
    def _find_existing_instance_profile(self, name: str) -> Optional[str]:
        """Find existing IAM instance profile."""
        try:
            response = self.iam_client.get_instance_profile(InstanceProfileName=name)
            return response['InstanceProfile']['Arn']
        except ClientError:
            return None
    
    def _get_ecs_gpu_ami(self) -> str:
        """Get AMI for ECS GPU instances - custom AMI if available, otherwise latest ECS GPU AMI."""
        import os
        
        # Check for custom AMI (AMI integration)
        custom_ami_id = os.getenv('CUSTOM_AMI_ID')
        if custom_ami_id:
            logger.info(f"Using custom AMI with pre-loaded models: {custom_ami_id}")
            return custom_ami_id
        
        try:
            # Get latest ECS-optimized GPU AMI (fallback)
            response = self.ec2_client.describe_images(
                Owners=['amazon'],
                Filters=[
                    {'Name': 'name', 'Values': ['amzn2-ami-ecs-gpu-hvm-*']},
                    {'Name': 'state', 'Values': ['available']},
                    {'Name': 'architecture', 'Values': ['x86_64']}
                ]
            )
            
            images = response.get('Images', [])
            if not images:
                raise Exception("No ECS-optimized GPU AMI found")
            
            # Sort by creation date and get the latest
            latest_image = sorted(images, key=lambda x: x['CreationDate'], reverse=True)[0]
            ami_id = latest_image['ImageId']
            
            logger.info(f"Using standard ECS GPU AMI (no custom AMI set): {ami_id}")
            return ami_id
            
        except ClientError as e:
            logger.error(f"Failed to get ECS GPU AMI: {e}")
            # Fallback to a known working AMI ID for the region
            fallback_amis = {
                'us-east-1': 'ami-0c6c4f4a3d8ba7d41',
                'us-west-2': 'ami-0c6c4f4a3d8ba7d41'
            }
            return fallback_amis.get(self.region, fallback_amis['us-east-1'])
    
    def _get_asg_details(self, asg_name: str) -> Optional[Dict[str, Any]]:
        """Get Auto Scaling Group details."""
        try:
            from deployment.aws.utils.aws_clients import get_asg_client
            asg_client = get_asg_client()
            
            response = asg_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            groups = response.get('AutoScalingGroups', [])
            if groups:
                return groups[0]
            return None
        except ClientError:
            return None
    
    def cleanup_cluster_resources(self) -> None:
        """Clean up cluster resources (for testing/cleanup)."""
        try:
            # Delete cluster (this will fail if services are running)
            if self.cluster_arn:
                try:
                    self.ecs_client.delete_cluster(cluster=self.cluster_name)
                    logger.info(f"Deleted ECS cluster: {self.cluster_name}")
                except ClientError as e:
                    logger.warning(f"Failed to delete cluster: {e}")
            
            # Delete capacity providers
            for cp_name, cp_info in self.capacity_providers.items():
                try:
                    self.ecs_client.delete_capacity_provider(
                        capacityProvider=cp_info['name']
                    )
                    logger.info(f"Deleted capacity provider: {cp_info['name']}")
                except ClientError as e:
                    logger.warning(f"Failed to delete capacity provider: {e}")
            
            # Delete Auto Scaling Groups (after capacity providers are deleted)
            asg_name = f"{self.cluster_name}-gpu-asg"
            try:
                from deployment.aws.utils.aws_clients import get_asg_client
                asg_client = get_asg_client()
                
                # Check if ASG exists
                existing_asg = self._get_asg_details(asg_name)
                if existing_asg:
                    logger.info(f"Deleting Auto Scaling Group: {asg_name}")
                    # Force delete ASG (will terminate all instances)
                    asg_client.delete_auto_scaling_group(
                        AutoScalingGroupName=asg_name,
                        ForceDelete=True
                    )
                    logger.info(f"✅ Deleted Auto Scaling Group: {asg_name}")
                else:
                    logger.info(f"Auto Scaling Group {asg_name} not found")
            except ClientError as e:
                logger.warning(f"Failed to delete Auto Scaling Group: {e}")
            
            # Delete launch templates (after ASGs are deleted)
            template_name = f"{self.cluster_name}-gpu-template"
            try:
                existing_template = self._find_existing_launch_template(template_name)
                if existing_template:
                    self.ec2_client.delete_launch_template(
                        LaunchTemplateId=existing_template['LaunchTemplateId']
                    )
                    logger.info(f"Deleted launch template: {template_name}")
            except ClientError as e:
                logger.warning(f"Failed to delete launch template: {e}")
            
            # Delete IAM instance profile and role (after launch templates are deleted)
            role_name = f"{self.cluster_name}-instance-role"
            profile_name = f"{self.cluster_name}-instance-profile"
            try:
                import boto3
                iam_client = boto3.client('iam', region_name=self.region)
                
                # Remove role from instance profile first
                try:
                    iam_client.remove_role_from_instance_profile(
                        InstanceProfileName=profile_name,
                        RoleName=role_name
                    )
                    logger.info(f"Removed role from instance profile: {profile_name}")
                except ClientError:
                    pass  # May not exist or already removed
                
                # Delete instance profile
                try:
                    iam_client.delete_instance_profile(InstanceProfileName=profile_name)
                    logger.info(f"✅ Deleted IAM instance profile: {profile_name}")
                except ClientError:
                    pass  # May not exist
                
                # Detach managed policies from role
                try:
                    iam_client.detach_role_policy(
                        RoleName=role_name,
                        PolicyArn='arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role'
                    )
                except ClientError:
                    pass  # May not be attached
                
                # Delete IAM role
                try:
                    iam_client.delete_role(RoleName=role_name)
                    logger.info(f"✅ Deleted IAM role: {role_name}")
                except ClientError:
                    pass  # May not exist
                    
            except Exception as e:
                logger.warning(f"Failed to cleanup IAM resources: {e}")
                    
        except Exception as e:
            logger.error(f"Error during cluster cleanup: {e}")
            raise
    
    # Task Definition Methods (consolidated from ecs_services.py)
    
    def create_vlm_worker_task_definition_ami(self, vpc_config: Dict[str, Any],
                                            database_host: str = None,
                                            lambda_function_url: str = None) -> str:
        """Create AMI-based task definition for VLM workers with host path mounts."""
        family = f"{settings.app_name}-vlm-worker-ami"
        
        try:
            # Create CloudWatch log group
            log_group = self._create_log_group(f"/ecs/{family}")
            
            # AMI-based VLM worker task definition with host path mounts
            task_definition = {
                'family': family,
                'networkMode': 'host',
                'requiresCompatibilities': ['EC2'],
                'cpu': '3584',  # 3.5 vCPU
                'memory': '14336',  # 14 GB
                'executionRoleArn': self._get_ecs_execution_role_arn(),
                'taskRoleArn': self._get_ecs_task_role_arn(),
                'volumes': [
                    {
                        'name': 'vlm-models',
                        'host': {
                            'sourcePath': '/opt/vlm-models'  # Models pre-loaded on AMI host
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
                            # Core deployment configuration
                            {'name': 'DEPLOYMENT_MODE', 'value': 'aws-prod'},
                            {'name': 'AWS_REGION', 'value': self.region},
                            {'name': 'SQS_QUEUE_URL', 'value': settings.sqs_queue_url},
                            {'name': 'S3_BUCKET_NAME', 'value': settings.s3_bucket_name},
                            {'name': 'DATABASE_HOST', 'value': database_host or 'localhost'},
                            {'name': 'DATABASE_PORT', 'value': '8080'},

                            # GPU configuration for Tesla T4 16GB (via GPUConfigManager)
                            {'name': 'MODEL_MEMORY_LIMIT', 'value': '14GiB'},
                            {'name': 'OFFLOAD_TO_CPU', 'value': 'false'},
                            {'name': 'CACHE_IMPLEMENTATION', 'value': 'standard'},
                            {'name': 'PYTORCH_CUDA_ALLOC_CONF', 'value': 'max_split_size_mb:1024,garbage_collection_threshold:0.6'},

                            # Model and cache configuration
                            {'name': 'MODEL_CACHE_DIR', 'value': '/app/cache'},
                            {'name': 'CUDA_VISIBLE_DEVICES', 'value': '0'},
                            {'name': 'TRANSFORMERS_CACHE', 'value': '/app/cache'},
                            {'name': 'HF_HOME', 'value': '/app/cache'},
                            {'name': 'HF_HUB_OFFLINE', 'value': '1'},  # Use pre-loaded models only

                            # Application configuration
                            {'name': 'PYTHONPATH', 'value': '/app/src'},
                            {'name': 'CLUSTER_NAME', 'value': self.cluster_name},
                            {'name': 'AMI_BASED_DEPLOYMENT', 'value': 'true'},  # Flag for AMI deployment
                            {'name': 'API_BASE_URL', 'value': lambda_function_url or ''},  # FastAPI backend URL for status updates
                            {'name': 'LAMBDA_FUNCTION_URL', 'value': lambda_function_url or ''}  # Backward compatibility
                        ],
                        'mountPoints': [
                            {
                                'sourceVolume': 'vlm-models',
                                'containerPath': '/app/cache',  # Container path for models
                                'readOnly': False  # Read-write for cache operations and temp files
                            }
                        ],
                        'logConfiguration': {
                            'logDriver': 'awslogs',
                            'options': {
                                'awslogs-group': log_group,
                                'awslogs-region': self.region,
                                'awslogs-stream-prefix': 'vlm-worker-ami'
                            }
                        },
                        'command': ["/app/start-worker.sh"]
                    }
                ],
                'tags': [
                    {'key': 'Name', 'value': family},
                    {'key': 'Project', 'value': settings.app_name},
                    {'key': 'DeploymentType', 'value': 'ami-based'}
                ]
            }
            
            # Register task definition
            response = self.ecs_client.register_task_definition(**task_definition)
            task_def_arn = response['taskDefinition']['taskDefinitionArn']
            
            logger.info(f"Created AMI-based VLM worker task definition: {family}")
            logger.info(f"Task definition ARN: {task_def_arn}")
            
            return task_def_arn
            
        except ClientError as e:
            logger.error(f"Failed to create AMI-based VLM worker task definition: {e}")
            raise
    
    def _create_log_group(self, log_group_name: str) -> str:
        """Create CloudWatch log group for ECS tasks."""
        try:
            from deployment.aws.utils.aws_clients import get_logs_client
            logs_client = get_logs_client()
            
            # Check if log group already exists
            try:
                logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
                logger.info(f"Using existing log group: {log_group_name}")
                return log_group_name
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
            
            # Create new log group
            logs_client.create_log_group(
                logGroupName=log_group_name,
                tags={
                    'Project': settings.app_name,
                    'Purpose': 'ECS-Task-Logs'
                }
            )
            
            # Set retention policy (30 days)
            logs_client.put_retention_policy(
                logGroupName=log_group_name,
                retentionInDays=30
            )
            
            logger.info(f"Created log group: {log_group_name}")
            return log_group_name
            
        except ClientError as e:
            logger.error(f"Failed to create log group: {e}")
            raise
    
    def _get_ecs_execution_role_arn(self) -> str:
        """Get ECS task execution role ARN."""
        role_name = f"{self.cluster_name}-task-execution-role"
        
        try:
            response = self.iam_client.get_role(RoleName=role_name)
            return response['Role']['Arn']
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                # Create the role if it doesn't exist
                return self._create_ecs_execution_role(role_name)
            raise
    
    def _create_ecs_execution_role(self, role_name: str) -> str:
        """Create ECS task execution role."""
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
        
        # Create role
        response = self.iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="ECS task execution role for VLM workers"
        )
        
        # Attach required policy
        self.iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
        )
        
        logger.info(f"Created ECS execution role: {role_name}")
        return response['Role']['Arn']
    
    def _get_ecs_task_role_arn(self) -> str:
        """Get ECS task role ARN for application permissions."""
        role_name = f"{self.cluster_name}-task-role"
        
        try:
            response = self.iam_client.get_role(RoleName=role_name)
            return response['Role']['Arn']
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                # Create the role if it doesn't exist
                return self._create_ecs_task_role(role_name)
            raise
    
    def _create_ecs_task_role(self, role_name: str) -> str:
        """Create ECS task role with S3, SQS, and CloudWatch permissions."""

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

        # Create role
        response = self.iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="ECS task role for VLM worker application permissions"
        )

        # Get account ID if not available in settings
        account_id = settings.aws_account_id
        if not account_id:
            try:
                import boto3
                sts_client = boto3.client('sts', region_name=self.region)
                account_id = sts_client.get_caller_identity()['Account']
            except Exception as e:
                logger.error(f"Failed to get AWS account ID: {e}")
                raise

        # Create and attach custom policy for S3, SQS, CloudWatch
        policy_document = {
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
                    "Resource": f"arn:aws:sqs:{self.region}:{account_id}:{settings.sqs_queue_name}"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "cloudwatch:PutMetricData"
                    ],
                    "Resource": "*"
                }
            ]
        }

        policy_name = f"{role_name}-policy"
        self.iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document)
        )
        
        logger.info(f"Created ECS task role: {role_name}")
        return response['Role']['Arn']


# Consolidated ECS Manager (replaces both ECSClusterManager and ECSServiceManager)
ECSManager = ECSClusterManager