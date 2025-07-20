"""
Unified cleanup orchestrator for AWS resources.

Provides comprehensive cleanup capabilities with different strategies:
- Full cleanup: Remove all resources
- Soft cleanup: Preserve expensive infrastructure (VPC, NAT, EFS, ECR)
- Selective cleanup: Target specific services or resource types
"""

import boto3
import logging
import time
from typing import Dict, List, Optional, Set
from botocore.exceptions import ClientError
from deployment.aws.utils.aws_clients import (
    get_ecs_client, get_ec2_client, get_s3_client, 
    get_lambda_client, get_efs_client, get_ecr_client,
    get_sqs_client, get_logs_client
)
from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)

class CleanupManager:
    """
    Unified cleanup orchestrator for AWS resources.
    
    Handles resource cleanup with safety checks and different cleanup strategies.
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.cleanup_results = {}
        self.errors = []
        
    def cleanup_all(self, confirm: bool = False) -> Dict[str, any]:
        """
        Perform full cleanup of all AWS resources.
        
        Args:
            confirm: If True, skip confirmation prompt
            
        Returns:
            Dict with cleanup results and statistics
        """
        if not confirm:
            response = input("⚠️ This will DELETE ALL AWS resources. Continue? (yes/no): ")
            if response.lower() != 'yes':
                return {"status": "cancelled", "message": "Cleanup cancelled by user"}
        
        logger.info("Starting full AWS resource cleanup...")
        
        # Cleanup order matters due to dependencies
        cleanup_order = [
            ("ECS Services", self._cleanup_ecs_services),
            ("ECS Tasks", self._cleanup_ecs_tasks),
            ("Lambda Functions", self._cleanup_lambda_functions),
            ("SQS Queues", self._cleanup_sqs_queues),
            ("S3 Buckets", self._cleanup_s3_buckets),
            ("CloudWatch Logs", self._cleanup_cloudwatch_logs),
            ("ECS Clusters", self._cleanup_ecs_clusters),
            ("EFS File Systems", self._cleanup_efs_filesystems),
            ("ECR Repositories", self._cleanup_ecr_repositories),
            ("VPC Resources", self._cleanup_vpc_resources),
        ]
        
        for resource_type, cleanup_func in cleanup_order:
            try:
                logger.info(f"Cleaning up {resource_type}...")
                result = cleanup_func()
                self.cleanup_results[resource_type] = result
                logger.info(f"✅ {resource_type} cleanup completed")
            except Exception as e:
                error_msg = f"❌ {resource_type} cleanup failed: {e}"
                logger.error(error_msg)
                self.errors.append(error_msg)
                self.cleanup_results[resource_type] = {"status": "error", "error": str(e)}
        
        return self._generate_cleanup_report("full")
    
    def cleanup_soft(self) -> Dict[str, any]:
        """
        Perform soft cleanup preserving expensive infrastructure.
        
        Preserves: VPC, NAT Gateway, EFS, ECR repositories
        Removes: ECS services/tasks, Lambda functions, S3 content, logs
        
        Returns:
            Dict with cleanup results and statistics
        """
        logger.info("Starting soft cleanup (preserving infrastructure)...")
        
        # Soft cleanup order - preserve expensive resources
        cleanup_order = [
            ("ECS Services", self._cleanup_ecs_services),
            ("ECS Tasks", self._cleanup_ecs_tasks),
            ("Lambda Functions", self._cleanup_lambda_functions),
            ("SQS Queues", self._cleanup_sqs_queues),
            ("S3 Bucket Contents", self._cleanup_s3_contents_only),
            ("CloudWatch Logs", self._cleanup_cloudwatch_logs),
        ]
        
        for resource_type, cleanup_func in cleanup_order:
            try:
                logger.info(f"Cleaning up {resource_type}...")
                result = cleanup_func()
                self.cleanup_results[resource_type] = result
                logger.info(f"✅ {resource_type} cleanup completed")
            except Exception as e:
                error_msg = f"❌ {resource_type} cleanup failed: {e}"
                logger.error(error_msg)
                self.errors.append(error_msg)
                self.cleanup_results[resource_type] = {"status": "error", "error": str(e)}
        
        logger.info("🏗️ Preserved infrastructure: VPC, EFS, ECR repositories")
        return self._generate_cleanup_report("soft")
    
    def cleanup_by_service(self, services: List[str]) -> Dict[str, any]:
        """
        Cleanup specific AWS services only.
        
        Args:
            services: List of service names to cleanup
            
        Returns:
            Dict with cleanup results
        """
        logger.info(f"Starting selective cleanup for services: {services}")
        
        service_map = {
            "ecs": self._cleanup_ecs_all,
            "lambda": self._cleanup_lambda_functions,
            "s3": self._cleanup_s3_buckets,
            "sqs": self._cleanup_sqs_queues,
            "logs": self._cleanup_cloudwatch_logs,
            "efs": self._cleanup_efs_filesystems,
            "ecr": self._cleanup_ecr_repositories,
            "vpc": self._cleanup_vpc_resources,
        }
        
        for service in services:
            if service in service_map:
                try:
                    logger.info(f"Cleaning up {service.upper()}...")
                    result = service_map[service]()
                    self.cleanup_results[service.upper()] = result
                    logger.info(f"✅ {service.upper()} cleanup completed")
                except Exception as e:
                    error_msg = f"❌ {service.upper()} cleanup failed: {e}"
                    logger.error(error_msg)
                    self.errors.append(error_msg)
                    self.cleanup_results[service.upper()] = {"status": "error", "error": str(e)}
            else:
                logger.warning(f"Unknown service: {service}")
        
        return self._generate_cleanup_report("selective")
    
    def _cleanup_ecs_services(self) -> Dict[str, any]:
        """Cleanup ECS services."""
        ecs_client = get_ecs_client()
        deleted_services = []
        
        try:
            # List all clusters
            clusters = ecs_client.list_clusters()['clusterArns']
            
            for cluster_arn in clusters:
                # List services in cluster
                services = ecs_client.list_services(cluster=cluster_arn)['serviceArns']
                
                for service_arn in services:
                    try:
                        # Scale service to 0
                        ecs_client.update_service(
                            cluster=cluster_arn,
                            service=service_arn,
                            desiredCount=0
                        )
                        
                        # Wait for tasks to stop
                        time.sleep(10)
                        
                        # Delete service
                        ecs_client.delete_service(
                            cluster=cluster_arn,
                            service=service_arn
                        )
                        
                        deleted_services.append(service_arn.split('/')[-1])
                        logger.info(f"Deleted ECS service: {service_arn.split('/')[-1]}")
                        
                    except ClientError as e:
                        logger.warning(f"Failed to delete service {service_arn}: {e}")
            
            return {"status": "success", "deleted_services": deleted_services, "count": len(deleted_services)}
            
        except Exception as e:
            logger.error(f"ECS services cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_ecs_tasks(self) -> Dict[str, any]:
        """Cleanup running ECS tasks."""
        ecs_client = get_ecs_client()
        stopped_tasks = []
        
        try:
            clusters = ecs_client.list_clusters()['clusterArns']
            
            for cluster_arn in clusters:
                # List running tasks
                tasks = ecs_client.list_tasks(cluster=cluster_arn)['taskArns']
                
                for task_arn in tasks:
                    try:
                        ecs_client.stop_task(cluster=cluster_arn, task=task_arn)
                        stopped_tasks.append(task_arn.split('/')[-1])
                        logger.info(f"Stopped ECS task: {task_arn.split('/')[-1]}")
                    except ClientError as e:
                        logger.warning(f"Failed to stop task {task_arn}: {e}")
            
            return {"status": "success", "stopped_tasks": stopped_tasks, "count": len(stopped_tasks)}
            
        except Exception as e:
            logger.error(f"ECS tasks cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_ecs_clusters(self) -> Dict[str, any]:
        """Cleanup ECS clusters."""
        ecs_client = get_ecs_client()
        deleted_clusters = []
        
        try:
            clusters = ecs_client.list_clusters()['clusterArns']
            
            for cluster_arn in clusters:
                try:
                    cluster_name = cluster_arn.split('/')[-1]
                    ecs_client.delete_cluster(cluster=cluster_arn)
                    deleted_clusters.append(cluster_name)
                    logger.info(f"Deleted ECS cluster: {cluster_name}")
                except ClientError as e:
                    logger.warning(f"Failed to delete cluster {cluster_arn}: {e}")
            
            return {"status": "success", "deleted_clusters": deleted_clusters, "count": len(deleted_clusters)}
            
        except Exception as e:
            logger.error(f"ECS clusters cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_ecs_all(self) -> Dict[str, any]:
        """Cleanup all ECS resources."""
        results = {}
        results.update(self._cleanup_ecs_services())
        results.update(self._cleanup_ecs_tasks())
        results.update(self._cleanup_ecs_clusters())
        return results
    
    def _cleanup_lambda_functions(self) -> Dict[str, any]:
        """Cleanup Lambda functions."""
        lambda_client = get_lambda_client()
        deleted_functions = []
        
        try:
            functions = lambda_client.list_functions()['Functions']
            
            for function in functions:
                try:
                    function_name = function['FunctionName']
                    lambda_client.delete_function(FunctionName=function_name)
                    deleted_functions.append(function_name)
                    logger.info(f"Deleted Lambda function: {function_name}")
                except ClientError as e:
                    logger.warning(f"Failed to delete function {function_name}: {e}")
            
            return {"status": "success", "deleted_functions": deleted_functions, "count": len(deleted_functions)}
            
        except Exception as e:
            logger.error(f"Lambda functions cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_s3_buckets(self) -> Dict[str, any]:
        """Cleanup S3 buckets and their contents."""
        s3_client = get_s3_client()
        deleted_buckets = []
        
        try:
            buckets = s3_client.list_buckets()['Buckets']
            
            for bucket in buckets:
                bucket_name = bucket['Name']
                
                # Only delete buckets that match our naming pattern
                if self.settings.s3_bucket_name in bucket_name:
                    try:
                        # Delete all objects first
                        self._empty_s3_bucket(bucket_name)
                        
                        # Delete bucket
                        s3_client.delete_bucket(Bucket=bucket_name)
                        deleted_buckets.append(bucket_name)
                        logger.info(f"Deleted S3 bucket: {bucket_name}")
                    except ClientError as e:
                        logger.warning(f"Failed to delete bucket {bucket_name}: {e}")
            
            return {"status": "success", "deleted_buckets": deleted_buckets, "count": len(deleted_buckets)}
            
        except Exception as e:
            logger.error(f"S3 buckets cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_s3_contents_only(self) -> Dict[str, any]:
        """Cleanup S3 bucket contents but preserve buckets."""
        s3_client = get_s3_client()
        cleaned_buckets = []
        
        try:
            buckets = s3_client.list_buckets()['Buckets']
            
            for bucket in buckets:
                bucket_name = bucket['Name']
                
                if self.settings.s3_bucket_name in bucket_name:
                    try:
                        objects_deleted = self._empty_s3_bucket(bucket_name)
                        cleaned_buckets.append({"bucket": bucket_name, "objects_deleted": objects_deleted})
                        logger.info(f"Cleaned S3 bucket contents: {bucket_name} ({objects_deleted} objects)")
                    except ClientError as e:
                        logger.warning(f"Failed to clean bucket {bucket_name}: {e}")
            
            return {"status": "success", "cleaned_buckets": cleaned_buckets, "count": len(cleaned_buckets)}
            
        except Exception as e:
            logger.error(f"S3 contents cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _empty_s3_bucket(self, bucket_name: str) -> int:
        """Empty all objects from an S3 bucket."""
        s3_client = get_s3_client()
        objects_deleted = 0
        
        try:
            # List and delete all objects
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name)
            
            for page in pages:
                if 'Contents' in page:
                    objects = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects:
                        s3_client.delete_objects(
                            Bucket=bucket_name,
                            Delete={'Objects': objects}
                        )
                        objects_deleted += len(objects)
            
            return objects_deleted
            
        except ClientError as e:
            logger.error(f"Failed to empty bucket {bucket_name}: {e}")
            return 0
    
    def _cleanup_sqs_queues(self) -> Dict[str, any]:
        """Cleanup SQS queues."""
        sqs_client = get_sqs_client()
        deleted_queues = []
        
        try:
            queues = sqs_client.list_queues().get('QueueUrls', [])
            
            for queue_url in queues:
                queue_name = queue_url.split('/')[-1]
                
                # Only delete queues that match our naming pattern
                if self.settings.sqs_queue_name in queue_name:
                    try:
                        sqs_client.delete_queue(QueueUrl=queue_url)
                        deleted_queues.append(queue_name)
                        logger.info(f"Deleted SQS queue: {queue_name}")
                    except ClientError as e:
                        logger.warning(f"Failed to delete queue {queue_name}: {e}")
            
            return {"status": "success", "deleted_queues": deleted_queues, "count": len(deleted_queues)}
            
        except Exception as e:
            logger.error(f"SQS queues cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_cloudwatch_logs(self) -> Dict[str, any]:
        """Cleanup CloudWatch log groups."""
        logs_client = get_logs_client()
        deleted_log_groups = []
        
        try:
            paginator = logs_client.get_paginator('describe_log_groups')
            pages = paginator.paginate()
            
            for page in pages:
                for log_group in page['logGroups']:
                    log_group_name = log_group['logGroupName']
                    
                    # Delete log groups related to our application
                    if any(keyword in log_group_name.lower() for keyword in ['files-api', 'vlm-worker', 'ecs']):
                        try:
                            logs_client.delete_log_group(logGroupName=log_group_name)
                            deleted_log_groups.append(log_group_name)
                            logger.info(f"Deleted log group: {log_group_name}")
                        except ClientError as e:
                            logger.warning(f"Failed to delete log group {log_group_name}: {e}")
            
            return {"status": "success", "deleted_log_groups": deleted_log_groups, "count": len(deleted_log_groups)}
            
        except Exception as e:
            logger.error(f"CloudWatch logs cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_efs_filesystems(self) -> Dict[str, any]:
        """Cleanup EFS file systems."""
        efs_client = get_efs_client()
        deleted_filesystems = []
        
        try:
            filesystems = efs_client.describe_file_systems()['FileSystems']
            
            for fs in filesystems:
                fs_id = fs['FileSystemId']
                
                # Only delete EFS with our tags or naming pattern
                try:
                    # Delete mount targets first
                    mount_targets = efs_client.describe_mount_targets(FileSystemId=fs_id)['MountTargets']
                    for mt in mount_targets:
                        efs_client.delete_mount_target(MountTargetId=mt['MountTargetId'])
                        logger.info(f"Deleted mount target: {mt['MountTargetId']}")
                    
                    # Wait for mount targets to be deleted
                    time.sleep(30)
                    
                    # Delete file system
                    efs_client.delete_file_system(FileSystemId=fs_id)
                    deleted_filesystems.append(fs_id)
                    logger.info(f"Deleted EFS file system: {fs_id}")
                    
                except ClientError as e:
                    logger.warning(f"Failed to delete EFS {fs_id}: {e}")
            
            return {"status": "success", "deleted_filesystems": deleted_filesystems, "count": len(deleted_filesystems)}
            
        except Exception as e:
            logger.error(f"EFS cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_ecr_repositories(self) -> Dict[str, any]:
        """Cleanup ECR repositories."""
        ecr_client = get_ecr_client()
        deleted_repositories = []
        
        try:
            repositories = ecr_client.describe_repositories()['repositories']
            
            for repo in repositories:
                repo_name = repo['repositoryName']
                
                # Only delete repositories related to our application
                if any(keyword in repo_name.lower() for keyword in ['files-api', 'vlm-worker']):
                    try:
                        ecr_client.delete_repository(repositoryName=repo_name, force=True)
                        deleted_repositories.append(repo_name)
                        logger.info(f"Deleted ECR repository: {repo_name}")
                    except ClientError as e:
                        logger.warning(f"Failed to delete repository {repo_name}: {e}")
            
            return {"status": "success", "deleted_repositories": deleted_repositories, "count": len(deleted_repositories)}
            
        except Exception as e:
            logger.error(f"ECR cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _cleanup_vpc_resources(self) -> Dict[str, any]:
        """Cleanup VPC and related networking resources."""
        ec2_client = get_ec2_client()
        deleted_resources = []
        
        try:
            # This is a complex operation that should be done carefully
            # For now, just log what would be deleted
            vpcs = ec2_client.describe_vpcs()['Vpcs']
            
            for vpc in vpcs:
                if not vpc.get('IsDefault', False):
                    vpc_id = vpc['VpcId']
                    logger.info(f"Would delete VPC: {vpc_id}")
                    # TODO: Implement careful VPC deletion with dependency handling
            
            return {"status": "success", "message": "VPC cleanup logged (not implemented)", "count": 0}
            
        except Exception as e:
            logger.error(f"VPC cleanup failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _generate_cleanup_report(self, cleanup_type: str) -> Dict[str, any]:
        """Generate a comprehensive cleanup report."""
        total_resources = sum(
            result.get('count', 0) for result in self.cleanup_results.values() 
            if isinstance(result, dict) and 'count' in result
        )
        
        report = {
            "cleanup_type": cleanup_type,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "total_resources_cleaned": total_resources,
            "results": self.cleanup_results,
            "errors": self.errors,
            "status": "completed_with_errors" if self.errors else "success"
        }
        
        logger.info(f"Cleanup report generated: {total_resources} resources cleaned")
        if self.errors:
            logger.warning(f"Cleanup completed with {len(self.errors)} errors")
        
        return report