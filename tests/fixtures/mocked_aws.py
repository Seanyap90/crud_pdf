"""Pytest fixture to mock AWS services."""

import os

import boto3
import botocore
import pytest
from moto import mock_aws
from unittest.mock import patch
from botocore.exceptions import BotoCoreError, ClientError

from tests.consts import TEST_BUCKET_NAME
from tests.utils import delete_s3_bucket


# Set the environment variables to point away from AWS
def point_away_from_aws() -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


# Our fixture is a function and we have named it as a noun instead
# of verb, because it is a resource that is being provided to the test.
@pytest.fixture(scope="function")
def mocked_aws():
    """Set up a mocked AWS environment for testing and clean up after the test."""
    with mock_aws():
        # Set the environment variables to point away from AWS
        point_away_from_aws()

        # 1. Create an S3 bucket
        s3_client = boto3.client("s3")
        s3_client.create_bucket(Bucket=TEST_BUCKET_NAME)

        # Configure S3 notifications
        s3_client.put_bucket_notification_configuration(
            Bucket=TEST_BUCKET_NAME,
            NotificationConfiguration={
                'QueueConfigurations': [{
                    'QueueArn': 'arn:aws:sqs:us-east-1:123456789012:test-queue',
                    'Events': ['s3:ObjectCreated:Put']
                }]
            }
        )

        yield

        # 2. Clean up/Teardown by deleting the bucket
        try:
            delete_s3_bucket(TEST_BUCKET_NAME)
        except botocore.exceptions.ClientError as err:
            if err.response["Error"]["Code"] == "NoSuchBucket":
                pass
            else:
                raise

@pytest.fixture
def mock_boto3_client():
    """Fixture to mock boto3 Lambda client"""
    with patch("boto3.client") as mock_client:
        yield mock_client