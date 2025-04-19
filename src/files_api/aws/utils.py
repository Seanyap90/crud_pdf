"""AWS utility functions and client management."""
import os
import boto3
import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class AWSClientManager:
    """Singleton manager for AWS service clients.
    
    Ensures only one instance of each AWS client exists, properly configured
    for the current execution mode (local-mock, cloud, etc.).
    """
    _instance = None
    _clients = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AWSClientManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize the client manager with execution mode settings."""
        self.region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        self.endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
        self.mode = os.environ.get('QUEUE_TYPE', 'local-mock')
        
        logger.info(f"Initializing AWSClientManager in {self.mode} mode")
        logger.info(f"Region: {self.region}, Endpoint: {self.endpoint_url}")
    
    def get_client(self, service_name: str) -> Any:
        """Get or create an AWS service client.
        
        Args:
            service_name: AWS service name (e.g., 's3', 'sqs', 'cloudwatch')
            
        Returns:
            Boto3 client for the requested service
        """
        # Return existing client if already created
        if service_name in self._clients:
            return self._clients[service_name]
        
        # Create client with appropriate configuration
        client_kwargs = {
            'region_name': self.region
        }
        
        # Add endpoint URL for local-mock mode
        if self.endpoint_url and self.mode in ['local-mock', 'local']:
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

def create_s3_bucket(bucket_name: str) -> bool:
    """Create an S3 bucket with proper error handling.
    
    Args:
        bucket_name: Name of the bucket to create
        
    Returns:
        True if bucket was created or already exists, False otherwise
    """
    try:
        s3_client = get_s3_client()
        s3_client.create_bucket(Bucket=bucket_name)
        logger.info(f"Created S3 bucket: {bucket_name}")
        return True
    except s3_client.exceptions.BucketAlreadyExists:
        logger.info(f"S3 bucket already exists: {bucket_name}")
        return True
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        logger.info(f"S3 bucket already owned by you: {bucket_name}")
        return True
    except Exception as e:
        logger.error(f"Error creating S3 bucket {bucket_name}: {str(e)}")
        return False

def create_sqs_queue(queue_name: str, attributes: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Create an SQS queue with proper error handling.
    
    Args:
        queue_name: Name of the queue to create
        attributes: Optional queue attributes
        
    Returns:
        Queue URL if created successfully, None otherwise
    """
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