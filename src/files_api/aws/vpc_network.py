"""VPC network infrastructure for ECS deployment with single AZ setup."""
import logging
from typing import Dict, Any, Optional, List
import ipaddress
import boto3
from botocore.exceptions import ClientError

from files_api.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class VPCNetworkBuilder:
    """Builder for VPC network infrastructure with single AZ design."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.vpc_id = None
        self.public_subnet_id = None
        self.private_subnet_id = None
        self.internet_gateway_id = None
        self.nat_gateway_id = None
        self.public_route_table_id = None
        self.private_route_table_id = None
        self.security_groups = {}
        
    def build_vpc(self, vpc_cidr: str = "10.0.0.0/16") -> 'VPCNetworkBuilder':
        """Create VPC with the specified CIDR block."""
        try:
            # Check if VPC already exists
            existing_vpc = self._find_existing_vpc()
            if existing_vpc:
                self.vpc_id = existing_vpc['VpcId']
                logger.info(f"Using existing VPC: {self.vpc_id}")
                return self
                
            # Create new VPC
            response = self.ec2_client.create_vpc(
                CidrBlock=vpc_cidr,
                TagSpecifications=[{
                    'ResourceType': 'vpc',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-vpc"
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.vpc_id = response['Vpc']['VpcId']
            
            # Enable DNS hostnames and resolution
            self.ec2_client.modify_vpc_attribute(
                VpcId=self.vpc_id,
                EnableDnsHostnames={'Value': True}
            )
            self.ec2_client.modify_vpc_attribute(
                VpcId=self.vpc_id,
                EnableDnsSupport={'Value': True}
            )
            
            logger.info(f"Created VPC: {self.vpc_id}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create VPC: {e}")
            raise
    
    def build_subnets(self) -> 'VPCNetworkBuilder':
        """Create public and private subnets in single AZ."""
        if not self.vpc_id:
            raise ValueError("VPC must be created before subnets")
            
        try:
            # Get first availability zone
            az_response = self.ec2_client.describe_availability_zones()
            first_az = az_response['AvailabilityZones'][0]['ZoneName']
            
            # Check for existing subnets
            existing_subnets = self._find_existing_subnets()
            if existing_subnets:
                for subnet in existing_subnets:
                    if 'public' in subnet.get('Tags', [{}])[0].get('Value', '').lower():
                        self.public_subnet_id = subnet['SubnetId']
                    elif 'private' in subnet.get('Tags', [{}])[0].get('Value', '').lower():
                        self.private_subnet_id = subnet['SubnetId']
                if self.public_subnet_id and self.private_subnet_id:
                    logger.info(f"Using existing subnets: public={self.public_subnet_id}, private={self.private_subnet_id}")
                    return self
            
            # Create public subnet (10.0.1.0/24)
            public_subnet_response = self.ec2_client.create_subnet(
                VpcId=self.vpc_id,
                CidrBlock="10.0.1.0/24",
                AvailabilityZone=first_az,
                TagSpecifications=[{
                    'ResourceType': 'subnet',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-public-subnet"
                    }, {
                        'Key': 'Type',
                        'Value': 'public'
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.public_subnet_id = public_subnet_response['Subnet']['SubnetId']
            
            # Create private subnet (10.0.2.0/24)
            private_subnet_response = self.ec2_client.create_subnet(
                VpcId=self.vpc_id,
                CidrBlock="10.0.2.0/24",
                AvailabilityZone=first_az,
                TagSpecifications=[{
                    'ResourceType': 'subnet',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-private-subnet"
                    }, {
                        'Key': 'Type',
                        'Value': 'private'
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.private_subnet_id = private_subnet_response['Subnet']['SubnetId']
            
            logger.info(f"Created subnets: public={self.public_subnet_id}, private={self.private_subnet_id}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create subnets: {e}")
            raise
    
    def build_internet_gateway(self) -> 'VPCNetworkBuilder':
        """Create and attach internet gateway."""
        if not self.vpc_id:
            raise ValueError("VPC must be created before internet gateway")
            
        try:
            # Check for existing internet gateway
            existing_igw = self._find_existing_internet_gateway()
            if existing_igw:
                self.internet_gateway_id = existing_igw['InternetGatewayId']
                logger.info(f"Using existing internet gateway: {self.internet_gateway_id}")
                return self
                
            # Create internet gateway
            igw_response = self.ec2_client.create_internet_gateway(
                TagSpecifications=[{
                    'ResourceType': 'internet-gateway',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-igw"
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.internet_gateway_id = igw_response['InternetGateway']['InternetGatewayId']
            
            # Attach to VPC
            self.ec2_client.attach_internet_gateway(
                InternetGatewayId=self.internet_gateway_id,
                VpcId=self.vpc_id
            )
            
            logger.info(f"Created and attached internet gateway: {self.internet_gateway_id}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create internet gateway: {e}")
            raise
    
    def build_nat_gateway(self) -> 'VPCNetworkBuilder':
        """Create NAT gateway in public subnet for private subnet internet access."""
        if not self.public_subnet_id:
            raise ValueError("Public subnet must be created before NAT gateway")
            
        try:
            # Check for existing NAT gateway
            existing_nat = self._find_existing_nat_gateway()
            if existing_nat:
                self.nat_gateway_id = existing_nat['NatGatewayId']
                logger.info(f"Using existing NAT gateway: {self.nat_gateway_id}")
                return self
                
            # Allocate Elastic IP for NAT gateway
            eip_response = self.ec2_client.allocate_address(
                Domain='vpc',
                TagSpecifications=[{
                    'ResourceType': 'elastic-ip',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-nat-eip"
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            allocation_id = eip_response['AllocationId']
            
            # Create NAT gateway
            nat_response = self.ec2_client.create_nat_gateway(
                SubnetId=self.public_subnet_id,
                AllocationId=allocation_id,
                TagSpecifications=[{
                    'ResourceType': 'nat-gateway',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-nat"
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.nat_gateway_id = nat_response['NatGateway']['NatGatewayId']
            
            # Wait for NAT gateway to be available
            logger.info("Waiting for NAT gateway to become available...")
            waiter = self.ec2_client.get_waiter('nat_gateway_available')
            waiter.wait(NatGatewayIds=[self.nat_gateway_id])
            
            logger.info(f"Created NAT gateway: {self.nat_gateway_id}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create NAT gateway: {e}")
            raise
    
    def build_route_tables(self) -> 'VPCNetworkBuilder':
        """Create and configure route tables for public and private subnets."""
        if not self.vpc_id or not self.internet_gateway_id or not self.nat_gateway_id:
            raise ValueError("VPC, IGW, and NAT gateway must be created before route tables")
            
        try:
            # Check for existing route tables
            existing_route_tables = self._find_existing_route_tables()
            if existing_route_tables:
                for rt in existing_route_tables:
                    rt_name = next((tag['Value'] for tag in rt.get('Tags', []) if tag['Key'] == 'Name'), '')
                    if 'public' in rt_name.lower():
                        self.public_route_table_id = rt['RouteTableId']
                    elif 'private' in rt_name.lower():
                        self.private_route_table_id = rt['RouteTableId']
                if self.public_route_table_id and self.private_route_table_id:
                    logger.info(f"Using existing route tables: public={self.public_route_table_id}, private={self.private_route_table_id}")
                    return self
            
            # Create public route table
            public_rt_response = self.ec2_client.create_route_table(
                VpcId=self.vpc_id,
                TagSpecifications=[{
                    'ResourceType': 'route-table',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-public-rt"
                    }, {
                        'Key': 'Type',
                        'Value': 'public'
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.public_route_table_id = public_rt_response['RouteTable']['RouteTableId']
            
            # Create private route table
            private_rt_response = self.ec2_client.create_route_table(
                VpcId=self.vpc_id,
                TagSpecifications=[{
                    'ResourceType': 'route-table',
                    'Tags': [{
                        'Key': 'Name',
                        'Value': f"{settings.app_name}-ecs-private-rt"
                    }, {
                        'Key': 'Type',
                        'Value': 'private'
                    }, {
                        'Key': 'Project',
                        'Value': settings.app_name
                    }]
                }]
            )
            self.private_route_table_id = private_rt_response['RouteTable']['RouteTableId']
            
            # Add routes
            # Public route table: 0.0.0.0/0 -> Internet Gateway
            self.ec2_client.create_route(
                RouteTableId=self.public_route_table_id,
                DestinationCidrBlock='0.0.0.0/0',
                GatewayId=self.internet_gateway_id
            )
            
            # Private route table: 0.0.0.0/0 -> NAT Gateway
            self.ec2_client.create_route(
                RouteTableId=self.private_route_table_id,
                DestinationCidrBlock='0.0.0.0/0',
                NatGatewayId=self.nat_gateway_id
            )
            
            # Associate subnets with route tables
            self.ec2_client.associate_route_table(
                SubnetId=self.public_subnet_id,
                RouteTableId=self.public_route_table_id
            )
            
            self.ec2_client.associate_route_table(
                SubnetId=self.private_subnet_id,
                RouteTableId=self.private_route_table_id
            )
            
            logger.info(f"Created route tables: public={self.public_route_table_id}, private={self.private_route_table_id}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create route tables: {e}")
            raise
    
    def build_security_groups(self) -> 'VPCNetworkBuilder':
        """Create security groups for ECS services."""
        if not self.vpc_id:
            raise ValueError("VPC must be created before security groups")
            
        try:
            # EFS security group (NFS within VPC)
            efs_sg = self._create_security_group(
                'efs',
                f"{settings.app_name}-ecs-efs-sg",
                "EFS access within VPC",
                [{'IpProtocol': 'tcp', 'FromPort': 2049, 'ToPort': 2049, 'CidrIp': '10.0.0.0/16'}]
            )
            self.security_groups['efs'] = efs_sg
            
            # MongoDB security group (port 27017 within VPC)
            mongodb_sg = self._create_security_group(
                'mongodb',
                f"{settings.app_name}-ecs-mongodb-sg",
                "MongoDB access within VPC",
                [{'IpProtocol': 'tcp', 'FromPort': 27017, 'ToPort': 27017, 'CidrIp': '10.0.0.0/16'}]
            )
            self.security_groups['mongodb'] = mongodb_sg
            
            # VLM workers security group (outbound only)
            vlm_workers_sg = self._create_security_group(
                'vlm_workers',
                f"{settings.app_name}-ecs-vlm-workers-sg",
                "VLM workers outbound access",
                [],  # No inbound rules - outbound only
                [{'IpProtocol': '-1', 'CidrIp': '0.0.0.0/0'}]  # Allow all outbound
            )
            self.security_groups['vlm_workers'] = vlm_workers_sg
            
            logger.info(f"Created security groups: {list(self.security_groups.keys())}")
            return self
            
        except ClientError as e:
            logger.error(f"Failed to create security groups: {e}")
            raise
    
    def get_network_config(self) -> Dict[str, Any]:
        """Get the complete network configuration."""
        return {
            'vpc_id': self.vpc_id,
            'public_subnet_id': self.public_subnet_id,
            'private_subnet_id': self.private_subnet_id,
            'internet_gateway_id': self.internet_gateway_id,
            'nat_gateway_id': self.nat_gateway_id,
            'public_route_table_id': self.public_route_table_id,
            'private_route_table_id': self.private_route_table_id,
            'security_groups': self.security_groups
        }
    
    def _find_existing_vpc(self) -> Optional[Dict[str, Any]]:
        """Find existing VPC by project tag."""
        try:
            response = self.ec2_client.describe_vpcs(
                Filters=[
                    {'Name': 'tag:Project', 'Values': [settings.app_name]},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
            return response['Vpcs'][0] if response['Vpcs'] else None
        except (ClientError, IndexError):
            return None
    
    def _find_existing_subnets(self) -> List[Dict[str, Any]]:
        """Find existing subnets by project tag."""
        try:
            response = self.ec2_client.describe_subnets(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [self.vpc_id]},
                    {'Name': 'tag:Project', 'Values': [settings.app_name]}
                ]
            )
            return response['Subnets']
        except ClientError:
            return []
    
    def _find_existing_internet_gateway(self) -> Optional[Dict[str, Any]]:
        """Find existing internet gateway by project tag."""
        try:
            response = self.ec2_client.describe_internet_gateways(
                Filters=[
                    {'Name': 'tag:Project', 'Values': [settings.app_name]},
                    {'Name': 'attachment.vpc-id', 'Values': [self.vpc_id]}
                ]
            )
            return response['InternetGateways'][0] if response['InternetGateways'] else None
        except (ClientError, IndexError):
            return None
    
    def _find_existing_nat_gateway(self) -> Optional[Dict[str, Any]]:
        """Find existing NAT gateway by project tag."""
        try:
            response = self.ec2_client.describe_nat_gateways(
                Filters=[
                    {'Name': 'tag:Project', 'Values': [settings.app_name]},
                    {'Name': 'subnet-id', 'Values': [self.public_subnet_id]},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
            return response['NatGateways'][0] if response['NatGateways'] else None
        except (ClientError, IndexError):
            return None
    
    def _find_existing_route_tables(self) -> List[Dict[str, Any]]:
        """Find existing route tables by project tag."""
        try:
            response = self.ec2_client.describe_route_tables(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [self.vpc_id]},
                    {'Name': 'tag:Project', 'Values': [settings.app_name]}
                ]
            )
            return response['RouteTables']
        except ClientError:
            return []
    
    def _create_security_group(self, sg_type: str, name: str, description: str, 
                              inbound_rules: List[Dict[str, Any]] = None,
                              outbound_rules: List[Dict[str, Any]] = None) -> str:
        """Create a security group with specified rules."""
        # Check for existing security group
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'group-name', 'Values': [name]},
                    {'Name': 'vpc-id', 'Values': [self.vpc_id]}
                ]
            )
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                logger.info(f"Using existing security group {sg_type}: {sg_id}")
                return sg_id
        except ClientError:
            pass
        
        # Create new security group
        sg_response = self.ec2_client.create_security_group(
            GroupName=name,
            Description=description,
            VpcId=self.vpc_id,
            TagSpecifications=[{
                'ResourceType': 'security-group',
                'Tags': [{
                    'Key': 'Name',
                    'Value': name
                }, {
                    'Key': 'Type',
                    'Value': sg_type
                }, {
                    'Key': 'Project',
                    'Value': settings.app_name
                }]
            }]
        )
        sg_id = sg_response['GroupId']
        
        # Add inbound rules
        if inbound_rules:
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=inbound_rules
            )
        
        # Add outbound rules (replace default all-outbound if specified)
        if outbound_rules:
            # Remove default outbound rule
            try:
                self.ec2_client.revoke_security_group_egress(
                    GroupId=sg_id,
                    IpPermissions=[{'IpProtocol': '-1', 'CidrIp': '0.0.0.0/0'}]
                )
            except ClientError:
                pass  # May not exist
            
            # Add custom outbound rules
            self.ec2_client.authorize_security_group_egress(
                GroupId=sg_id,
                IpPermissions=outbound_rules
            )
        
        logger.info(f"Created security group {sg_type}: {sg_id}")
        return sg_id