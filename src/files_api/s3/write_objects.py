"""Functions for writing objects from an S3 bucket--the "C" and "U" in CRUD."""

from typing import Optional

import boto3

try:
    from mypy_boto3_s3 import S3Client
except ImportError:
    ...


def upload_s3_object(
    bucket_name: str,
    object_key: str,
    file_content: bytes,
    content_type: Optional[str] = None,
    s3_client: Optional["S3Client"] = None,
) -> None:
    """
    Upload a file to an S3 bucket.

    :param bucket_name: The name of the S3 bucket.
    :param object_key: path to the object in the S3 bucket.
    :param file_content: The content of the file to upload.
    :param content_type: The MIME type of the file, e.g. "text/plain" for a text file.
    :param s3_client: An optional boto3 S3 client. If not provided, one will be created.
    """
    content_type = content_type or "application/octet-stream"
    #s3_client = s3_client or boto3.client("s3")
    if s3_client is None:
        # Use the centralized client that respects mock settings
        from deployment.aws.utils.aws_clients import get_s3_client
        s3_client = get_s3_client()
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=file_content,
        ContentType=content_type,
    )