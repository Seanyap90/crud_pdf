"""S3 event notification functions."""
import boto3
from typing import Optional
from mypy_boto3_s3 import S3Client

def enable_s3_notifications(bucket_name: str, s3_client: Optional[S3Client] = None) -> None:
    # test with mock queuearn
    s3_client = s3_client or boto3.client('s3')
    s3_client.put_bucket_notification_configuration(
        Bucket=bucket_name,
        NotificationConfiguration={
            'QueueConfigurations': [{
                'QueueArn': 'arn:aws:sqs:us-east-1:123456789012:test-queue',
                'Events': ['s3:ObjectCreated:Put']
            }]
        }
    )