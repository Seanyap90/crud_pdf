"""
Simplified storage adapter for decoupling storage and database operations.
Uses a single file with environment-based configuration rather than abstract classes.
"""

import os
import json
import logging
import boto3
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)

# Global variables to hold configured implementations
_S3_CLIENT = None
_SQS_CLIENT = None
_BUCKET_NAME = None
_QUEUE_URL = None
_STORAGE_DIR = None
_MODE = None

def init_storage(mode=None):
    """Initialize storage with the specified mode or from environment."""
    global _S3_CLIENT, _SQS_CLIENT, _BUCKET_NAME, _QUEUE_URL, _STORAGE_DIR, _MODE
    
    # Get mode from parameter or environment
    _MODE = mode or os.environ.get('QUEUE_TYPE', 'local-dev')
    
    if _MODE in ['aws-mock', 'aws-prod']:
        # Configure endpoint based on context
        endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
        
        # IMPORTANT: Initialize bucket name first so we can log it
        _BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
        if not _BUCKET_NAME:
            logger.error("S3_BUCKET_NAME environment variable is not set!")
            # Set a fallback value to prevent errors
            _BUCKET_NAME = "rag-pdf-storage"
            
        logger.info(f"Using S3 bucket: {_BUCKET_NAME}")
        
        # Initialize S3 client
        _S3_CLIENT = boto3.client(
            's3',
            region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-west-2'),
            endpoint_url=endpoint_url,
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        # Initialize SQS client
        _SQS_CLIENT = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-west-2'),
            endpoint_url=endpoint_url, 
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        # Store queue URL
        _QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
        logger.info(f"Initialized SQS client with queue URL: {_QUEUE_URL}")
    else:
        # Local file system mode
        _STORAGE_DIR = Path(os.environ.get('STORAGE_DIR', 'storage'))
        _STORAGE_DIR.mkdir(exist_ok=True)
    
    logger.info(f"Storage initialized in {_MODE} mode")

# File storage operations
def download_file(file_path: str, local_path: str) -> str:
    """Download file from storage to local path."""
    if _MODE is None:
        init_storage()
        
    if _MODE in ['aws-mock', 'aws-prod']:
        try:
            # Validate bucket name is available
            if not _BUCKET_NAME:
                raise ValueError("S3 bucket name is not set")
                
            logger.info(f"Downloading file '{file_path}' from bucket '{_BUCKET_NAME}' to '{local_path}'")
            _S3_CLIENT.download_file(
                Bucket=_BUCKET_NAME,
                Key=file_path,
                Filename=local_path
            )
            logger.info(f"Successfully downloaded {file_path} from S3 to {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"Error downloading from S3: {str(e)}")
            raise
    else:
        # Local file system implementation
        source_path = _STORAGE_DIR / file_path
        if not source_path.exists():
            raise FileNotFoundError(f"File not found: {source_path}")
            
        shutil.copy2(str(source_path), local_path)
        return local_path

def upload_file(local_path: str, file_path: str, content_type: Optional[str] = None) -> None:
    """Upload file from local path to storage."""
    if _MODE is None:
        init_storage()
        
    if _MODE in ['aws-mock', 'aws-prod']:
        try:
            with open(local_path, 'rb') as file_data:
                _S3_CLIENT.put_object(
                    Bucket=_BUCKET_NAME,
                    Key=file_path,
                    Body=file_data,
                    ContentType=content_type or "application/octet-stream"
                )
            logger.info(f"Uploaded {local_path} to S3 as {file_path}")
        except Exception as e:
            logger.error(f"Error uploading to S3: {str(e)}")
            raise
    else:
        # Local file system implementation
        dest_path = _STORAGE_DIR / file_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, str(dest_path))

def file_exists(file_path: str) -> bool:
    """Check if a file exists in storage."""
    if _MODE is None:
        init_storage()
        
    if _MODE in ['aws-mock', 'aws-prod']:
        try:
            _S3_CLIENT.head_object(
                Bucket=_BUCKET_NAME,
                Key=file_path
            )
            return True
        except Exception as e:
            if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == '404':
                return False
            raise
    else:
        # Local file system implementation
        return (_STORAGE_DIR / file_path).exists()

# Conditional database imports - only used if available and in certain modes
try:
    from database.local import update_invoice_processing_status, update_invoice_with_extracted_data
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
    logger.warning("Database module not available")

# Task result operations
def update_task_status(task_id: Union[str, int], status: str, timestamp: Optional[str] = None) -> None:
    """Update task status with appropriate handler based on mode."""
    if _MODE is None:
        init_storage()
        
    if _MODE == 'local-dev' and _DB_AVAILABLE:
        # Direct database update
        try:
            update_invoice_processing_status(
                invoice_id=task_id,
                status=status,
                processing_date=timestamp or datetime.utcnow().isoformat()
            )
            logger.info(f"Updated task {task_id} status to {status} in database")
        except Exception as e:
            logger.error(f"Error updating task status in database: {str(e)}")
            raise
    elif _MODE in ['aws-mock', 'aws-prod']:
        # SQS message for decoupled update
        try:
            # Check if SQS client is initialized
            if _SQS_CLIENT is None:
                logger.error("SQS client is not initialized!")
                # Try to initialize it
                init_storage(_MODE)
                if _SQS_CLIENT is None:
                    logger.error("Failed to initialize SQS client, using fallback")
                    logger.info(f"MOCK: Updated task {task_id} status to {status}")
                    return
                    
            message_body = json.dumps({
                'task_type': 'update_status',
                'invoice_id': task_id,
                'status': status,
                'timestamp': timestamp or datetime.utcnow().isoformat()
            })
            
            logger.info(f"Sending status update to SQS queue: {_QUEUE_URL}")
            _SQS_CLIENT.send_message(
                QueueUrl=_QUEUE_URL,
                MessageBody=message_body
            )
            logger.info(f"Sent task {task_id} status update to SQS queue")
        except Exception as e:
            logger.error(f"Error sending status update to SQS: {str(e)}")
            # Don't raise the exception - just log it and continue
            logger.info(f"MOCK: Updated task {task_id} status to {status}")
    else:
        # Fallback - just log
        logger.info(f"MOCK: Updated task {task_id} status to {status}")

def update_task_result(task_id: Union[str, int], 
                     result_data: Optional[Dict[str, Any]], 
                     status: str, 
                     completion_timestamp: Optional[str] = None, 
                     error_message: Optional[str] = None) -> None:
    """Update task with result data using appropriate handler based on mode."""
    if _MODE is None:
        init_storage()
        
    if _MODE == 'local-dev' and _DB_AVAILABLE:
        # Direct database update
        try:
            total_amount = None
            reported_weight = None
            
            if result_data:
                total_amount = result_data.get('total_amount')
                reported_weight = result_data.get('reported_weight')
                
            update_invoice_with_extracted_data(
                invoice_id=task_id,
                total_amount=total_amount,
                reported_weight_kg=reported_weight,
                status=status,
                completion_date=completion_timestamp or datetime.utcnow().isoformat(),
                error_message=error_message
            )
            logger.info(f"Updated task {task_id} result with status {status} in database")
        except Exception as e:
            logger.error(f"Error updating task result in database: {str(e)}")
            raise
    elif _MODE in ['aws-mock', 'aws-prod']:
        # SQS message for decoupled update
        try:
            message_body = json.dumps({
                'task_type': 'update_result',
                'invoice_id': task_id,
                'result_data': result_data,
                'status': status,
                'completion_timestamp': completion_timestamp or datetime.utcnow().isoformat(),
                'error_message': error_message
            })
            
            _SQS_CLIENT.send_message(
                QueueUrl=_QUEUE_URL,
                MessageBody=message_body
            )
            logger.info(f"Sent task {task_id} result update to SQS queue")
        except Exception as e:
            logger.error(f"Error sending result update to SQS: {str(e)}")
            raise
    else:
        # Fallback - just log
        logger.info(f"MOCK: Updated task {task_id} result with status {status}")