"""
IAM Verification Module for AWS Deployment

This module provides IAM role verification capabilities for Lambda functions,
ECS tasks, and other AWS services. It validates that the correct IAM roles
are being used and provides comprehensive identity verification.

Key features:
- Lambda function IAM role verification
- ECS task role verification  
- STS caller identity validation
- Role name pattern matching
- Security token validation
"""

import json
import logging
import os
from typing import Dict, Optional, List
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

logger = logging.getLogger(__name__)


class IAMVerifier:
    """Class for verifying IAM roles and permissions across AWS services."""
    
    def __init__(self, expected_role_pattern: str = None):
        """
        Initialize IAM verifier.
        
        Args:
            expected_role_pattern: Expected role name pattern (e.g., 'fastapi-app-*-role')
        """
        self.expected_role_pattern = expected_role_pattern or "fastapi-app-*-role"
        self.session = boto3.Session()
        self.sts_client = self.session.client('sts')
        
    def get_caller_identity(self) -> Dict[str, str]:
        """
        Get the current caller's identity using STS.
        
        Returns:
            Dictionary containing Account, Arn, and UserId
            
        Raises:
            ClientError: If STS call fails
        """
        try:
            identity = self.sts_client.get_caller_identity()
            logger.info(f"Caller Identity: {json.dumps(identity, indent=2)}")
            return identity
        except ClientError as e:
            logger.error(f"Failed to get caller identity: {e}")
            raise
    
    def verify_lambda_role(self, function_name: str, expected_role_name: str = None) -> bool:
        """
        Verify that a Lambda function is using the correct IAM role.
        
        Args:
            function_name: Name of the Lambda function
            expected_role_name: Expected role name (auto-detected if not provided)
            
        Returns:
            True if role is valid, False otherwise
        """
        try:
            # Get caller identity
            identity = self.get_caller_identity()
            actual_role_arn = identity['Arn']
            account_id = identity['Account']
            
            # Ensure the ARN is in the assumed-role format (Lambda execution context)
            if not actual_role_arn.startswith(f"arn:aws:sts::{account_id}:assumed-role/"):
                logger.error(f"Unexpected IAM role format. Expected assumed-role ARN, got: {actual_role_arn}")
                return False
            
            # Extract role name from the ARN 
            role_name = actual_role_arn.split('/')[1]
            
            # Determine expected role name
            if not expected_role_name:
                expected_role_name = f"fastapi-app-lambda-execution-role"
            
            # Verify role name matches
            if role_name != expected_role_name:
                logger.error(f"IAM role mismatch. Expected: {expected_role_name}, Got: {role_name}")
                return False
                
            logger.info(f"✅ Lambda function {function_name} using correct IAM role: {role_name}")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"IAM verification failed for {function_name}: {error_code} - {e}")
            
            if error_code == 'InvalidClientTokenId':
                logger.error("Invalid security token. Check Lambda IAM role or credentials.")
            
            return False
        except Exception as e:
            logger.error(f"Unexpected error verifying Lambda role for {function_name}: {e}")
            return False
    
    def verify_ecs_roles(self, cluster_name: str, service_names: List[str] = None) -> bool:
        """
        Verify that ECS services are using the correct IAM roles.
        
        Args:
            cluster_name: Name of the ECS cluster
            service_names: List of service names to verify (optional)
            
        Returns:
            True if all roles are valid, False otherwise
        """
        try:
            ecs_client = self.session.client('ecs')
            
            # List services if not provided
            if not service_names:
                response = ecs_client.list_services(cluster=cluster_name)
                service_arns = response.get('serviceArns', [])
                service_names = [arn.split('/')[-1] for arn in service_arns]
            
            if not service_names:
                logger.warning(f"No ECS services found in cluster {cluster_name}")
                return True
            
            all_valid = True
            
            for service_name in service_names:
                try:
                    # Describe the service to get task definition
                    response = ecs_client.describe_services(
                        cluster=cluster_name,
                        services=[service_name]
                    )
                    
                    if not response['services']:
                        logger.warning(f"Service {service_name} not found in cluster {cluster_name}")
                        continue
                    
                    service = response['services'][0]
                    task_definition_arn = service['taskDefinition']
                    
                    # Get task definition details
                    task_def_response = ecs_client.describe_task_definition(
                        taskDefinition=task_definition_arn
                    )
                    
                    task_def = task_def_response['taskDefinition']
                    execution_role_arn = task_def.get('executionRoleArn')
                    task_role_arn = task_def.get('taskRoleArn')
                    
                    # Verify execution role
                    if execution_role_arn:
                        if not self._verify_role_arn(execution_role_arn, 'execution'):
                            logger.error(f"Invalid execution role for service {service_name}: {execution_role_arn}")
                            all_valid = False
                    
                    # Verify task role
                    if task_role_arn:
                        if not self._verify_role_arn(task_role_arn, 'task'):
                            logger.error(f"Invalid task role for service {service_name}: {task_role_arn}")
                            all_valid = False
                    
                    if execution_role_arn or task_role_arn:
                        logger.info(f"✅ ECS service {service_name} IAM roles verified")
                    else:
                        logger.warning(f"⚠️ ECS service {service_name} has no IAM roles configured")
                        
                except ClientError as e:
                    logger.error(f"Failed to verify roles for ECS service {service_name}: {e}")
                    all_valid = False
            
            return all_valid
            
        except ClientError as e:
            logger.error(f"Failed to verify ECS roles for cluster {cluster_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error verifying ECS roles: {e}")
            return False
    
    def _verify_role_arn(self, role_arn: str, role_type: str) -> bool:
        """
        Verify that a role ARN matches expected patterns.
        
        Args:
            role_arn: The role ARN to verify
            role_type: Type of role ('execution', 'task', 'lambda')
            
        Returns:
            True if role ARN is valid, False otherwise
        """
        try:
            # Extract role name from ARN
            if not role_arn.startswith('arn:aws:iam::'):
                logger.error(f"Invalid role ARN format: {role_arn}")
                return False
            
            role_name = role_arn.split('/')[-1]
            
            # Define expected role patterns
            expected_patterns = {
                'execution': ['fastapi-app-ecs-execution-role', 'fastapi-app-lambda-execution-role'],
                'task': ['fastapi-app-ecs-task-role'],
                'lambda': ['fastapi-app-lambda-execution-role']
            }
            
            valid_patterns = expected_patterns.get(role_type, [])
            
            # Check if role name matches any expected pattern
            for pattern in valid_patterns:
                if role_name == pattern or self._matches_pattern(role_name, pattern):
                    return True
            
            logger.error(f"Role name {role_name} does not match expected patterns for {role_type}: {valid_patterns}")
            return False
            
        except Exception as e:
            logger.error(f"Error verifying role ARN {role_arn}: {e}")
            return False
    
    def _matches_pattern(self, role_name: str, pattern: str) -> bool:
        """
        Check if role name matches a pattern (supports wildcards).
        
        Args:
            role_name: Actual role name
            pattern: Expected pattern (may contain *)
            
        Returns:
            True if matches, False otherwise
        """
        if '*' not in pattern:
            return role_name == pattern
        
        # Simple wildcard matching
        pattern_parts = pattern.split('*')
        if len(pattern_parts) == 2:
            prefix, suffix = pattern_parts
            return role_name.startswith(prefix) and role_name.endswith(suffix)
        
        # For more complex patterns, use exact match for now
        return role_name == pattern.replace('*', '')
    
    def test_service_connectivity(self, service_configs: List[Dict]) -> Dict[str, bool]:
        """
        Test connectivity to various AWS services with current credentials.
        
        Args:
            service_configs: List of service configurations to test
            Example: [{'service': 'sqs', 'action': 'list_queues'}, 
                     {'service': 's3', 'action': 'list_buckets'}]
        
        Returns:
            Dictionary mapping service names to success status
        """
        results = {}
        
        for config in service_configs:
            service_name = config['service']
            action = config.get('action', 'list_queues')
            
            try:
                client = self.session.client(service_name)
                
                # Test basic service connectivity
                if service_name == 'sqs':
                    client.list_queues()
                elif service_name == 's3':
                    client.list_buckets()
                elif service_name == 'ecs':
                    client.list_clusters()
                elif service_name == 'lambda':
                    client.list_functions()
                else:
                    # Generic approach - try to call the specified action
                    getattr(client, action)()
                
                results[service_name] = True
                logger.info(f"✅ {service_name.upper()} connectivity verified")
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                logger.error(f"❌ {service_name.upper()} connectivity failed: {error_code}")
                results[service_name] = False
            except Exception as e:
                logger.error(f"❌ {service_name.upper()} connectivity test failed: {e}")
                results[service_name] = False
        
        return results
    
    def generate_verification_report(self, lambda_functions: List[str] = None, 
                                   ecs_cluster: str = None) -> Dict:
        """
        Generate a comprehensive IAM verification report.
        
        Args:
            lambda_functions: List of Lambda function names to verify
            ecs_cluster: ECS cluster name to verify
            
        Returns:
            Dictionary containing verification results
        """
        report = {
            'timestamp': str(boto3.Session().region_name),
            'caller_identity': {},
            'lambda_functions': {},
            'ecs_services': {},
            'service_connectivity': {},
            'overall_status': 'UNKNOWN'
        }
        
        try:
            # Get caller identity
            report['caller_identity'] = self.get_caller_identity()
            
            # Verify Lambda functions
            if lambda_functions:
                for func_name in lambda_functions:
                    report['lambda_functions'][func_name] = self.verify_lambda_role(func_name)
            
            # Verify ECS services
            if ecs_cluster:
                report['ecs_services']['cluster'] = ecs_cluster
                report['ecs_services']['valid'] = self.verify_ecs_roles(ecs_cluster)
            
            # Test service connectivity
            service_configs = [
                {'service': 'sts'},
                {'service': 'sqs'},
                {'service': 's3'},
                {'service': 'ecs'},
                {'service': 'lambda'}
            ]
            report['service_connectivity'] = self.test_service_connectivity(service_configs)
            
            # Determine overall status
            all_lambda_valid = all(report['lambda_functions'].values()) if report['lambda_functions'] else True
            ecs_valid = report['ecs_services'].get('valid', True)
            all_services_connected = all(report['service_connectivity'].values())
            
            if all_lambda_valid and ecs_valid and all_services_connected:
                report['overall_status'] = 'PASS'
            else:
                report['overall_status'] = 'FAIL'
            
        except Exception as e:
            logger.error(f"Error generating verification report: {e}")
            report['error'] = str(e)
            report['overall_status'] = 'ERROR'
        
        return report


# Convenience functions for direct usage
def verify_lambda_iam_role(function_name: str, expected_role_name: str = None) -> bool:
    """
    Convenience function to verify a single Lambda function's IAM role.
    
    Args:
        function_name: Name of the Lambda function
        expected_role_name: Expected role name (auto-detected if not provided)
        
    Returns:
        True if role is valid, False otherwise
    """
    verifier = IAMVerifier()
    return verifier.verify_lambda_role(function_name, expected_role_name)


def verify_ecs_iam_roles(cluster_name: str, service_names: List[str] = None) -> bool:
    """
    Convenience function to verify ECS service IAM roles.
    
    Args:
        cluster_name: Name of the ECS cluster
        service_names: List of service names to verify (optional)
        
    Returns:
        True if all roles are valid, False otherwise
    """
    verifier = IAMVerifier()
    return verifier.verify_ecs_roles(cluster_name, service_names)


def generate_iam_verification_report(lambda_functions: List[str] = None,
                                   ecs_cluster: str = None) -> Dict:
    """
    Convenience function to generate a comprehensive IAM verification report.
    
    Args:
        lambda_functions: List of Lambda function names to verify
        ecs_cluster: ECS cluster name to verify
        
    Returns:
        Dictionary containing verification results
    """
    verifier = IAMVerifier()
    return verifier.generate_verification_report(lambda_functions, ecs_cluster)