u"""EFS file system management for MongoDB data and model storage."""
import logging
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

from files_api.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EFSManager:
    """Manager for EFS file systems with in-VPC placement."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.efs_client = boto3.client('efs', region_name=self.region)
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.file_systems = {}
        self.mount_targets = {}
        self.access_points = {}
    
    def create_mongodb_efs(self, vpc_id: str, subnet_id: str, security_group_id: str) -> Dict[str, Any]:
        """Create EFS file system for MongoDB data storage."""
        fs_name = f"{settings.app_name}-mongodb-data"
        
        try:
            # Check for existing file system
            existing_fs = self._find_existing_file_system(fs_name)
            if existing_fs:
                fs_id = existing_fs['FileSystemId']
                logger.info(f"Using existing MongoDB EFS: {fs_id}")
                self.file_systems['mongodb'] = existing_fs
            else:
                # Create new file system
                fs_response = self.efs_client.create_file_system(
                    CreationToken=f"{settings.app_name}-mongodb-{hash(fs_name) % 10000}",
                    PerformanceMode='generalPurpose',
                    StorageClass='Standard',
                    ThroughputMode='provisioned',
                    ProvisionedThroughputInMibps=10,  # 10 MiB/s for MongoDB
                    Tags=[
                        {'Key': 'Name', 'Value': fs_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'mongodb-data'}
                    ]
                )
                fs_id = fs_response['FileSystemId']
                self.file_systems['mongodb'] = fs_response
                logger.info(f"Created MongoDB EFS: {fs_id}")
            
            # Create mount target in public subnet (for MongoDB service)
            mount_target = self._create_mount_target(
                fs_id, subnet_id, security_group_id, 'mongodb'
            )
            
            # Create access point for MongoDB data
            access_point = self._create_access_point(
                fs_id, 'mongodb-data', '/data/db', 999, 999
            )
            
            return {
                'file_system_id': fs_id,
                'mount_target_id': mount_target['MountTargetId'],
                'access_point_id': access_point['AccessPointId'],
                'mount_target_ip': mount_target.get('IpAddress'),
                'dns_name': f"{fs_id}.efs.{self.region}.amazonaws.com"
            }
            
        except ClientError as e:
            logger.error(f"Failed to create MongoDB EFS: {e}")
            raise
    
    def create_models_efs(self, vpc_id: str, subnet_id: str, security_group_id: str) -> Dict[str, Any]:
        """Create EFS file system for VLM model storage."""
        fs_name = f"{settings.app_name}-vlm-models"
        
        try:
            # Check for existing file system
            existing_fs = self._find_existing_file_system(fs_name)
            if existing_fs:
                fs_id = existing_fs['FileSystemId']
                logger.info(f"Using existing models EFS: {fs_id}")
                self.file_systems['models'] = existing_fs
            else:
                # Create new file system with higher throughput for model loading
                fs_response = self.efs_client.create_file_system(
                    CreationToken=f"{settings.app_name}-models-{hash(fs_name) % 10000}",
                    PerformanceMode='generalPurpose',
                    StorageClass='Standard',
                    ThroughputMode='provisioned',
                    ProvisionedThroughputInMibps=50,  # 50 MiB/s for model loading
                    Tags=[
                        {'Key': 'Name', 'Value': fs_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'vlm-models'}
                    ]
                )
                fs_id = fs_response['FileSystemId']
                self.file_systems['models'] = fs_response
                logger.info(f"Created models EFS: {fs_id}")
            
            # Create mount target in private subnet (for VLM workers)
            mount_target = self._create_mount_target(
                fs_id, subnet_id, security_group_id, 'models'
            )
            
            # Create access points for different model types
            colpali_ap = self._create_access_point(
                fs_id, 'colpali-models', '/models/colpali', 1000, 1000
            )
            
            smolvlm_ap = self._create_access_point(
                fs_id, 'smolvlm-models', '/models/smolvlm', 1000, 1000
            )
            
            return {
                'file_system_id': fs_id,
                'mount_target_id': mount_target['MountTargetId'],
                'colpali_access_point_id': colpali_ap['AccessPointId'],
                'smolvlm_access_point_id': smolvlm_ap['AccessPointId'],
                'mount_target_ip': mount_target.get('IpAddress'),
                'dns_name': f"{fs_id}.efs.{self.region}.amazonaws.com"
            }
            
        except ClientError as e:
            logger.error(f"Failed to create models EFS: {e}")
            raise
    
    def wait_for_mount_targets_available(self) -> None:
        """Wait for all mount targets to become available."""
        for purpose, mount_target_id in self.mount_targets.items():
            try:
                logger.info(f"Waiting for {purpose} mount target to become available: {mount_target_id}")
                waiter = self.efs_client.get_waiter('mount_target_available')
                waiter.wait(MountTargetId=mount_target_id)
                logger.info(f"{purpose} mount target available: {mount_target_id}")
            except ClientError as e:
                logger.error(f"Failed waiting for {purpose} mount target: {e}")
                raise
    
    def get_efs_config(self) -> Dict[str, Any]:
        """Get complete EFS configuration."""
        return {
            'file_systems': self.file_systems,
            'mount_targets': self.mount_targets,
            'access_points': self.access_points
        }
    
    def _find_existing_file_system(self, name: str) -> Optional[Dict[str, Any]]:
        """Find existing EFS file system by name tag."""
        try:
            response = self.efs_client.describe_file_systems()
            for fs in response['FileSystems']:
                # Get tags for this file system
                tags_response = self.efs_client.describe_tags(FileSystemId=fs['FileSystemId'])
                tags = {tag['Key']: tag['Value'] for tag in tags_response['Tags']}
                
                if tags.get('Name') == name and tags.get('Project') == settings.app_name:
                    return fs
            return None
        except ClientError:
            return None
    
    def _create_mount_target(self, file_system_id: str, subnet_id: str, 
                           security_group_id: str, purpose: str) -> Dict[str, Any]:
        """Create mount target for EFS file system."""
        try:
            # Check for existing mount target
            existing_mt = self._find_existing_mount_target(file_system_id, subnet_id)
            if existing_mt:
                mount_target_id = existing_mt['MountTargetId']
                logger.info(f"Using existing {purpose} mount target: {mount_target_id}")
                self.mount_targets[purpose] = mount_target_id
                return existing_mt
            
            # Create new mount target
            mt_response = self.efs_client.create_mount_target(
                FileSystemId=file_system_id,
                SubnetId=subnet_id,
                SecurityGroups=[security_group_id]
            )
            mount_target_id = mt_response['MountTargetId']
            self.mount_targets[purpose] = mount_target_id
            
            logger.info(f"Created {purpose} mount target: {mount_target_id}")
            return mt_response
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'MountTargetConflict':
                # Mount target already exists, find and return it
                existing_mt = self._find_existing_mount_target(file_system_id, subnet_id)
                if existing_mt:
                    self.mount_targets[purpose] = existing_mt['MountTargetId']
                    return existing_mt
            logger.error(f"Failed to create {purpose} mount target: {e}")
            raise
    
    def _find_existing_mount_target(self, file_system_id: str, subnet_id: str) -> Optional[Dict[str, Any]]:
        """Find existing mount target for file system in subnet."""
        try:
            response = self.efs_client.describe_mount_targets(FileSystemId=file_system_id)
            for mt in response['MountTargets']:
                if mt['SubnetId'] == subnet_id:
                    return mt
            return None
        except ClientError:
            return None
    
    def _create_access_point(self, file_system_id: str, name: str, path: str, 
                           uid: int, gid: int) -> Dict[str, Any]:
        """Create access point for EFS file system."""
        try:
            # Check for existing access point
            existing_ap = self._find_existing_access_point(file_system_id, name)
            if existing_ap:
                access_point_id = existing_ap['AccessPointId']
                logger.info(f"Using existing access point {name}: {access_point_id}")
                self.access_points[name] = access_point_id
                return existing_ap
            
            # Create new access point
            ap_response = self.efs_client.create_access_point(
                FileSystemId=file_system_id,
                PosixUser={
                    'Uid': uid,
                    'Gid': gid
                },
                RootDirectory={
                    'Path': path,
                    'CreationInfo': {
                        'OwnerUid': uid,
                        'OwnerGid': gid,
                        'Permissions': '0755'
                    }
                },
                Tags=[
                    {'Key': 'Name', 'Value': f"{settings.app_name}-{name}"},
                    {'Key': 'Project', 'Value': settings.app_name},
                    {'Key': 'Purpose', 'Value': name}
                ]
            )
            access_point_id = ap_response['AccessPointId']
            self.access_points[name] = access_point_id
            
            logger.info(f"Created access point {name}: {access_point_id}")
            return ap_response
            
        except ClientError as e:
            logger.error(f"Failed to create access point {name}: {e}")
            raise
    
    def _find_existing_access_point(self, file_system_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Find existing access point by name tag."""
        try:
            response = self.efs_client.describe_access_points(FileSystemId=file_system_id)
            for ap in response['AccessPoints']:
                ap_name = next((tag['Value'] for tag in ap.get('Tags', []) if tag['Key'] == 'Name'), '')
                if f"{settings.app_name}-{name}" in ap_name:
                    return ap
            return None
        except ClientError:
            return None
    
    def cleanup_efs_resources(self) -> None:
        """Clean up EFS resources (for testing/cleanup)."""
        try:
            # Delete access points first
            for name, access_point_id in self.access_points.items():
                try:
                    self.efs_client.delete_access_point(AccessPointId=access_point_id)
                    logger.info(f"Deleted access point {name}: {access_point_id}")
                except ClientError as e:
                    logger.warning(f"Failed to delete access point {name}: {e}")
            
            # Delete mount targets
            for purpose, mount_target_id in self.mount_targets.items():
                try:
                    self.efs_client.delete_mount_target(MountTargetId=mount_target_id)
                    logger.info(f"Deleted mount target {purpose}: {mount_target_id}")
                except ClientError as e:
                    logger.warning(f"Failed to delete mount target {purpose}: {e}")
            
            # Wait for mount targets to be deleted before deleting file systems
            for purpose, mount_target_id in self.mount_targets.items():
                try:
                    waiter = self.efs_client.get_waiter('mount_target_deleted')
                    waiter.wait(MountTargetId=mount_target_id)
                except ClientError:
                    pass  # May not exist
            
            # Delete file systems
            for purpose, file_system in self.file_systems.items():
                fs_id = file_system['FileSystemId']
                try:
                    self.efs_client.delete_file_system(FileSystemId=fs_id)
                    logger.info(f"Deleted file system {purpose}: {fs_id}")
                except ClientError as e:
                    logger.warning(f"Failed to delete file system {purpose}: {e}")
                    
        except Exception as e:
            logger.error(f"Error during EFS cleanup: {e}")
            raise