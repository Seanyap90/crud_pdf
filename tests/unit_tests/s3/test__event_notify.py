import boto3
import pytest
from files_api.s3.event_notify import enable_s3_notifications
from tests.consts import TEST_BUCKET_NAME

def test_enable_s3_notifications(mocked_aws):
    s3_client = boto3.client('s3')
    enable_s3_notifications(TEST_BUCKET_NAME, s3_client)
    
    config = s3_client.get_bucket_notification_configuration(Bucket=TEST_BUCKET_NAME)
    config.pop('ResponseMetadata', None)
    print(config)
    
    assert 'QueueConfigurations' in config
    assert config['QueueConfigurations'][0]['Events'] == ['s3:ObjectCreated:Put']