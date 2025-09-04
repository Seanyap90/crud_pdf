"""AWS utility functions and client management."""
import os
import boto3
import logging
from typing import Dict, Optional, Any
from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

class AWSClientManager:
    """Singleton manager for AWS service clients."""
    _instance = None
    _clients = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AWSClientManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize the client manager with settings."""
        # Get settings once during initialization
        self.settings = get_settings()
        
        # Cache commonly used values
        self.region = self.settings.aws_region
        self.endpoint_url = self.settings.aws_endpoint_url
        self.mode = self.settings.deployment_mode
        
        logger.info(f"Initializing AWSClientManager")
        logger.info(f"  Mode: {self.mode}")
        logger.info(f"  Region: {self.region}")
        logger.info(f"  Endpoint: {self.endpoint_url}")
    
    def get_client(self, service_name: str) -> Any:
        """Get or create an AWS service client."""
        # Return existing client if already created
        if service_name in self._clients:
            return self._clients[service_name]
        
        # Create client with settings-based configuration
        client_kwargs = {
            'region_name': self.region
        }
        
        # Check for AWS profile in environment (for SSO)
        aws_profile = os.environ.get('AWS_PROFILE')
        if aws_profile and self.mode == 'aws-prod':
            # Use session with profile for SSO
            try:
                session = boto3.Session(profile_name=aws_profile)
                client = session.client(service_name, region_name=self.region)
                self._clients[service_name] = client
                logger.debug(f"Created {service_name} client using profile: {aws_profile}")
                return client
            except Exception as e:
                logger.warning(f"Failed to create client with profile {aws_profile}: {e}")
                # Fall back to manual credential configuration
        
        # Add credentials from settings (fallback or for non-SSO modes)
        if self.settings.aws_access_key_id:
            client_kwargs['aws_access_key_id'] = self.settings.aws_access_key_id
        if self.settings.aws_secret_access_key:
            client_kwargs['aws_secret_access_key'] = self.settings.aws_secret_access_key
        
        # Add endpoint URL for local/mock modes
        if self.endpoint_url and self.mode in ['local-dev', 'aws-mock']:
            client_kwargs['endpoint_url'] = self.endpoint_url
        
        try:
            client = boto3.client(service_name, **client_kwargs)
            self._clients[service_name] = client
            logger.debug(f"Created {service_name} client")
            return client
        except Exception as e:
            logger.error(f"Error creating {service_name} client: {str(e)}")
            raise
    
    def clear_clients(self):
        """Clear all cached clients."""
        self._clients.clear()
        logger.debug("Cleared all AWS clients")

# Convenience functions for common operations

def get_s3_client():
    """Get the S3 client."""
    return AWSClientManager().get_client('s3')

def get_sqs_client():
    """Get the SQS client."""
    return AWSClientManager().get_client('sqs')

def get_cloudwatch_client():
    """Get the CloudWatch client."""
    return AWSClientManager().get_client('cloudwatch')

def get_ec2_client():
    """Get the EC2 client."""
    return AWSClientManager().get_client('ec2')

def get_iam_client():
    """Get the IAM client."""
    return AWSClientManager().get_client('iam')

def get_ecr_client():
    """Get the ECR client."""
    return AWSClientManager().get_client('ecr')

def get_asg_client():
    """Get the Auto Scaling Group client."""
    return AWSClientManager().get_client('autoscaling')

def get_elasticbeanstalk_client():
    """Get the Elastic Beanstalk client."""
    return AWSClientManager().get_client('elasticbeanstalk')

def get_ecs_client():
    """Get the ECS client."""
    return AWSClientManager().get_client('ecs')

def get_efs_client():
    """Get the EFS client."""
    return AWSClientManager().get_client('efs')

def get_application_autoscaling_client():
    """Get the Application Auto Scaling client."""
    return AWSClientManager().get_client('application-autoscaling')


def get_cloudwatch_client():
    """Get the CloudWatch client."""
    return AWSClientManager().get_client('cloudwatch')

def get_lambda_client():
    """Get the Lambda client."""
    return AWSClientManager().get_client('lambda')

def get_logs_client():
    """Get the CloudWatch Logs client."""
    return AWSClientManager().get_client('logs')

def create_s3_bucket(bucket_name: str = None) -> bool:
    """Create an S3 bucket with smart naming and reuse strategy."""
    import time
    
    settings = get_settings()
    base_bucket_name = bucket_name or settings.s3_bucket_name.rstrip('-0123456789')
    
    try:
        s3_client = get_s3_client()
        region = settings.aws_region
        
        # First, look for existing buckets with the base name
        try:
            response = s3_client.list_buckets()
            existing_buckets = []
            
            for bucket in response.get('Buckets', []):
                bucket_name_check = bucket['Name']
                if bucket_name_check.startswith(base_bucket_name):
                    # Check if we own this bucket
                    try:
                        s3_client.head_bucket(Bucket=bucket_name_check)
                        existing_buckets.append({
                            'name': bucket_name_check,
                            'created': bucket['CreationDate']
                        })
                    except Exception:
                        # We don't own this bucket, skip it
                        continue
            
            if existing_buckets:
                # Use the latest bucket we own
                latest_bucket = max(existing_buckets, key=lambda x: x['created'])
                latest_bucket_name = latest_bucket['name']
                logger.info(f"Using existing S3 bucket: {latest_bucket_name}")
                
                # Update settings to use this bucket name
                settings.s3_bucket_name = latest_bucket_name
                return True
                
        except Exception as e:
            logger.warning(f"Could not list existing buckets: {e}")
        
        # No existing bucket found, create a new one
        # For mock environments, use exact bucket name without timestamp
        if settings.deployment_mode == 'aws-mock':
            new_bucket_name = base_bucket_name
        else:
            # For production, use timestamp to ensure uniqueness
            timestamp = str(int(time.time()))
            new_bucket_name = f"{base_bucket_name}-{timestamp}"
        
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=new_bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=new_bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        
        logger.info(f"Created new S3 bucket: {new_bucket_name}")
        
        # Update settings to use this bucket name
        settings.s3_bucket_name = new_bucket_name
        return True
        
    except Exception as e:
        # Check for specific S3 exceptions
        if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'BucketAlreadyExists':
            logger.warning(f"Bucket name {new_bucket_name if 'new_bucket_name' in locals() else 'unknown'} is taken globally")
            # Try one more time with a more unique timestamp
            try:
                import random
                unique_suffix = f"{int(time.time())}-{random.randint(1000, 9999)}"
                fallback_bucket_name = f"{base_bucket_name}-{unique_suffix}"
                
                if region == 'us-east-1':
                    s3_client.create_bucket(Bucket=fallback_bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=fallback_bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )
                
                logger.info(f"Created fallback S3 bucket: {fallback_bucket_name}")
                settings.s3_bucket_name = fallback_bucket_name
                return True
                
            except Exception as fallback_error:
                logger.error(f"Failed to create fallback bucket: {fallback_error}")
                return False
        elif hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'BucketAlreadyOwnedByYou':
            bucket_to_use = new_bucket_name if 'new_bucket_name' in locals() else base_bucket_name
            logger.info(f"S3 bucket already owned by you: {bucket_to_use}")
            settings.s3_bucket_name = bucket_to_use
            return True
        
        logger.error(f"Error creating S3 bucket: {str(e)}")
        return False

def create_sqs_queue(queue_name: str = None, attributes: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Create an SQS queue with proper error handling."""
    settings = get_settings()
    queue_name = queue_name or settings.sqs_queue_name
    
    try:
        sqs_client = get_sqs_client()
        
        # Set default attributes if not provided
        if attributes is None:
            attributes = {
                'VisibilityTimeout': '900',  # 15 minutes
                'MessageRetentionPeriod': '345600'  # 4 days
            }
        
        response = sqs_client.create_queue(
            QueueName=queue_name,
            Attributes=attributes
        )
        
        queue_url = response.get('QueueUrl')
        if queue_url:
            logger.info(f"Created SQS queue: {queue_name} ({queue_url})")
            
            # For mock mode, adjust the URL format if needed
            if settings.deployment_mode == 'aws-mock':
                # Keep the original URL from moto, it will be normalized elsewhere
                pass
            
            return queue_url
        else:
            logger.error(f"Failed to create SQS queue: {queue_name}")
            return None
    except Exception as e:
        logger.error(f"Error creating SQS queue {queue_name}: {str(e)}")
        return None

def get_queue_arn(queue_url: str) -> Optional[str]:
    """Get the ARN for an SQS queue.
    
    Args:
        queue_url: URL of the queue
        
    Returns:
        Queue ARN if successful, None otherwise
    """
    try:
        sqs_client = get_sqs_client()
        response = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn']
        )
        return response['Attributes']['QueueArn']
    except Exception as e:
        logger.error(f"Error getting queue ARN for {queue_url}: {str(e)}")
        return None