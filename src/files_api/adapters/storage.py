"""
Storage adapter with synchronous HTTP API calls for status updates.
"""

import os
import json
import logging
import boto3
import requests  # Use requests instead of httpx
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)

# Global variables
_S3_CLIENT = None
_BUCKET_NAME = None
_STORAGE_DIR = None
_MODE = None
_API_BASE_URL = None

def init_storage(mode=None):
    """Initialize storage with the specified mode."""
    global _S3_CLIENT, _BUCKET_NAME, _STORAGE_DIR, _MODE, _API_BASE_URL
    
    _MODE = mode or os.environ.get('DEPLOYMENT_MODE', 'local-dev')
    
    # Get API base URL for HTTP calls - different defaults for each mode
    if _MODE == 'local-dev':
        default_api_url = 'http://localhost:8000'
    else:
        # For container modes (aws-mock, aws-prod), use host.docker.internal
        default_api_url = 'http://host.docker.internal:8000'
    
    _API_BASE_URL = os.environ.get('API_BASE_URL', default_api_url)
    
    if _MODE in ['aws-mock', 'aws-prod']:
        # Initialize S3 client
        _S3_CLIENT = boto3.client(
            's3',
            region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-west-2'),
            endpoint_url=os.environ.get('AWS_ENDPOINT_URL'),
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        _BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
        logger.info(f"Using S3 bucket: {_BUCKET_NAME}")
    else:
        # Local file system mode
        _STORAGE_DIR = Path(os.environ.get('STORAGE_DIR', 'storage'))
        _STORAGE_DIR.mkdir(exist_ok=True)
    
    logger.info(f"Storage initialized in {_MODE} mode")
    logger.info(f"API base URL: {_API_BASE_URL}")

# File operations (unchanged - keeping your existing code)
def download_file(file_path: str, local_path: str) -> str:
    """Download file from storage to local path."""
    if _MODE is None:
        init_storage()
        
    if _MODE in ['aws-mock', 'aws-prod']:
        try:
            if not _BUCKET_NAME:
                raise ValueError("S3 bucket name is not set")
                
            logger.info(f"Downloading '{file_path}' from bucket '{_BUCKET_NAME}' to '{local_path}'")
            _S3_CLIENT.download_file(
                Bucket=_BUCKET_NAME,
                Key=file_path,
                Filename=local_path
            )
            logger.info(f"Successfully downloaded {file_path}")
            return local_path
        except Exception as e:
            logger.error(f"Error downloading from S3: {str(e)}")
            raise
    else:
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
        dest_path = _STORAGE_DIR / file_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, str(dest_path))

def file_exists(file_path: str) -> bool:
    """Check if a file exists in storage."""
    if _MODE is None:
        init_storage()
        
    if _MODE in ['aws-mock', 'aws-prod']:
        try:
            _S3_CLIENT.head_object(Bucket=_BUCKET_NAME, Key=file_path)
            return True
        except Exception as e:
            if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == '404':
                return False
            raise
    else:
        return (_STORAGE_DIR / file_path).exists()

# Synchronous HTTP-based status updates
def _make_api_call(method: str, endpoint: str, data: dict) -> bool:
    """Make synchronous HTTP API call to FastAPI backend."""
    try:
        url = f"{_API_BASE_URL}{endpoint}"
        logger.info(f"Making {method} request to {url}")
        logger.debug(f"Request data: {data}")
        
        # Use requests for synchronous HTTP calls
        if method.upper() == 'PUT':
            response = requests.put(url, json=data, timeout=30.0)
        elif method.upper() == 'PATCH':
            response = requests.patch(url, json=data, timeout=30.0)
        else:
            response = requests.post(url, json=data, timeout=30.0)
        
        response.raise_for_status()
        logger.info(f"API call successful: {response.status_code}")
        logger.debug(f"Response: {response.text}")
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"API call failed: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in API call: {str(e)}")
        return False

def update_task_status(task_id: Union[str, int], status: str, timestamp: Optional[str] = None) -> None:
    """Update task status via HTTP API call."""
    if _MODE is None:
        init_storage()
    
    data = {
        "status": status,
        "timestamp": timestamp or datetime.utcnow().isoformat()
    }
    
    try:
        # Synchronous API call - no asyncio.run() needed
        success = _make_api_call(
            method="PATCH",
            endpoint=f"/v1/invoices/{task_id}/status",
            data=data
        )
        
        if success:
            logger.info(f"Updated task {task_id} status to {status} via API")
        else:
            logger.error(f"Failed to update task {task_id} status via API")
            
    except Exception as e:
        logger.error(f"Error updating task status: {str(e)}")
        # Don't raise - continue processing

def update_task_result(task_id: Union[str, int], 
                     result_data: Optional[Dict[str, Any]], 
                     status: str, 
                     completion_timestamp: Optional[str] = None, 
                     error_message: Optional[str] = None) -> None:
    """Update task result via HTTP API call."""
    if _MODE is None:
        init_storage()
    
    data = {
        "status": status,
        "completion_timestamp": completion_timestamp or datetime.utcnow().isoformat(),
        "error_message": error_message
    }
    
    if result_data:
        data["total_amount"] = result_data.get('total_amount')
        data["reported_weight"] = result_data.get('reported_weight')
    
    try:
        # Synchronous API call - no asyncio.run() needed
        success = _make_api_call(
            method="PATCH",
            endpoint=f"/v1/invoices/{task_id}/result",
            data=data
        )
        
        if success:
            logger.info(f"Updated task {task_id} result with status {status} via API")
        else:
            logger.error(f"Failed to update task {task_id} result via API")
            
    except Exception as e:
        logger.error(f"Error updating task result: {str(e)}")
        # Don't raise - continue processing