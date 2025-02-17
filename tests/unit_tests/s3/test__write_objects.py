"""Test cases for `s3.write_objects`."""

import boto3

from files_api.s3.write_objects import upload_s3_object
from tests.consts import TEST_BUCKET_NAME


# pylint: disable=unused-argument
def test_upload_s3_object(mocked_aws: None):
    s3_client = boto3.client("s3")
    file_content = b"test content"
    upload_s3_object(TEST_BUCKET_NAME, "testfile.txt", file_content)
    response = s3_client.get_object(Bucket=TEST_BUCKET_NAME, Key="testfile.txt")
    assert response["Body"].read() == file_content