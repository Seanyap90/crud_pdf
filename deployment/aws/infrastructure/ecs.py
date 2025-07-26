"""ECS cluster management with GPU-optimized capacity provider."""
import base64
import logging
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

from files_api.config.settings import get_settings
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
        
    def create_cluster(self, vpc_id: str, private_subnet_ids: List[str]) -> Dict[str, Any]:
        """Create ECS cluster with GPU capacity provider."""
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
                        vpc_id, private_subnet_ids
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
                vpc_id, private_subnet_ids
            )
            
            # Associate capacity providers with cluster
            self._associate_capacity_providers([gpu_capacity_provider['name']])
            
            return cluster_response['cluster']
            
        except ClientError as e:
            logger.error(f"Failed to create ECS cluster: {e}")
            raise
    
    def _create_gpu_capacity_provider(self, vpc_id: str, private_subnet_ids: List[str]) -> Dict[str, Any]:
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
                asg_name, launch_template['LaunchTemplateId'], private_subnet_ids
            )
            
            # Create capacity provider
            cp_response = self.ecs_client.create_capacity_provider(
                name=cp_name,
                autoScalingGroupProvider={
                    'autoScalingGroupArn': asg['AutoScalingGroupARN'],
                    'managedScaling': {
                        'status': 'ENABLED',
                        'targetCapacity': 100,
                        'minimumScalingStepSize': 1,
                        'maximumScalingStepSize': 2,
                        'instanceWarmupPeriod': 180  # 3 minutes for GPU instance warmup
                    },
                    'managedTerminationProtection': 'DISABLED'
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
            
            # Create security group for GPU instances
            gpu_sg_id = self._create_gpu_security_group(vpc_id)
            
            # Create IAM instance profile for ECS instances
            instance_profile_arn = self._create_ecs_instance_profile()
            instance_profile_name = f"{self.cluster_name}-instance-profile"
            
            # Wait for IAM instance profile to propagate
            import time
            logger.info("Waiting for IAM instance profile to propagate...")
            time.sleep(10)  # Wait 10 seconds for IAM propagation
            
            # User data script for ECS agent configuration
            user_data = f"""#!/bin/bash
echo ECS_CLUSTER={self.cluster_name} >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE_NETWORK_HOST=true >> /etc/ecs/ecs.config

# Install NVIDIA Docker runtime
amazon-linux-extras install docker
service docker start
usermod -a -G docker ec2-user

# Install nvidia-docker2
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list

yum install -y nvidia-docker2
systemctl restart docker

# Configure Docker daemon for NVIDIA runtime
cat > /etc/docker/daemon.json <<EOF
{{
    "default-runtime": "nvidia",
    "runtimes": {{
        "nvidia": {{
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }}
    }}
}}
EOF

systemctl restart docker
systemctl restart ecs
"""
            
            # Create launch template
            template_response = self.ec2_client.create_launch_template(
                LaunchTemplateName=template_name,
                LaunchTemplateData={
                    'ImageId': gpu_ami_id,
                    'InstanceType': settings.primary_instance_type,
                    'IamInstanceProfile': {
                        'Name': instance_profile_name
                    },
                    'SecurityGroupIds': [gpu_sg_id],
                    'UserData': base64.b64encode(user_data.encode('utf-8')).decode('utf-8'),  # Must be Base64 encoded for launch templates
                    
                    # Spot instance configuration for 70% cost savings
                    'InstanceMarketOptions': {
                        'MarketType': 'spot',
                        'SpotOptions': {
                            'MaxPrice': str(settings.regional_config[settings.aws_region]['spot_max_price']),
                            'SpotInstanceType': 'one-time'
                        }
                    },
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
                                'VolumeSize': 50,  # 50GB for Docker images and models
                                'VolumeType': 'gp3',
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
            
            # Create ASG
            asg_response = self.asg_client.create_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                LaunchTemplate={
                    'LaunchTemplateId': launch_template_id,
                    'Version': '$Latest'
                },
                MinSize=0,          # Scale to zero when idle
                MaxSize=2,          # HARD LIMIT: 2 instances max (adjusted for current quota)
                DesiredCapacity=0,  # Start with zero instances
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
                    }
                ]
            )
            
            # Get ASG details
            asg_details = self._get_asg_details(asg_name)
            logger.info(f"Created Auto Scaling Group: {asg_name}")
            
            return asg_details
            
        except ClientError as e:
            logger.error(f"Failed to create Auto Scaling Group: {e}")
            raise
    
    def _create_gpu_security_group(self, vpc_id: str) -> str:
        """Create security group for GPU instances."""
        sg_name = f"{self.cluster_name}-gpu-sg"
        
        try:
            # Check for existing security group
            existing_sg = self._find_existing_security_group(sg_name, vpc_id)
            if existing_sg:
                logger.info(f"Using existing GPU security group: {sg_name}")
                return existing_sg['GroupId']
            
            # Create security group
            sg_response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description=f"Security group for {self.cluster_name} GPU instances",
                VpcId=vpc_id,
                TagSpecifications=[
                    {
                        'ResourceType': 'security-group',
                        'Tags': [
                            {'Key': 'Name', 'Value': sg_name},
                            {'Key': 'Project', 'Value': settings.app_name}
                        ]
                    }
                ]
            )
            
            sg_id = sg_response['GroupId']
            
            # Add ingress rules for EFS (NFS)
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 2049,
                        'ToPort': 2049,
                        'IpRanges': [{'CidrIp': '10.0.0.0/16', 'Description': 'EFS NFS within VPC'}]
                    }
                ]
            )
            
            logger.info(f"Created GPU security group: {sg_name}")
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
            logger.info(f"Associated capacity providers with cluster: {capacity_provider_names}")
            
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
        """Get the latest ECS-optimized GPU AMI."""
        try:
            # Get latest ECS-optimized GPU AMI
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
            
            logger.info(f"Using ECS GPU AMI: {ami_id}")
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