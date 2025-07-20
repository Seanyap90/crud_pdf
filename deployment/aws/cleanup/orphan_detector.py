"""
Detect orphaned AWS resources for cost optimization.

Identifies resources that are not tracked in deployment state or have been
left behind from failed deployments. Helps optimize costs by finding
expensive resources that can be safely cleaned up.
"""

import boto3
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from botocore.exceptions import ClientError
from deployment.aws.utils.aws_clients import (
    get_ec2_client, get_ecs_client, get_s3_client,
    get_lambda_client, get_efs_client, get_ecr_client
)
from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)

class OrphanDetector:
    """
    Detect and report orphaned AWS resources.
    
    Identifies resources that may be costing money but are no longer needed:
    - Untagged EC2 instances
    - Unused Elastic IPs
    - Detached EBS volumes
    - Empty S3 buckets with lifecycle policies
    - Unused security groups
    - Old CloudWatch log groups
    - Unused ECR repositories
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.orphaned_resources = {}
        self.cost_estimates = {}
        
    def scan_orphaned_resources(self, max_age_days: int = 7) -> Dict[str, any]:
        """
        Scan for orphaned resources across all AWS services.
        
        Args:
            max_age_days: Consider resources older than this as potential orphans
            
        Returns:
            Dict with orphaned resources by service
        """
        logger.info(f"Scanning for orphaned resources older than {max_age_days} days...")
        
        scan_functions = [
            ("EC2 Instances", self._scan_orphaned_ec2_instances),
            ("Elastic IPs", self._scan_orphaned_elastic_ips),
            ("EBS Volumes", self._scan_orphaned_ebs_volumes),
            ("Security Groups", self._scan_orphaned_security_groups),
            ("S3 Buckets", self._scan_orphaned_s3_buckets),
            ("CloudWatch Logs", self._scan_orphaned_log_groups),
            ("ECR Repositories", self._scan_orphaned_ecr_repositories),
            ("EFS File Systems", self._scan_orphaned_efs_filesystems),
            ("Lambda Functions", self._scan_orphaned_lambda_functions),
        ]
        
        for resource_type, scan_func in scan_functions:
            try:
                logger.info(f"Scanning {resource_type}...")
                orphans = scan_func(max_age_days)
                if orphans:
                    self.orphaned_resources[resource_type] = orphans
                    logger.info(f"Found {len(orphans)} orphaned {resource_type}")
                else:
                    logger.info(f"No orphaned {resource_type} found")
            except Exception as e:
                logger.error(f"Failed to scan {resource_type}: {e}")
                self.orphaned_resources[resource_type] = {"error": str(e)}
        
        return self.generate_orphan_report()
    
    def _scan_orphaned_ec2_instances(self, max_age_days: int) -> List[Dict]:
        """Scan for potentially orphaned EC2 instances."""
        ec2_client = get_ec2_client()
        orphaned_instances = []
        
        try:
            instances = ec2_client.describe_instances()
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for reservation in instances['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    state = instance['State']['Name']
                    launch_time = instance['LaunchTime'].replace(tzinfo=None)
                    
                    # Skip terminated instances
                    if state == 'terminated':
                        continue
                    
                    # Check if instance is old and potentially orphaned
                    if launch_time < cutoff_date:
                        # Check for proper tags
                        tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                        
                        # Consider orphaned if no proper project tags
                        if not any(key in tags for key in ['Project', 'Environment', 'Owner']):
                            orphaned_instances.append({
                                "instance_id": instance_id,
                                "state": state,
                                "instance_type": instance['InstanceType'],
                                "launch_time": launch_time.isoformat(),
                                "age_days": (datetime.now() - launch_time).days,
                                "tags": tags,
                                "estimated_monthly_cost": self._estimate_ec2_cost(instance['InstanceType']),
                                "reason": "No project tags and older than threshold"
                            })
            
            return orphaned_instances
            
        except Exception as e:
            logger.error(f"Failed to scan EC2 instances: {e}")
            return []
    
    def _scan_orphaned_elastic_ips(self, max_age_days: int) -> List[Dict]:
        """Scan for unassociated Elastic IPs."""
        ec2_client = get_ec2_client()
        orphaned_eips = []
        
        try:
            eips = ec2_client.describe_addresses()['Addresses']
            
            for eip in eips:
                # EIP is orphaned if not associated with an instance
                if 'InstanceId' not in eip and 'NetworkInterfaceId' not in eip:
                    orphaned_eips.append({
                        "allocation_id": eip['AllocationId'],
                        "public_ip": eip['PublicIp'],
                        "domain": eip['Domain'],
                        "estimated_monthly_cost": 3.65,  # ~$0.005/hour for unassociated EIP
                        "reason": "Elastic IP not associated with any resource"
                    })
            
            return orphaned_eips
            
        except Exception as e:
            logger.error(f"Failed to scan Elastic IPs: {e}")
            return []
    
    def _scan_orphaned_ebs_volumes(self, max_age_days: int) -> List[Dict]:
        """Scan for unattached EBS volumes."""
        ec2_client = get_ec2_client()
        orphaned_volumes = []
        
        try:
            volumes = ec2_client.describe_volumes()['Volumes']
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for volume in volumes:
                volume_id = volume['VolumeId']
                state = volume['State']
                create_time = volume['CreateTime'].replace(tzinfo=None)
                
                # Volume is orphaned if available (unattached) and old
                if state == 'available' and create_time < cutoff_date:
                    size_gb = volume['Size']
                    volume_type = volume['VolumeType']
                    
                    orphaned_volumes.append({
                        "volume_id": volume_id,
                        "size_gb": size_gb,
                        "volume_type": volume_type,
                        "create_time": create_time.isoformat(),
                        "age_days": (datetime.now() - create_time).days,
                        "estimated_monthly_cost": self._estimate_ebs_cost(size_gb, volume_type),
                        "reason": "Unattached volume older than threshold"
                    })
            
            return orphaned_volumes
            
        except Exception as e:
            logger.error(f"Failed to scan EBS volumes: {e}")
            return []
    
    def _scan_orphaned_security_groups(self, max_age_days: int) -> List[Dict]:
        """Scan for unused security groups."""
        ec2_client = get_ec2_client()
        orphaned_sgs = []
        
        try:
            security_groups = ec2_client.describe_security_groups()['SecurityGroups']
            
            # Get all network interfaces to check SG usage
            network_interfaces = ec2_client.describe_network_interfaces()['NetworkInterfaces']
            used_sg_ids = set()
            
            for ni in network_interfaces:
                for sg in ni.get('Groups', []):
                    used_sg_ids.add(sg['GroupId'])
            
            for sg in security_groups:
                sg_id = sg['GroupId']
                sg_name = sg['GroupName']
                
                # Skip default security groups
                if sg_name == 'default':
                    continue
                
                # Security group is orphaned if not used by any network interface
                if sg_id not in used_sg_ids:
                    orphaned_sgs.append({
                        "security_group_id": sg_id,
                        "group_name": sg_name,
                        "description": sg['Description'],
                        "vpc_id": sg.get('VpcId', 'N/A'),
                        "estimated_monthly_cost": 0,  # Security groups are free
                        "reason": "Security group not used by any network interface"
                    })
            
            return orphaned_sgs
            
        except Exception as e:
            logger.error(f"Failed to scan security groups: {e}")
            return []
    
    def _scan_orphaned_s3_buckets(self, max_age_days: int) -> List[Dict]:
        """Scan for potentially unused S3 buckets."""
        s3_client = get_s3_client()
        orphaned_buckets = []
        
        try:
            buckets = s3_client.list_buckets()['Buckets']
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for bucket in buckets:
                bucket_name = bucket['Name']
                create_date = bucket['CreationDate'].replace(tzinfo=None)
                
                try:
                    # Check if bucket is empty or has very old objects
                    objects = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
                    
                    if 'Contents' not in objects:
                        # Empty bucket
                        if create_date < cutoff_date:
                            orphaned_buckets.append({
                                "bucket_name": bucket_name,
                                "create_date": create_date.isoformat(),
                                "age_days": (datetime.now() - create_date).days,
                                "object_count": 0,
                                "estimated_monthly_cost": 0,
                                "reason": "Empty bucket older than threshold"
                            })
                    else:
                        # Check last modified date of objects
                        try:
                            # Get bucket size and object count for cost estimation
                            total_size = 0
                            object_count = 0
                            
                            paginator = s3_client.get_paginator('list_objects_v2')
                            pages = paginator.paginate(Bucket=bucket_name)
                            
                            latest_modified = None
                            for page in pages:
                                if 'Contents' in page:
                                    for obj in page['Contents']:
                                        object_count += 1
                                        total_size += obj['Size']
                                        if latest_modified is None or obj['LastModified'] > latest_modified:
                                            latest_modified = obj['LastModified']
                            
                            # Consider bucket orphaned if no recent activity
                            if latest_modified and latest_modified.replace(tzinfo=None) < cutoff_date:
                                orphaned_buckets.append({
                                    "bucket_name": bucket_name,
                                    "create_date": create_date.isoformat(),
                                    "last_modified": latest_modified.isoformat(),
                                    "age_days": (datetime.now() - create_date).days,
                                    "object_count": object_count,
                                    "total_size_gb": round(total_size / (1024**3), 2),
                                    "estimated_monthly_cost": self._estimate_s3_cost(total_size),
                                    "reason": "No recent object modifications"
                                })
                                
                        except ClientError:
                            # Access denied or other error, skip detailed analysis
                            pass
                            
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchBucket':
                        logger.warning(f"Failed to analyze bucket {bucket_name}: {e}")
            
            return orphaned_buckets
            
        except Exception as e:
            logger.error(f"Failed to scan S3 buckets: {e}")
            return []
    
    def _scan_orphaned_log_groups(self, max_age_days: int) -> List[Dict]:
        """Scan for old CloudWatch log groups."""
        logs_client = get_cloudwatch_client()
        orphaned_log_groups = []
        
        try:
            paginator = logs_client.get_paginator('describe_log_groups')
            pages = paginator.paginate()
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for page in pages:
                for log_group in page['logGroups']:
                    log_group_name = log_group['logGroupName']
                    creation_time = datetime.fromtimestamp(log_group['creationTime'] / 1000)
                    
                    # Check if log group is old and potentially unused
                    if creation_time < cutoff_date:
                        # Check for recent log events
                        try:
                            streams = logs_client.describe_log_streams(
                                logGroupName=log_group_name,
                                orderBy='LastEventTime',
                                descending=True,
                                limit=1
                            )
                            
                            latest_event = None
                            if streams['logStreams']:
                                latest_event_time = streams['logStreams'][0].get('lastEventTime')
                                if latest_event_time:
                                    latest_event = datetime.fromtimestamp(latest_event_time / 1000)
                            
                            # Consider orphaned if no recent events
                            if latest_event is None or latest_event < cutoff_date:
                                stored_bytes = log_group.get('storedBytes', 0)
                                orphaned_log_groups.append({
                                    "log_group_name": log_group_name,
                                    "creation_time": creation_time.isoformat(),
                                    "last_event_time": latest_event.isoformat() if latest_event else None,
                                    "age_days": (datetime.now() - creation_time).days,
                                    "stored_bytes": stored_bytes,
                                    "estimated_monthly_cost": self._estimate_cloudwatch_cost(stored_bytes),
                                    "reason": "No recent log events"
                                })
                                
                        except ClientError:
                            # Skip if can't access log streams
                            pass
            
            return orphaned_log_groups
            
        except Exception as e:
            logger.error(f"Failed to scan CloudWatch log groups: {e}")
            return []
    
    def _scan_orphaned_ecr_repositories(self, max_age_days: int) -> List[Dict]:
        """Scan for unused ECR repositories."""
        ecr_client = get_ecr_client()
        orphaned_repos = []
        
        try:
            repositories = ecr_client.describe_repositories()['repositories']
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for repo in repositories:
                repo_name = repo['repositoryName']
                created_at = repo['createdAt'].replace(tzinfo=None)
                
                try:
                    # Check for recent image pushes
                    images = ecr_client.describe_images(repositoryName=repo_name)['imageDetails']
                    
                    if not images:
                        # Empty repository
                        if created_at < cutoff_date:
                            orphaned_repos.append({
                                "repository_name": repo_name,
                                "created_at": created_at.isoformat(),
                                "age_days": (datetime.now() - created_at).days,
                                "image_count": 0,
                                "estimated_monthly_cost": 0,
                                "reason": "Empty repository older than threshold"
                            })
                    else:
                        # Check last push time
                        latest_push = max(img['imagePushedAt'] for img in images)
                        latest_push = latest_push.replace(tzinfo=None)
                        
                        if latest_push < cutoff_date:
                            total_size = sum(img['imageSizeInBytes'] for img in images)
                            orphaned_repos.append({
                                "repository_name": repo_name,
                                "created_at": created_at.isoformat(),
                                "last_push": latest_push.isoformat(),
                                "age_days": (datetime.now() - created_at).days,
                                "image_count": len(images),
                                "total_size_gb": round(total_size / (1024**3), 2),
                                "estimated_monthly_cost": self._estimate_ecr_cost(total_size),
                                "reason": "No recent image pushes"
                            })
                            
                except ClientError:
                    # Skip if can't access repository
                    pass
            
            return orphaned_repos
            
        except Exception as e:
            logger.error(f"Failed to scan ECR repositories: {e}")
            return []
    
    def _scan_orphaned_efs_filesystems(self, max_age_days: int) -> List[Dict]:
        """Scan for unused EFS file systems."""
        efs_client = get_efs_client()
        orphaned_efs = []
        
        try:
            filesystems = efs_client.describe_file_systems()['FileSystems']
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for fs in filesystems:
                fs_id = fs['FileSystemId']
                created_at = fs['CreationTime'].replace(tzinfo=None)
                
                # Check if EFS is old and potentially unused
                if created_at < cutoff_date:
                    # Check for mount targets (indicates usage)
                    mount_targets = efs_client.describe_mount_targets(FileSystemId=fs_id)['MountTargets']
                    
                    size_bytes = fs.get('SizeInBytes', {}).get('Value', 0)
                    
                    orphaned_efs.append({
                        "file_system_id": fs_id,
                        "created_at": created_at.isoformat(),
                        "age_days": (datetime.now() - created_at).days,
                        "mount_target_count": len(mount_targets),
                        "size_gb": round(size_bytes / (1024**3), 2),
                        "estimated_monthly_cost": self._estimate_efs_cost(size_bytes),
                        "reason": f"Old EFS with {len(mount_targets)} mount targets"
                    })
            
            return orphaned_efs
            
        except Exception as e:
            logger.error(f"Failed to scan EFS file systems: {e}")
            return []
    
    def _scan_orphaned_lambda_functions(self, max_age_days: int) -> List[Dict]:
        """Scan for unused Lambda functions."""
        lambda_client = get_lambda_client()
        orphaned_functions = []
        
        try:
            functions = lambda_client.list_functions()['Functions']
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            for function in functions:
                function_name = function['FunctionName']
                last_modified = datetime.strptime(function['LastModified'], '%Y-%m-%dT%H:%M:%S.%f%z').replace(tzinfo=None)
                
                # Check if function is old and potentially unused
                if last_modified < cutoff_date:
                    orphaned_functions.append({
                        "function_name": function_name,
                        "runtime": function['Runtime'],
                        "last_modified": last_modified.isoformat(),
                        "age_days": (datetime.now() - last_modified).days,
                        "memory_size": function['MemorySize'],
                        "estimated_monthly_cost": 0,  # Would need CloudWatch metrics for accurate cost
                        "reason": "Function not modified recently"
                    })
            
            return orphaned_functions
            
        except Exception as e:
            logger.error(f"Failed to scan Lambda functions: {e}")
            return []
    
    def _estimate_ec2_cost(self, instance_type: str) -> float:
        """Estimate monthly cost for EC2 instance type."""
        # Rough estimates for common instance types (USD/month for 24/7 usage)
        cost_map = {
            't2.micro': 8.5,
            't2.small': 17.0,
            't2.medium': 34.0,
            't3.micro': 7.5,
            't3.small': 15.0,
            't3.medium': 30.0,
            'm5.large': 70.0,
            'm5.xlarge': 140.0,
            'c5.large': 62.0,
            'c5.xlarge': 124.0,
        }
        return cost_map.get(instance_type, 50.0)  # Default estimate
    
    def _estimate_ebs_cost(self, size_gb: int, volume_type: str) -> float:
        """Estimate monthly cost for EBS volume."""
        # Rough estimates (USD/GB/month)
        cost_per_gb = {
            'gp2': 0.10,
            'gp3': 0.08,
            'io1': 0.125,
            'io2': 0.125,
            'st1': 0.045,
            'sc1': 0.025,
        }
        rate = cost_per_gb.get(volume_type, 0.10)
        return size_gb * rate
    
    def _estimate_s3_cost(self, size_bytes: int) -> float:
        """Estimate monthly cost for S3 storage."""
        size_gb = size_bytes / (1024**3)
        return size_gb * 0.023  # Standard storage rate
    
    def _estimate_cloudwatch_cost(self, stored_bytes: int) -> float:
        """Estimate monthly cost for CloudWatch logs."""
        size_gb = stored_bytes / (1024**3)
        return size_gb * 0.50  # Log storage rate
    
    def _estimate_ecr_cost(self, size_bytes: int) -> float:
        """Estimate monthly cost for ECR storage."""
        size_gb = size_bytes / (1024**3)
        return size_gb * 0.10  # ECR storage rate
    
    def _estimate_efs_cost(self, size_bytes: int) -> float:
        """Estimate monthly cost for EFS storage."""
        size_gb = size_bytes / (1024**3)
        return size_gb * 0.30  # EFS Standard storage rate
    
    def generate_orphan_report(self) -> Dict[str, any]:
        """Generate comprehensive orphan detection report."""
        total_orphans = sum(
            len(resources) if isinstance(resources, list) else 0 
            for resources in self.orphaned_resources.values()
        )
        
        total_estimated_cost = 0
        for resource_type, resources in self.orphaned_resources.items():
            if isinstance(resources, list):
                for resource in resources:
                    total_estimated_cost += resource.get('estimated_monthly_cost', 0)
        
        report = {
            "scan_timestamp": datetime.now().isoformat(),
            "total_orphaned_resources": total_orphans,
            "estimated_monthly_savings": round(total_estimated_cost, 2),
            "orphaned_resources": self.orphaned_resources,
            "recommendations": self._generate_recommendations(),
            "cleanup_script": self._generate_cleanup_script()
        }
        
        logger.info(f"Orphan detection completed: {total_orphans} resources found")
        logger.info(f"Estimated monthly savings: ${total_estimated_cost:.2f}")
        
        return report
    
    def _generate_recommendations(self) -> List[str]:
        """Generate cleanup recommendations based on findings."""
        recommendations = []
        
        for resource_type, resources in self.orphaned_resources.items():
            if isinstance(resources, list) and resources:
                count = len(resources)
                total_cost = sum(r.get('estimated_monthly_cost', 0) for r in resources)
                
                if total_cost > 10:  # Only recommend if significant cost
                    recommendations.append(
                        f"Consider cleaning up {count} orphaned {resource_type} "
                        f"(estimated savings: ${total_cost:.2f}/month)"
                    )
        
        return recommendations
    
    def _generate_cleanup_script(self) -> List[str]:
        """Generate AWS CLI commands for cleaning up orphaned resources."""
        commands = []
        
        for resource_type, resources in self.orphaned_resources.items():
            if isinstance(resources, list):
                for resource in resources:
                    if resource_type == "Elastic IPs":
                        commands.append(f"aws ec2 release-address --allocation-id {resource['allocation_id']}")
                    elif resource_type == "EBS Volumes":
                        commands.append(f"aws ec2 delete-volume --volume-id {resource['volume_id']}")
                    elif resource_type == "Security Groups":
                        commands.append(f"aws ec2 delete-security-group --group-id {resource['security_group_id']}")
                    # Add more cleanup commands as needed
        
        return commands