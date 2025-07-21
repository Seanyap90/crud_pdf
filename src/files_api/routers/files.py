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
from files_api.services.database import get_invoice_service, get_category_service

router = APIRouter()

@router.get("/files", response_model=GetFilesResponse)
async def get_files(
    request: Request,
    query_params: GetFilesQueryParams = Depends()
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
        settings: Settings = request.app.state.settings
        
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

@router.put("/files/{file_path:path}", response_model=PutFileResponse)
async def upload_file(
    request: Request,
    response: Response,
    file_path: str = Path(..., description="The path for the file"),
    file_content: UploadFile = None,
    vendor_name: str = Query(..., description="Name of the vendor"),
    vendor_id: Optional[str] = Query(None, description="Vendor ID (optional)"),
    category_id: Optional[int] = Query(None, description="Category ID (optional)"),
    invoice_number: Optional[str] = Query(None, description="Invoice number (optional)"),
    invoice_date: Optional[str] = Query(None, description="Invoice date in YYYY-MM-DD format")
) -> PutFileResponse:
    """
    Upload a vendor invoice file with comprehensive metadata handling.
    
    This endpoint handles both file upload to S3 and invoice record creation,
    plus queues the file for VLM processing - all in one operation.
    
    Args:
        file_path: The path for the file
        file_content: The file to upload
        vendor_name: Name of the vendor (required)
        vendor_id: Vendor ID (optional)
        category_id: Category ID (optional)
        invoice_number: Invoice number (optional)
        invoice_date: Invoice date in YYYY-MM-DD format (optional)
        
    Returns:
        PutFileResponse: Upload confirmation with invoice metadata
    """
    if not file_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided"
        )
    
    try:
        settings: Settings = request.app.state.settings
        
        # Check if file exists in S3
        object_already_exists_at_path = object_exists_in_s3(settings.s3_bucket_name, object_key=file_path)
        if object_already_exists_at_path:
            message = f"Existing invoice updated at path: /{file_path}"
            response.status_code = status.HTTP_200_OK
        else:
            message = f"New invoice uploaded at path: /{file_path}"
            response.status_code = status.HTTP_201_CREATED
        
        # Read file content
        file_bytes = await file_content.read()
        
        # Upload to S3
        upload_s3_object(
            bucket_name=settings.s3_bucket_name,
            object_key=file_path,
            file_content=file_bytes,
            content_type=file_content.content_type
        )
        
        # Add initial invoice record using database service
        invoice_service = get_invoice_service()
        
        # Convert invoice_date string to datetime if provided
        invoice_date_obj = None
        if invoice_date:
            invoice_date_obj = datetime.fromisoformat(invoice_date)
        
        # Get category name if category_id is provided
        category_name = None
        if category_id:
            category_service = get_category_service()
            category_data = category_service.get_category_by_id(category_id)
            if category_data:
                category_name = category_data.get('category_name')
            else:
                # Fallback to default categories for category IDs 1-10
                default_categories = {
                    1: "General Waste", 2: "Recyclable", 3: "Hazardous", 4: "Organic", 5: "Metal",
                    6: "Paper", 7: "Plastic", 8: "Glass", 9: "Electronic", 10: "Construction"
                }
                category_name = default_categories.get(category_id, f"Category {category_id}")
        
        invoice_id = invoice_service.create_invoice(
            filename=file_content.filename,
            filepath=file_path,
            vendor_name=vendor_name,
            vendor_id=vendor_id,
            category_id=category_id,
            category_name=category_name,
            invoice_number=invoice_number,
            invoice_date=invoice_date_obj
        )
        
        # Queue PDF for processing
        queue = QueueFactory.get_queue_handler()
        await queue.add_task({
            "task_type": "process_invoice",
            "file_info": {
                "filepath": file_path,
                "invoice_id": invoice_id
            }
        })
        
        return PutFileResponse(
            file_path=f"{file_path}",
            message=message,
            invoice_id=invoice_id
        )
        
    except Exception as e:
        print(f"Upload error: {str(e)}")  # Debug print
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}"
        )

@router.get("/files/{file_key}")
async def get_file(
    request: Request,
    file_key: str = Path(..., description="The key/path of the file to retrieve")
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
        settings: Settings = request.app.state.settings
        
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
    request: Request,
    file_key: str = Path(..., description="The key/path of the file to delete")
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
        settings: Settings = request.app.state.settings
        
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