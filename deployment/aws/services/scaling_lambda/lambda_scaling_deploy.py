"""
Lambda Scaling Functions Deployment

Deploys separate scale-out and scale-in Lambda functions for EventBridge integration.
These functions control ECS task scaling via ASG stop/start operations.

Usage:
    # Use existing role (recommended)
    python lambda_scaling_deploy.py us-east-1 ECSScaling-role-iinqatta

    # Create new role
    python lambda_scaling_deploy.py us-east-1

    # Use default region
    python lambda_scaling_deploy.py
"""

import logging
import json
import time
import zipfile
import io
from typing import Dict, Any, Optional
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

class LambdaScalingDeployer:
    """Deploy scale-out and scale-in Lambda functions for EventBridge."""
    
    def __init__(self, region: str = None):
        """Initialize Lambda scaling deployer."""
        self.settings = get_settings()
        self.region = region or self.settings.aws_region
        
        # AWS clients
        self.lambda_client = boto3.client('lambda', region_name=self.region)
        self.iam_client = boto3.client('iam', region_name=self.region)
        
        # Configuration
        self.app_name = self.settings.app_name or "fastapi-app"
        self.asg_name = f"{self.app_name}-asg"
        
        logger.info(f"Lambda scaling deployer initialized for region: {self.region}")
    
    def create_lambda_execution_role(self, role_name: str) -> str:
        """Create IAM role for Lambda scaling functions."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        try:
            # Check if role exists
            try:
                response = self.iam_client.get_role(RoleName=role_name)
                logger.info(f"Using existing Lambda role: {role_name}")
                return response['Role']['Arn']
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    raise
            
            # Create role
            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Lambda execution role for ECS scaling functions",
                Tags=[
                    {'Key': 'Project', 'Value': self.app_name},
                    {'Key': 'Purpose', 'Value': 'lambda-scaling'}
                ]
            )
            
            role_arn = response['Role']['Arn']
            logger.info(f"Created Lambda role: {role_name}")
            
            # Attach basic execution policy
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            )
            
            # Create and attach custom scaling policy
            scaling_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "autoscaling:DescribeAutoScalingGroups",
                            "autoscaling:UpdateAutoScalingGroup"
                        ],
                        "Resource": f"arn:aws:autoscaling:{self.region}:{self.settings.aws_account_id}:autoScalingGroup:*:autoScalingGroupName/{self.asg_name}"
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ec2:DescribeInstances",
                            "ec2:StartInstances",
                            "ec2:StopInstances"
                        ],
                        "Resource": "*"
                    }
                ]
            }
            
            policy_name = f"{role_name}-scaling-policy"
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName=policy_name,
                PolicyDocument=json.dumps(scaling_policy)
            )
            
            logger.info(f"Attached scaling policy to role: {role_name}")
            
            # Wait for role to be available
            time.sleep(10)
            
            return role_arn
            
        except Exception as e:
            logger.error(f"Failed to create Lambda role: {e}")
            raise
    
    def create_lambda_deployment_package(self) -> bytes:
        """Create deployment package with scaling functions."""
        # Create in-memory zip file
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Use the separate handler files from scaling_lambda directory
            scaling_lambda_dir = Path(__file__).parent / "scaling_lambda"
            
            scale_out_path = scaling_lambda_dir / "scale_out_handler.py"
            scale_in_path = scaling_lambda_dir / "scale_in_handler.py"
            
            if scale_out_path.exists() and scale_in_path.exists():
                # Add the individual handler files
                zip_file.write(scale_out_path, "scale_out_handler.py")
                zip_file.write(scale_in_path, "scale_in_handler.py")
                
                # Create wrapper files for Lambda entry points
                scale_out_wrapper = '''
from scale_out_handler import lambda_handler
'''
                
                scale_in_wrapper = '''
from scale_in_handler import lambda_handler
'''
                
                zip_file.writestr("scale_out.py", scale_out_wrapper)
                zip_file.writestr("scale_in.py", scale_in_wrapper)
            else:
                raise FileNotFoundError(
                    f"Required handler files not found: {scale_out_path}, {scale_in_path}"
                )
        
        zip_buffer.seek(0)
        return zip_buffer.read()
    
    def deploy_lambda_function(self, function_name: str, handler: str, role_arn: str, zip_code: bytes) -> Dict[str, Any]:
        """Deploy a Lambda function."""
        try:
            # Check if function exists
            try:
                response = self.lambda_client.get_function(FunctionName=function_name)
                logger.info(f"Updating existing Lambda function: {function_name}")
                
                # Update function code
                self.lambda_client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=zip_code
                )
                
                # Update function configuration
                response = self.lambda_client.update_function_configuration(
                    FunctionName=function_name,
                    Handler=handler,
                    Runtime="python3.11",
                    Timeout=60,
                    Environment={
                        'Variables': {
                            'ASG_NAME': self.asg_name,
                            'REGION': self.region
                        }
                    }
                )
                
                function_arn = response['FunctionArn']
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
                
                logger.info(f"Creating new Lambda function: {function_name}")
                
                # Create function
                response = self.lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime="python3.11",
                    Role=role_arn,
                    Handler=handler,
                    Code={'ZipFile': zip_code},
                    Timeout=60,
                    Environment={
                        'Variables': {
                            'ASG_NAME': self.asg_name,
                            'REGION': self.region
                        }
                    },
                    Tags={
                        'Project': self.app_name,
                        'Purpose': 'ecs-scaling'
                    }
                )
                
                function_arn = response['FunctionArn']
            
            logger.info(f"Lambda function deployed: {function_name}")
            
            return {
                'function_name': function_name,
                'function_arn': function_arn,
                'handler': handler
            }
            
        except Exception as e:
            logger.error(f"Failed to deploy Lambda function {function_name}: {e}")
            raise
    
    def deploy_scaling_functions(self, existing_role_name: str = None) -> Dict[str, Any]:
        """Deploy both scale-out and scale-in Lambda functions."""
        logger.info("Deploying Lambda scaling functions...")

        try:
            # Use existing role if provided, otherwise create new one
            if existing_role_name:
                logger.info(f"Using existing IAM role: {existing_role_name}")
                try:
                    response = self.iam_client.get_role(RoleName=existing_role_name)
                    role_arn = response['Role']['Arn']
                    logger.info(f"Found existing role ARN: {role_arn}")
                except ClientError as e:
                    logger.error(f"Failed to find existing role {existing_role_name}: {e}")
                    raise
            else:
                # Create IAM role
                role_name = f"{self.app_name}-lambda-scaling-role"
                role_arn = self.create_lambda_execution_role(role_name)
            
            # Create deployment package
            zip_code = self.create_lambda_deployment_package()
            
            # Deploy scale-out function
            scale_out_function = self.deploy_lambda_function(
                function_name=f"{self.app_name}-scale-out",
                handler="scale_out.lambda_handler",
                role_arn=role_arn,
                zip_code=zip_code
            )
            
            # Deploy scale-in function
            scale_in_function = self.deploy_lambda_function(
                function_name=f"{self.app_name}-scale-in",
                handler="scale_in.lambda_handler",
                role_arn=role_arn,
                zip_code=zip_code
            )
            
            result = {
                'status': 'success',
                'region': self.region,
                'iam_role_arn': role_arn,
                'scale_out': scale_out_function,
                'scale_in': scale_in_function,
                'asg_name': self.asg_name,
                'eventbridge_integration': {
                    'scale_out_function': scale_out_function['function_arn'],
                    'scale_in_function': scale_in_function['function_arn'],
                    'note': 'Create EventBridge rules manually to trigger these functions'
                }
            }
            
            logger.info("Lambda scaling functions deployed successfully")
            logger.info(f"Scale-out function: {scale_out_function['function_arn']}")
            logger.info(f"Scale-in function: {scale_in_function['function_arn']}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to deploy scaling functions: {e}")
            return {
                'status': 'failed',
                'error': str(e),
                'region': self.region
            }


def deploy_lambda_scaling_functions(region: str = None, existing_role_name: str = None) -> Dict[str, Any]:
    """Convenience function to deploy Lambda scaling functions."""
    deployer = LambdaScalingDeployer(region=region)
    return deployer.deploy_scaling_functions(existing_role_name=existing_role_name)


if __name__ == "__main__":
    # Deploy Lambda scaling functions
    import sys

    region = sys.argv[1] if len(sys.argv) > 1 else None
    existing_role_name = sys.argv[2] if len(sys.argv) > 2 else None
    result = deploy_lambda_scaling_functions(region=region, existing_role_name=existing_role_name)
    
    if result['status'] == 'success':
        print("✅ Lambda scaling functions deployed successfully")
        print(f"Scale-out function: {result['scale_out']['function_name']}")
        print(f"Scale-in function: {result['scale_in']['function_name']}")
        print("\n📋 Next steps:")
        print("1. Create EventBridge rules to trigger these functions")
        print("2. Test scaling functions manually or via EventBridge")
        print("3. Monitor CloudWatch logs for function execution")
    else:
        print(f"❌ Deployment failed: {result.get('error')}")
        sys.exit(1)