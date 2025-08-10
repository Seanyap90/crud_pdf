"""
Console Resource Detector and Validator.

Validates that console-created AWS resources exist and are properly configured
before proceeding with code-based deployment.
"""

import logging
import os
from typing import Dict, List, Optional, Any, Tuple
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)


class ConsoleResourceDetector:
    """Detect and validate console-created AWS resources."""
    
    def __init__(self, region: str = None):
        self.settings = get_settings()
        self.region = region or self.settings.aws_region
        
        # Initialize AWS clients
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.efs_client = boto3.client('efs', region_name=self.region)
        self.s3_client = boto3.client('s3', region_name=self.region)
        self.sqs_client = boto3.client('sqs', region_name=self.region)
        
        self.validation_results = {
            'valid': True,
            'warnings': [],
            'errors': [],
            'resources': {}
        }
    
    def validate_all_prerequisites(self, config: Dict[str, str]) -> bool:
        """
        Validate all console-created resources exist and are accessible.
        
        Args:
            config: Dictionary with resource IDs/names from environment variables
            
        Returns:
            bool: True if all prerequisites are met
        """
        logger.info("🔍 Validating console-created resources...")
        
        try:
            # Validate VPC resources
            vpc_valid = self.validate_vpc_resources(config)
            
            # Validate EFS resources
            efs_valid = self.validate_efs_resources(config)
            
            # Validate storage resources (S3, SQS)
            storage_valid = self.validate_storage_resources(config)
            
            # Validate security groups
            sg_valid = self.validate_security_groups(config)
            
            # Overall validation result
            all_valid = all([vpc_valid, efs_valid, storage_valid, sg_valid])
            
            if all_valid:
                logger.info("✅ All console prerequisites validated successfully")
            else:
                logger.error("❌ Console prerequisites validation failed")
                self._log_validation_errors()
            
            return all_valid
            
        except Exception as e:
            logger.error(f"❌ Unexpected error during validation: {e}")
            self.validation_results['valid'] = False
            self.validation_results['errors'].append(f"Validation error: {str(e)}")
            return False
    
    def validate_vpc_resources(self, config: Dict[str, str]) -> bool:
        """Validate VPC and subnet resources exist."""
        logger.info("Validating VPC resources...")
        
        try:
            vpc_id = config.get('VPC_ID')
            public_subnet_id = config.get('PUBLIC_SUBNET_ID')
            
            if not vpc_id or not public_subnet_id:
                self._add_error("Missing VPC_ID or PUBLIC_SUBNET_ID in configuration")
                return False
            
            # Validate VPC exists
            vpc_info = self._validate_vpc_exists(vpc_id)
            if not vpc_info:
                return False
            
            # Validate public subnet exists and is in the VPC
            subnet_info = self._validate_subnet_exists(public_subnet_id, vpc_id)
            if not subnet_info:
                return False
            
            # Validate Internet Gateway attached
            igw_attached = self._validate_internet_gateway(vpc_id)
            if not igw_attached:
                return False
            
            # Validate route table for public subnet
            route_valid = self._validate_public_route_table(public_subnet_id)
            if not route_valid:
                return False
            
            self.validation_results['resources']['vpc'] = {
                'vpc_id': vpc_id,
                'vpc_cidr': vpc_info['CidrBlock'],
                'public_subnet_id': public_subnet_id,
                'subnet_cidr': subnet_info['CidrBlock'],
                'availability_zone': subnet_info['AvailabilityZone']
            }
            
            logger.info(f"✅ VPC resources validated: {vpc_id}")
            return True
            
        except ClientError as e:
            self._add_error(f"VPC validation failed: {e}")
            return False
    
    def validate_efs_resources(self, config: Dict[str, str]) -> bool:
        """Validate EFS file system and access point exist."""
        logger.info("Validating EFS resources...")
        
        try:
            efs_id = config.get('EFS_FILE_SYSTEM_ID')
            access_point_id = config.get('EFS_ACCESS_POINT_ID')
            
            if not efs_id or not access_point_id:
                self._add_error("Missing EFS_FILE_SYSTEM_ID or EFS_ACCESS_POINT_ID in configuration")
                return False
            
            # Validate EFS file system exists
            efs_info = self._validate_efs_exists(efs_id)
            if not efs_info:
                return False
            
            # Validate access point exists
            ap_info = self._validate_access_point_exists(access_point_id, efs_id)
            if not ap_info:
                return False
            
            # Validate mount targets exist in the subnet
            public_subnet_id = config.get('PUBLIC_SUBNET_ID')
            mount_target_valid = self._validate_mount_targets(efs_id, public_subnet_id)
            if not mount_target_valid:
                return False
            
            self.validation_results['resources']['efs'] = {
                'file_system_id': efs_id,
                'access_point_id': access_point_id,
                'performance_mode': efs_info['PerformanceMode'],
                'throughput_mode': efs_info['ThroughputMode'],
                'access_point_path': ap_info['RootDirectory']['Path']
            }
            
            logger.info(f"✅ EFS resources validated: {efs_id}")
            return True
            
        except ClientError as e:
            self._add_error(f"EFS validation failed: {e}")
            return False
    
    def validate_storage_resources(self, config: Dict[str, str]) -> bool:
        """Validate S3 bucket and SQS queue exist."""
        logger.info("Validating storage resources...")
        
        try:
            s3_bucket = config.get('S3_BUCKET_NAME')
            sqs_queue_url = config.get('SQS_QUEUE_URL')
            
            if not s3_bucket or not sqs_queue_url:
                self._add_error("Missing S3_BUCKET_NAME or SQS_QUEUE_URL in configuration")
                return False
            
            # Validate S3 bucket exists and is accessible
            s3_valid = self._validate_s3_bucket_exists(s3_bucket)
            if not s3_valid:
                return False
            
            # Validate SQS queue exists and is accessible
            sqs_valid = self._validate_sqs_queue_exists(sqs_queue_url)
            if not sqs_valid:
                return False
            
            self.validation_results['resources']['storage'] = {
                's3_bucket': s3_bucket,
                'sqs_queue_url': sqs_queue_url
            }
            
            logger.info(f"✅ Storage resources validated: S3={s3_bucket}, SQS=queue")
            return True
            
        except ClientError as e:
            self._add_error(f"Storage validation failed: {e}")
            return False
    
    def validate_security_groups(self, config: Dict[str, str]) -> bool:
        """Validate security groups exist and have correct rules."""
        logger.info("Validating security groups...")
        
        try:
            vpc_id = config.get('VPC_ID')
            database_sg_id = config.get('DATABASE_SG_ID')
            efs_sg_id = config.get('EFS_SG_ID')
            ecs_workers_sg_id = config.get('ECS_WORKERS_SG_ID')
            
            required_sgs = {
                'database': database_sg_id,
                'efs': efs_sg_id,
                'ecs_workers': ecs_workers_sg_id
            }
            
            for sg_name, sg_id in required_sgs.items():
                if not sg_id:
                    self._add_error(f"Missing {sg_name.upper()}_SG_ID in configuration")
                    return False
                
                sg_info = self._validate_security_group_exists(sg_id, vpc_id)
                if not sg_info:
                    return False
            
            # Validate security group rules
            sg_rules_valid = self._validate_security_group_rules(required_sgs)
            if not sg_rules_valid:
                return False
            
            self.validation_results['resources']['security_groups'] = required_sgs
            
            logger.info(f"✅ Security groups validated: {len(required_sgs)} groups")
            return True
            
        except ClientError as e:
            self._add_error(f"Security groups validation failed: {e}")
            return False
    
    def _validate_vpc_exists(self, vpc_id: str) -> Optional[Dict[str, Any]]:
        """Validate VPC exists and return its information."""
        try:
            response = self.ec2_client.describe_vpcs(VpcIds=[vpc_id])
            vpcs = response['Vpcs']
            
            if not vpcs:
                self._add_error(f"VPC not found: {vpc_id}")
                return None
            
            vpc = vpcs[0]
            if vpc['State'] != 'available':
                self._add_error(f"VPC not available: {vpc_id} (state: {vpc['State']})")
                return None
            
            return vpc
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidVpcID.NotFound':
                self._add_error(f"VPC not found: {vpc_id}")
            else:
                self._add_error(f"VPC validation error: {e}")
            return None
    
    def _validate_subnet_exists(self, subnet_id: str, vpc_id: str) -> Optional[Dict[str, Any]]:
        """Validate subnet exists and is in the correct VPC."""
        try:
            response = self.ec2_client.describe_subnets(SubnetIds=[subnet_id])
            subnets = response['Subnets']
            
            if not subnets:
                self._add_error(f"Subnet not found: {subnet_id}")
                return None
            
            subnet = subnets[0]
            if subnet['VpcId'] != vpc_id:
                self._add_error(f"Subnet {subnet_id} not in VPC {vpc_id}")
                return None
            
            if subnet['State'] != 'available':
                self._add_error(f"Subnet not available: {subnet_id} (state: {subnet['State']})")
                return None
            
            return subnet
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidSubnetID.NotFound':
                self._add_error(f"Subnet not found: {subnet_id}")
            else:
                self._add_error(f"Subnet validation error: {e}")
            return None
    
    def _validate_internet_gateway(self, vpc_id: str) -> bool:
        """Validate Internet Gateway is attached to VPC."""
        try:
            response = self.ec2_client.describe_internet_gateways(
                Filters=[
                    {'Name': 'attachment.vpc-id', 'Values': [vpc_id]}
                ]
            )
            
            igws = response['InternetGateways']
            if not igws:
                self._add_error(f"No Internet Gateway attached to VPC: {vpc_id}")
                return False
            
            igw = igws[0]
            attachments = igw.get('Attachments', [])
            
            for attachment in attachments:
                if attachment['VpcId'] == vpc_id and attachment['State'] == 'available':
                    return True
            
            self._add_error(f"Internet Gateway not properly attached to VPC: {vpc_id}")
            return False
            
        except ClientError as e:
            self._add_error(f"Internet Gateway validation error: {e}")
            return False
    
    def _validate_public_route_table(self, subnet_id: str) -> bool:
        """Validate public subnet has route to Internet Gateway."""
        try:
            response = self.ec2_client.describe_route_tables(
                Filters=[
                    {'Name': 'association.subnet-id', 'Values': [subnet_id]}
                ]
            )
            
            route_tables = response['RouteTables']
            if not route_tables:
                self._add_warning(f"No explicit route table association for subnet: {subnet_id}")
                # Check main route table
                response = self.ec2_client.describe_route_tables(
                    Filters=[
                        {'Name': 'association.main', 'Values': ['true']},
                        {'Name': 'vpc-id', 'Values': [self._get_subnet_vpc(subnet_id)]}
                    ]
                )
                route_tables = response['RouteTables']
            
            if not route_tables:
                self._add_error(f"No route table found for subnet: {subnet_id}")
                return False
            
            # Check for default route to IGW
            for route_table in route_tables:
                routes = route_table.get('Routes', [])
                for route in routes:
                    if (route.get('DestinationCidrBlock') == '0.0.0.0/0' and 
                        route.get('GatewayId', '').startswith('igw-')):
                        return True
            
            self._add_error(f"No default route to Internet Gateway found for subnet: {subnet_id}")
            return False
            
        except ClientError as e:
            self._add_error(f"Route table validation error: {e}")
            return False
    
    def _validate_efs_exists(self, efs_id: str) -> Optional[Dict[str, Any]]:
        """Validate EFS file system exists."""
        try:
            response = self.efs_client.describe_file_systems(FileSystemId=efs_id)
            file_systems = response['FileSystems']
            
            if not file_systems:
                self._add_error(f"EFS file system not found: {efs_id}")
                return None
            
            efs = file_systems[0]
            if efs['LifeCycleState'] != 'available':
                self._add_error(f"EFS not available: {efs_id} (state: {efs['LifeCycleState']})")
                return None
            
            return efs
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'FileSystemNotFound':
                self._add_error(f"EFS file system not found: {efs_id}")
            else:
                self._add_error(f"EFS validation error: {e}")
            return None
    
    def _validate_access_point_exists(self, access_point_id: str, efs_id: str) -> Optional[Dict[str, Any]]:
        """Validate EFS access point exists."""
        try:
            response = self.efs_client.describe_access_points(AccessPointId=access_point_id)
            access_points = response['AccessPoints']
            
            if not access_points:
                self._add_error(f"EFS access point not found: {access_point_id}")
                return None
            
            access_point = access_points[0]
            if access_point['FileSystemId'] != efs_id:
                self._add_error(f"Access point {access_point_id} not for EFS {efs_id}")
                return None
            
            if access_point['LifeCycleState'] != 'available':
                self._add_error(f"Access point not available: {access_point_id}")
                return None
            
            return access_point
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'AccessPointNotFound':
                self._add_error(f"EFS access point not found: {access_point_id}")
            else:
                self._add_error(f"Access point validation error: {e}")
            return None
    
    def _validate_mount_targets(self, efs_id: str, subnet_id: str) -> bool:
        """Validate EFS mount targets exist in the subnet."""
        try:
            response = self.efs_client.describe_mount_targets(FileSystemId=efs_id)
            mount_targets = response['MountTargets']
            
            if not mount_targets:
                self._add_error(f"No mount targets found for EFS: {efs_id}")
                return False
            
            # Check if there's a mount target in the public subnet
            for mt in mount_targets:
                if mt['SubnetId'] == subnet_id and mt['LifeCycleState'] == 'available':
                    return True
            
            self._add_error(f"No available mount target found in subnet {subnet_id} for EFS {efs_id}")
            return False
            
        except ClientError as e:
            self._add_error(f"Mount target validation error: {e}")
            return False
    
    def _validate_s3_bucket_exists(self, bucket_name: str) -> bool:
        """Validate S3 bucket exists and is accessible."""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                self._add_error(f"S3 bucket not found: {bucket_name}")
            elif error_code == '403':
                self._add_error(f"Access denied to S3 bucket: {bucket_name}")
            else:
                self._add_error(f"S3 bucket validation error: {e}")
            return False
    
    def _validate_sqs_queue_exists(self, queue_url: str) -> bool:
        """Validate SQS queue exists and is accessible."""
        try:
            self.sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['QueueArn']
            )
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AWS.SimpleQueueService.NonExistentQueue':
                self._add_error(f"SQS queue not found: {queue_url}")
            else:
                self._add_error(f"SQS queue validation error: {e}")
            return False
    
    def _validate_security_group_exists(self, sg_id: str, vpc_id: str) -> Optional[Dict[str, Any]]:
        """Validate security group exists in the VPC."""
        try:
            response = self.ec2_client.describe_security_groups(GroupIds=[sg_id])
            security_groups = response['SecurityGroups']
            
            if not security_groups:
                self._add_error(f"Security group not found: {sg_id}")
                return None
            
            sg = security_groups[0]
            if sg['VpcId'] != vpc_id:
                self._add_error(f"Security group {sg_id} not in VPC {vpc_id}")
                return None
            
            return sg
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidGroupId.NotFound':
                self._add_error(f"Security group not found: {sg_id}")
            else:
                self._add_error(f"Security group validation error: {e}")
            return None
    
    def _validate_security_group_rules(self, security_groups: Dict[str, str]) -> bool:
        """Validate security group rules are properly configured."""
        # Basic validation - could be expanded with specific rule checks
        try:
            # For now, just ensure they exist (detailed rule validation can be added later)
            for sg_name, sg_id in security_groups.items():
                response = self.ec2_client.describe_security_groups(GroupIds=[sg_id])
                sg = response['SecurityGroups'][0]
                
                # Log security group info for verification
                inbound_rules = len(sg.get('IpPermissions', []))
                outbound_rules = len(sg.get('IpPermissionsEgress', []))
                logger.info(f"Security group {sg_name}: {inbound_rules} inbound, {outbound_rules} outbound rules")
            
            return True
            
        except ClientError as e:
            self._add_error(f"Security group rules validation error: {e}")
            return False
    
    def _get_subnet_vpc(self, subnet_id: str) -> str:
        """Get VPC ID for a subnet."""
        try:
            response = self.ec2_client.describe_subnets(SubnetIds=[subnet_id])
            return response['Subnets'][0]['VpcId']
        except:
            return ""
    
    def _add_error(self, message: str) -> None:
        """Add validation error."""
        self.validation_results['valid'] = False
        self.validation_results['errors'].append(message)
        logger.error(f"❌ {message}")
    
    def _add_warning(self, message: str) -> None:
        """Add validation warning."""
        self.validation_results['warnings'].append(message)
        logger.warning(f"⚠️ {message}")
    
    def _log_validation_errors(self) -> None:
        """Log all validation errors and warnings."""
        if self.validation_results['errors']:
            logger.error("Validation errors found:")
            for error in self.validation_results['errors']:
                logger.error(f"  - {error}")
        
        if self.validation_results['warnings']:
            logger.warning("Validation warnings found:")
            for warning in self.validation_results['warnings']:
                logger.warning(f"  - {warning}")
    
    def get_validation_results(self) -> Dict[str, Any]:
        """Get complete validation results."""
        return self.validation_results