"""EFS file system management for MongoDB data and model storage."""
import logging
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

from src.files_api.config.settings import get_settings

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
    
    
    def create_database_efs(self, vpc_id: str, subnet_id: str, security_group_id: str) -> Dict[str, Any]:
        """Create EFS file system for SQLite database storage."""
        fs_name = f"{settings.app_name}-database-storage"
        
        try:
            # Check for existing file system
            existing_fs = self._find_existing_file_system(fs_name)
            if existing_fs:
                fs_id = existing_fs['FileSystemId']
                logger.info(f"Using existing database EFS: {fs_id}")
                self.file_systems['database'] = existing_fs
            else:
                # Create new file system
                fs_response = self.efs_client.create_file_system(
                    CreationToken=f"{settings.app_name}-database-{hash(fs_name) % 10000}",
                    PerformanceMode='generalPurpose',
                    ThroughputMode='bursting',
                    Tags=[
                        {'Key': 'Name', 'Value': fs_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'database-storage'}
                    ]
                )
                fs_id = fs_response['FileSystemId']
                self.file_systems['database'] = fs_response
                logger.info(f"Created database EFS: {fs_id}")
                
                # Wait for file system to become available
                logger.info("Waiting for database EFS to become available...")
                self._wait_for_file_system_available(fs_id)
                logger.info(f"Database EFS is now available: {fs_id}")
            
            # Create mount target in private subnet (for EC2 database server)
            mount_target = self._create_mount_target(
                fs_id, subnet_id, security_group_id, 'database'
            )
            
            # Create access point for database files
            access_point = self._create_access_point(
                fs_id, 'database-files', '/database', 1000, 1000
            )
            
            return {
                'file_system_id': fs_id,
                'mount_target_id': mount_target['MountTargetId'],
                'access_point_id': access_point['AccessPointId'],
                'mount_target_ip': mount_target.get('IpAddress'),
                'dns_name': f"{fs_id}.efs.{self.region}.amazonaws.com"
            }
            
        except ClientError as e:
            logger.error(f"Failed to create database EFS: {e}")
            raise

    def create_scripts_efs(self, vpc_id: str, subnet_id: str, security_group_id: str) -> Dict[str, Any]:
        """Create EFS file system for deployment scripts storage."""
        fs_name = f"{settings.app_name}-scripts-storage"
        
        try:
            # Check for existing file system
            existing_fs = self._find_existing_file_system(fs_name)
            if existing_fs:
                fs_id = existing_fs['FileSystemId']
                logger.info(f"Using existing scripts EFS: {fs_id}")
                self.file_systems['scripts'] = existing_fs
            else:
                # Create new file system
                fs_response = self.efs_client.create_file_system(
                    CreationToken=f"{settings.app_name}-scripts-{hash(fs_name) % 10000}",
                    PerformanceMode='generalPurpose',
                    ThroughputMode='bursting',
                    Tags=[
                        {'Key': 'Name', 'Value': fs_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'scripts-storage'}
                    ]
                )
                fs_id = fs_response['FileSystemId']
                self.file_systems['scripts'] = fs_response
                logger.info(f"Created scripts EFS: {fs_id}")
                
                # Wait for file system to become available
                logger.info("Waiting for scripts EFS to become available...")
                self._wait_for_file_system_available(fs_id)
                logger.info(f"Scripts EFS is now available: {fs_id}")
            
            # Create mount target in private subnet
            mount_target = self._create_mount_target(
                fs_id, subnet_id, security_group_id, 'scripts'
            )
            
            # Create access point for scripts
            access_point = self._create_access_point(
                fs_id, 'deployment-scripts', '/scripts', 1000, 1000
            )
            
            return {
                'file_system_id': fs_id,
                'mount_target_id': mount_target['MountTargetId'],
                'access_point_id': access_point['AccessPointId'],
                'mount_target_ip': mount_target.get('IpAddress'),
                'dns_name': f"{fs_id}.efs.{self.region}.amazonaws.com"
            }
            
        except ClientError as e:
            logger.error(f"Failed to create scripts EFS: {e}")
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
                # Create new file system with general purpose throughput for model loading
                fs_response = self.efs_client.create_file_system(
                    CreationToken=f"{settings.app_name}-models-{hash(fs_name) % 10000}",
                    PerformanceMode='generalPurpose',
                    ThroughputMode='bursting',
                    Tags=[
                        {'Key': 'Name', 'Value': fs_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'vlm-models'}
                    ]
                )
                fs_id = fs_response['FileSystemId']
                self.file_systems['models'] = fs_response
                logger.info(f"Created models EFS: {fs_id}")
                
                # Wait for file system to become available
                logger.info("Waiting for models EFS to become available...")
                self._wait_for_file_system_available(fs_id)
                logger.info(f"Models EFS is now available: {fs_id}")
            
            # Create mount target in private subnet (for VLM workers)
            mount_target = self._create_mount_target(
                fs_id, subnet_id, security_group_id, 'models'
            )
            
            # Create single access point for all models (matches Docker volume structure)
            models_ap = self._create_access_point(
                fs_id, 'models-cache', '/cache', 1000, 1000
            )
            
            return {
                'file_system_id': fs_id,
                'mount_target_id': mount_target['MountTargetId'],
                'access_point_id': models_ap['AccessPointId'],
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
                self._wait_for_mount_target_available(mount_target_id)
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
                    self._wait_for_efs_resource_state(
                        resource_type='mount_target',
                        resource_id=mount_target_id,
                        target_state='deleted',
                        describe_method='describe_mount_targets',
                        id_key='MountTargetId',
                        collection_key='MountTargets'
                    )
                except Exception:
                    pass  # May not exist or already deleted
            
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
    
    def _wait_for_file_system_available(self, file_system_id: str, max_attempts: int = 20, delay: int = 10) -> None:
        """Wait for EFS file system to become available (custom implementation since no built-in waiter exists)."""
        import time
        
        for attempt in range(max_attempts):
            try:
                response = self.efs_client.describe_file_systems(FileSystemId=file_system_id)
                file_systems = response.get('FileSystems', [])
                
                if not file_systems:
                    raise ClientError(
                        error_response={'Error': {'Code': 'FileSystemNotFound'}},
                        operation_name='DescribeFileSystems'
                    )
                
                lifecycle_state = file_systems[0]['LifeCycleState']
                logger.debug(f"EFS {file_system_id} lifecycle state: {lifecycle_state} (attempt {attempt + 1}/{max_attempts})")
                
                if lifecycle_state == 'available':
                    return
                elif lifecycle_state in ['deleting', 'deleted']:
                    raise ClientError(
                        error_response={'Error': {'Code': 'FileSystemDeleted'}},
                        operation_name='DescribeFileSystems'
                    )
                elif lifecycle_state == 'creating':
                    # Continue waiting
                    if attempt < max_attempts - 1:  # Don't sleep on last attempt
                        time.sleep(delay)
                    continue
                else:
                    logger.warning(f"Unknown EFS lifecycle state: {lifecycle_state}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
                        
            except ClientError as e:
                if e.response['Error']['Code'] in ['FileSystemNotFound', 'FileSystemDeleted']:
                    raise
                logger.warning(f"Error checking EFS state (attempt {attempt + 1}): {e}")
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        # If we get here, we've exceeded max attempts
        raise Exception(f"EFS file system {file_system_id} did not become available within {max_attempts * delay} seconds")
    
    def _wait_for_mount_target_available(self, mount_target_id: str) -> None:
        """Wait for mount target to become available."""
        self._wait_for_efs_resource_state(
            resource_type='mount_target',
            resource_id=mount_target_id,
            target_state='available',
            describe_method='describe_mount_targets',
            id_key='MountTargetId',
            collection_key='MountTargets'
        )
    
    def _wait_for_efs_resource_state(self, resource_type: str, resource_id: str, target_state: str,
                                   describe_method: str, id_key: str, collection_key: str,
                                   max_attempts: int = 20, delay: int = 10) -> None:
        """Generic waiter for EFS resources (file systems, mount targets, etc.)."""
        import time
        
        for attempt in range(max_attempts):
            try:
                # Dynamically call the appropriate describe method
                describe_func = getattr(self.efs_client, describe_method)
                kwargs = {id_key: resource_id}
                response = describe_func(**kwargs)
                
                resources = response.get(collection_key, [])
                if not resources:
                    raise ClientError(
                        error_response={'Error': {'Code': f'{resource_type.title()}NotFound'}},
                        operation_name=describe_method
                    )
                
                lifecycle_state = resources[0]['LifeCycleState']
                logger.debug(f"{resource_type} {resource_id} state: {lifecycle_state} (attempt {attempt + 1}/{max_attempts})")
                
                if lifecycle_state == target_state:
                    return
                elif lifecycle_state in ['deleting', 'deleted']:
                    raise ClientError(
                        error_response={'Error': {'Code': f'{resource_type.title()}Deleted'}},
                        operation_name=describe_method
                    )
                elif lifecycle_state == 'creating':
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
                    continue
                else:
                    logger.warning(f"Unknown {resource_type} lifecycle state: {lifecycle_state}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
                        
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if 'NotFound' in error_code or 'Deleted' in error_code:
                    raise
                logger.warning(f"Error checking {resource_type} state (attempt {attempt + 1}): {e}")
                if attempt < max_attempts - 1:
                    time.sleep(delay)
        
        raise Exception(f"{resource_type} {resource_id} did not reach {target_state} state within {max_attempts * delay} seconds")