from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status
)
from fastapi.responses import StreamingResponse, JSONResponse
from datetime import datetime, timezone
from typing import Optional
from files_api.s3.delete_objects import delete_s3_object
from files_api.s3.read_objects import (
    fetch_s3_object,
    fetch_s3_objects_metadata,
    fetch_s3_objects_using_page_token,
    object_exists_in_s3,
)
from files_api.s3.write_objects import upload_s3_object
from files_api.schemas import (
    FileMetadata,
    GetFilesQueryParams,
    GetFilesResponse,
    PutFileResponse,
)
from files_api.config.settings import Settings
from files_api.adapters.queue import QueueFactory

router = APIRouter()

@router.get("/files", response_model=GetFilesResponse)
async def get_files(
    query_params: GetFilesQueryParams = Depends(),
    settings: Settings = Depends()
):
    """
    Retrieve a list of files with optional filtering and pagination.
    
    Args:
        query_params: Query parameters for filtering and pagination
        settings: Application settings
        
    Returns:
        GetFilesResponse: List of files with metadata and pagination info
    """
    try:
        if query_params.page_token:
            # Use page token for pagination
            files_data = fetch_s3_objects_using_page_token(
                bucket_name=settings.s3_bucket_name,
                page_token=query_params.page_token,
                max_keys=query_params.limit
            )
        else:
            # Fetch files with optional prefix filtering
            files_data = fetch_s3_objects_metadata(
                bucket_name=settings.s3_bucket_name,
                prefix=query_params.prefix,
                max_keys=query_params.limit
            )
        
        return GetFilesResponse(**files_data)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve files: {str(e)}"
        )

@router.put("/files/{file_key}", response_model=PutFileResponse)
async def put_file(
    file_key: str = Path(..., description="The key/path for the file"),
    file: UploadFile = None,
    settings: Settings = Depends()
):
    """
    Upload a file to storage.
    
    Args:
        file_key: The key/path for the file
        file: The file to upload
        settings: Application settings
        
    Returns:
        PutFileResponse: Upload confirmation with metadata
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided"
        )
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Upload to storage
        upload_result = upload_s3_object(
            bucket_name=settings.s3_bucket_name,
            object_key=file_key,
            file_content=file_content,
            content_type=file.content_type
        )
        
        return PutFileResponse(
            file_key=file_key,
            size=len(file_content),
            content_type=file.content_type,
            upload_time=datetime.now(timezone.utc),
            **upload_result
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}"
        )

@router.get("/files/{file_key}")
async def get_file(
    file_key: str = Path(..., description="The key/path of the file to retrieve"),
    settings: Settings = Depends()
):
    """
    Download a file from storage.
    
    Args:
        file_key: The key/path of the file to retrieve
        settings: Application settings
        
    Returns:
        StreamingResponse: The file content as a stream
    """
    try:
        # Check if file exists
        if not object_exists_in_s3(settings.s3_bucket_name, file_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File '{file_key}' not found"
            )
        
        # Fetch file from storage
        file_obj = fetch_s3_object(settings.s3_bucket_name, file_key)
        
        return StreamingResponse(
            file_obj["Body"],
            media_type=file_obj.get("ContentType", "application/octet-stream"),
            headers={
                "Content-Disposition": f"attachment; filename={file_key.split('/')[-1]}"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve file: {str(e)}"
        )

@router.delete("/files/{file_key}")
async def delete_file(
    file_key: str = Path(..., description="The key/path of the file to delete"),
    settings: Settings = Depends()
):
    """
    Delete a file from storage.
    
    Args:
        file_key: The key/path of the file to delete
        settings: Application settings
        
    Returns:
        dict: Deletion confirmation
    """
    try:
        # Check if file exists
        if not object_exists_in_s3(settings.s3_bucket_name, file_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File '{file_key}' not found"
            )
        
        # Delete file from storage
        delete_s3_object(settings.s3_bucket_name, file_key)
        
        return {"message": f"File '{file_key}' deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}"
        )