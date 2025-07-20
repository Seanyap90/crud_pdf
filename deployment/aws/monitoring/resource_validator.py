"""
Pre-deployment resource validation.

Validates AWS credentials, quotas, and prerequisites before deployment
to catch issues early and provide helpful error messages.
"""

import boto3
import json
from typing import Dict, List, Optional, Any, Tuple
from botocore.exceptions import ClientError, NoCredentialsError
from files_api.config.settings import get_settings


class ResourceValidator:
    """Validate AWS resources and prerequisites before deployment."""
    
    def __init__(self):
        self.settings = get_settings()
        self.validation_results = {
            'valid': True,
            'warnings': [],
            'errors': [],
            'checks': {}
        }
    
    def validate_credentials(self) -> bool:
        """Validate AWS credentials and basic access."""
        try:
            sts_client = boto3.client('sts', region_name=self.settings.aws_region)
            identity = sts_client.get_caller_identity()
            
            self.validation_results['checks']['credentials'] = {
                'status': 'valid',
                'account_id': identity['Account'],
                'user_arn': identity['Arn'],
                'user_id': identity['UserId']
            }
            
            return True
            
        except NoCredentialsError:
            self.validation_results['valid'] = False
            self.validation_results['errors'].append(
                "AWS credentials not configured. Please run 'aws configure' or set environment variables."
            )
            self.validation_results['checks']['credentials'] = {'status': 'error', 'error': 'No credentials'}
            return False
            
        except ClientError as e:
            self.validation_results['valid'] = False
            error_msg = f"AWS credentials invalid: {e.response['Error']['Message']}"
            self.validation_results['errors'].append(error_msg)
            self.validation_results['checks']['credentials'] = {'status': 'error', 'error': error_msg}
            return False
    
    def check_service_limits(self) -> bool:
        """Check AWS service limits and quotas."""
        all_limits_ok = True
        
        # Check ECS limits
        ecs_ok = self._check_ecs_limits()
        if not ecs_ok:
            all_limits_ok = False
        
        # Check Lambda limits
        lambda_ok = self._check_lambda_limits()
        if not lambda_ok:
            all_limits_ok = False
        
        # Check VPC limits
        vpc_ok = self._check_vpc_limits()
        if not vpc_ok:
            all_limits_ok = False
        
        # Check EFS limits
        efs_ok = self._check_efs_limits()
        if not efs_ok:
            all_limits_ok = False
        
        return all_limits_ok
    
    def _check_ecs_limits(self) -> bool:
        """Check ECS service limits."""
        try:
            ecs_client = boto3.client('ecs', region_name=self.settings.aws_region)
            
            # Check cluster limit
            clusters = ecs_client.list_clusters()
            cluster_count = len(clusters['clusterArns'])
            
            # Check service limit (approximate)
            services_count = 0
            for cluster_arn in clusters['clusterArns']:
                services = ecs_client.list_services(cluster=cluster_arn)
                services_count += len(services['serviceArns'])
            
            self.validation_results['checks']['ecs_limits'] = {
                'status': 'valid',
                'clusters': f"{cluster_count}/100",  # Default limit
                'services': f"{services_count}/500"  # Default limit
            }
            
            if cluster_count > 90:
                self.validation_results['warnings'].append(
                    f"ECS cluster count ({cluster_count}) approaching limit (100)"
                )
            
            if services_count > 450:
                self.validation_results['warnings'].append(
                    f"ECS service count ({services_count}) approaching limit (500)"
                )
            
            return True
            
        except Exception as e:
            self.validation_results['checks']['ecs_limits'] = {
                'status': 'error',
                'error': str(e)
            }
            self.validation_results['warnings'].append(f"Could not check ECS limits: {e}")
            return True  # Don't fail deployment for limit check errors
    
    def _check_lambda_limits(self) -> bool:
        """Check Lambda service limits."""
        try:
            lambda_client = boto3.client('lambda', region_name=self.settings.aws_region)
            
            # List functions to check count
            functions = lambda_client.list_functions()
            function_count = len(functions['Functions'])
            
            # Check total code size
            total_code_size = sum(func['CodeSize'] for func in functions['Functions'])
            
            self.validation_results['checks']['lambda_limits'] = {
                'status': 'valid',
                'functions': f"{function_count}/1000",  # Default limit
                'total_code_size_mb': f"{total_code_size / (1024*1024):.1f}/75000"  # 75GB limit
            }
            
            if function_count > 900:
                self.validation_results['warnings'].append(
                    f"Lambda function count ({function_count}) approaching limit (1000)"
                )
            
            if total_code_size > 70 * 1024 * 1024 * 1024:  # 70GB
                self.validation_results['warnings'].append(
                    f"Lambda total code size approaching limit (75GB)"
                )
            
            return True
            
        except Exception as e:
            self.validation_results['checks']['lambda_limits'] = {
                'status': 'error',
                'error': str(e)
            }
            self.validation_results['warnings'].append(f"Could not check Lambda limits: {e}")
            return True
    
    def _check_vpc_limits(self) -> bool:
        """Check VPC and networking limits."""
        try:
            ec2_client = boto3.client('ec2', region_name=self.settings.aws_region)
            
            # Check VPC count
            vpcs = ec2_client.describe_vpcs()
            vpc_count = len(vpcs['Vpcs'])
            
            # Check security groups
            security_groups = ec2_client.describe_security_groups()
            sg_count = len(security_groups['SecurityGroups'])
            
            # Check NAT gateways
            nat_gateways = ec2_client.describe_nat_gateways()
            nat_count = len(nat_gateways['NatGateways'])
            
            self.validation_results['checks']['vpc_limits'] = {
                'status': 'valid',
                'vpcs': f"{vpc_count}/5",  # Default limit
                'security_groups': f"{sg_count}/2500",  # Default limit
                'nat_gateways': f"{nat_count}/5"  # Default limit per AZ
            }
            
            if vpc_count > 4:
                self.validation_results['warnings'].append(
                    f"VPC count ({vpc_count}) approaching limit (5)"
                )
            
            if sg_count > 2000:
                self.validation_results['warnings'].append(
                    f"Security group count ({sg_count}) approaching limit (2500)"
                )
            
            return True
            
        except Exception as e:
            self.validation_results['checks']['vpc_limits'] = {
                'status': 'error',
                'error': str(e)
            }
            self.validation_results['warnings'].append(f"Could not check VPC limits: {e}")
            return True
    
    def _check_efs_limits(self) -> bool:
        """Check EFS limits."""
        try:
            efs_client = boto3.client('efs', region_name=self.settings.aws_region)
            
            # Check file system count
            file_systems = efs_client.describe_file_systems()
            fs_count = len(file_systems['FileSystems'])
            
            self.validation_results['checks']['efs_limits'] = {
                'status': 'valid',
                'file_systems': f"{fs_count}/1000"  # Default limit
            }
            
            if fs_count > 900:
                self.validation_results['warnings'].append(
                    f"EFS file system count ({fs_count}) approaching limit (1000)"
                )
            
            return True
            
        except Exception as e:
            self.validation_results['checks']['efs_limits'] = {
                'status': 'error',
                'error': str(e)
            }
            self.validation_results['warnings'].append(f"Could not check EFS limits: {e}")
            return True
    
    def verify_permissions(self) -> bool:
        """Verify required IAM permissions for deployment."""
        required_permissions = [
            ('ecs', ['CreateCluster', 'CreateService', 'RegisterTaskDefinition']),
            ('lambda', ['CreateFunction', 'UpdateFunctionCode', 'CreateEventSourceMapping']),
            ('ec2', ['CreateVpc', 'CreateSubnet', 'CreateSecurityGroup', 'CreateNatGateway']),
            ('efs', ['CreateFileSystem', 'CreateMountTarget', 'CreateAccessPoint']),
            ('ecr', ['CreateRepository', 'PutImage', 'BatchCheckLayerAvailability']),
            ('s3', ['CreateBucket', 'PutObject', 'GetObject']),
            ('sqs', ['CreateQueue', 'SendMessage', 'ReceiveMessage']),
            ('iam', ['CreateRole', 'AttachRolePolicy', 'PassRole']),
            ('logs', ['CreateLogGroup', 'CreateLogStream', 'PutLogEvents'])
        ]
        
        permissions_ok = True
        permission_results = {}
        
        for service, actions in required_permissions:
            try:
                # Use IAM policy simulator to check permissions
                iam_client = boto3.client('iam', region_name=self.settings.aws_region)
                
                # Get current user/role ARN
                sts_client = boto3.client('sts', region_name=self.settings.aws_region)
                identity = sts_client.get_caller_identity()
                principal_arn = identity['Arn']
                
                # Simulate policy for each action
                action_results = {}
                for action in actions:
                    try:
                        response = iam_client.simulate_principal_policy(
                            PolicySourceArn=principal_arn,
                            ActionNames=[f"{service}:{action}"],
                            ResourceArns=['*']
                        )
                        
                        eval_results = response.get('EvaluationResults', [])
                        if eval_results:
                            decision = eval_results[0]['EvalDecision']
                            action_results[action] = decision == 'allowed'
                        else:
                            action_results[action] = 'unknown'
                            
                    except Exception as e:
                        action_results[action] = f'error: {e}'
                
                permission_results[service] = action_results
                
                # Check if any critical actions are denied
                denied_actions = [
                    action for action, result in action_results.items()
                    if result is False
                ]
                
                if denied_actions:
                    permissions_ok = False
                    self.validation_results['errors'].append(
                        f"Missing {service} permissions: {', '.join(denied_actions)}"
                    )
                
            except Exception as e:
                permission_results[service] = f'error: {e}'
                self.validation_results['warnings'].append(
                    f"Could not verify {service} permissions: {e}"
                )
        
        self.validation_results['checks']['permissions'] = {
            'status': 'valid' if permissions_ok else 'error',
            'details': permission_results
        }
        
        return permissions_ok
    
    def check_existing_resources(self) -> Dict[str, Any]:
        """Check for existing resources that might conflict."""
        existing_resources = {
            'conflicts': [],
            'reusable': [],
            'warnings': []
        }
        
        app_name = self.settings.app_name
        
        # Check for existing ECS cluster
        try:
            ecs_client = boto3.client('ecs', region_name=self.settings.aws_region)
            cluster_name = f"{app_name}-ecs-cluster"
            
            response = ecs_client.describe_clusters(clusters=[cluster_name])
            if response['clusters']:
                cluster = response['clusters'][0]
                if cluster['status'] == 'ACTIVE':
                    existing_resources['reusable'].append({
                        'type': 'ecs_cluster',
                        'name': cluster_name,
                        'status': cluster['status'],
                        'message': 'Existing ECS cluster can be reused'
                    })
                else:
                    existing_resources['conflicts'].append({
                        'type': 'ecs_cluster',
                        'name': cluster_name,
                        'status': cluster['status'],
                        'message': 'ECS cluster exists but is not active'
                    })
        except Exception as e:
            existing_resources['warnings'].append(f"Could not check ECS cluster: {e}")
        
        # Check for existing Lambda functions
        try:
            lambda_client = boto3.client('lambda', region_name=self.settings.aws_region)
            function_names = [f"{app_name}-files-api", f"{app_name}-iot-backend"]
            
            for function_name in function_names:
                try:
                    response = lambda_client.get_function(FunctionName=function_name)
                    existing_resources['reusable'].append({
                        'type': 'lambda_function',
                        'name': function_name,
                        'status': response['Configuration']['State'],
                        'message': 'Existing Lambda function will be updated'
                    })
                except ClientError as e:
                    if e.response['Error']['Code'] != 'ResourceNotFoundException':
                        existing_resources['warnings'].append(f"Could not check Lambda {function_name}: {e}")
        except Exception as e:
            existing_resources['warnings'].append(f"Could not check Lambda functions: {e}")
        
        # Check for existing S3 bucket
        try:
            s3_client = boto3.client('s3', region_name=self.settings.aws_region)
            bucket_name = self.settings.s3_bucket_name
            
            try:
                s3_client.head_bucket(Bucket=bucket_name)
                existing_resources['conflicts'].append({
                    'type': 's3_bucket',
                    'name': bucket_name,
                    'message': 'S3 bucket already exists - deployment may fail if not owned by this account'
                })
            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    existing_resources['warnings'].append(f"Could not check S3 bucket: {e}")
        except Exception as e:
            existing_resources['warnings'].append(f"Could not check S3 bucket: {e}")
        
        self.validation_results['checks']['existing_resources'] = existing_resources
        
        # Add conflicts as warnings
        for conflict in existing_resources['conflicts']:
            self.validation_results['warnings'].append(
                f"Resource conflict: {conflict['type']} '{conflict['name']}' - {conflict['message']}"
            )
        
        return existing_resources
    
    def validate_deployment_prerequisites(self) -> bool:
        """Run all validation checks for deployment prerequisites."""
        print("🔍 Validating deployment prerequisites...")
        
        # Check credentials first
        if not self.validate_credentials():
            return False
        
        print("✅ AWS credentials valid")
        
        # Check service limits
        self.check_service_limits()
        print("✅ Service limits checked")
        
        # Verify permissions
        self.verify_permissions()
        print("✅ Permissions verified")
        
        # Check existing resources
        self.check_existing_resources()
        print("✅ Existing resources checked")
        
        return self.validation_results['valid']
    
    def get_validation_report(self, format: str = 'text') -> str:
        """Get validation report in specified format."""
        if format == 'json':
            return json.dumps(self.validation_results, indent=2)
        
        elif format == 'text':
            report = []
            report.append("AWS Deployment Validation Report")
            report.append("=" * 40)
            report.append(f"Overall Status: {'✅ VALID' if self.validation_results['valid'] else '❌ INVALID'}")
            report.append("")
            
            # Credentials
            if 'credentials' in self.validation_results['checks']:
                creds = self.validation_results['checks']['credentials']
                if creds['status'] == 'valid':
                    report.append(f"✅ Credentials: {creds['user_arn']}")
                else:
                    report.append(f"❌ Credentials: {creds.get('error', 'Invalid')}")
            
            # Service Limits
            for service in ['ecs_limits', 'lambda_limits', 'vpc_limits', 'efs_limits']:
                if service in self.validation_results['checks']:
                    limits = self.validation_results['checks'][service]
                    if limits['status'] == 'valid':
                        report.append(f"✅ {service.replace('_', ' ').title()}: OK")
                    else:
                        report.append(f"⚠️ {service.replace('_', ' ').title()}: {limits.get('error', 'Check failed')}")
            
            # Permissions
            if 'permissions' in self.validation_results['checks']:
                perms = self.validation_results['checks']['permissions']
                if perms['status'] == 'valid':
                    report.append("✅ Permissions: Sufficient")
                else:
                    report.append("❌ Permissions: Insufficient")
            
            # Existing Resources
            if 'existing_resources' in self.validation_results['checks']:
                existing = self.validation_results['checks']['existing_resources']
                if existing['reusable']:
                    report.append(f"♻️ Reusable Resources: {len(existing['reusable'])} found")
                if existing['conflicts']:
                    report.append(f"⚠️ Resource Conflicts: {len(existing['conflicts'])} found")
            
            report.append("")
            
            # Warnings
            if self.validation_results['warnings']:
                report.append("Warnings:")
                for warning in self.validation_results['warnings']:
                    report.append(f"  ⚠️ {warning}")
                report.append("")
            
            # Errors
            if self.validation_results['errors']:
                report.append("Errors:")
                for error in self.validation_results['errors']:
                    report.append(f"  ❌ {error}")
                report.append("")
            
            return "\n".join(report)
        
        else:
            raise ValueError(f"Unsupported format: {format}")


def main():
    """CLI entry point for resource validation."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Validate AWS deployment prerequisites')
    parser.add_argument('--format', choices=['json', 'text'], default='text',
                       help='Output format')
    parser.add_argument('--check', choices=['credentials', 'limits', 'permissions', 'existing'],
                       help='Run specific validation check')
    
    args = parser.parse_args()
    
    validator = ResourceValidator()
    
    try:
        if args.check:
            # Run specific check
            if args.check == 'credentials':
                result = validator.validate_credentials()
            elif args.check == 'limits':
                result = validator.check_service_limits()
            elif args.check == 'permissions':
                result = validator.verify_permissions()
            elif args.check == 'existing':
                result = validator.check_existing_resources()
            
            print(f"Check result: {'✅ PASS' if result else '❌ FAIL'}")
        else:
            # Run all validations
            result = validator.validate_deployment_prerequisites()
        
        # Print report
        report = validator.get_validation_report(args.format)
        print(report)
        
        # Exit with appropriate code
        exit(0 if validator.validation_results['valid'] else 1)
        
    except Exception as e:
        print(f"❌ Validation error: {e}")
        exit(1)


if __name__ == "__main__":
    main()