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
from datetime import datetime
from typing import Optional
from decimal import Decimal
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
    InvoiceMetadata,
    GetInvoiceResponse,
    InvoiceListResponse,
    InvoiceListItem
)
from files_api.settings import Settings
from files_api.database.local import (
    add_invoice, 
    get_invoice_metadata,
    get_invoices_list
)
from files_api.msg_queue import QueueFactory

ROUTER = APIRouter(tags=["Files"])

@ROUTER.put(
    "/v1/files/{file_path:path}",
    responses={
        status.HTTP_200_OK: {"model": PutFileResponse},
        status.HTTP_201_CREATED: {"model": PutFileResponse},
    },
)
async def upload_file(
    request: Request,
    response: Response,
    file_path: str,
    file_content: UploadFile,
    vendor_name: str = Query(..., description="Name of the vendor"),
    vendor_id: Optional[str] = Query(None, description="Vendor ID (optional)"),
    category_id: Optional[int] = Query(None, description="Category ID (optional)"),
    invoice_number: Optional[str] = Query(None, description="Invoice number (optional)"),
    invoice_date: Optional[str] = Query(None, description="Invoice date in YYYY-MM-DD format")
) -> PutFileResponse:
    """Upload a vendor invoice file."""
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
            content_type=file_content.content_type,
        )

        # Add initial invoice record - only basic info
        invoice_id = add_invoice(
            filename=file_content.filename,
            filepath=file_path,
            vendor_name=vendor_name,
            vendor_id=vendor_id,
            category_id=category_id,
            invoice_number=invoice_number,
            invoice_date=invoice_date
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
        raise


@ROUTER.get("/v1/files")
async def list_files(
    request: Request,
    query_params: GetFilesQueryParams = Depends(),
) -> GetFilesResponse:
    """List files with pagination."""
    settings: Settings = request.app.state.settings
    if query_params.page_token:
        files, next_page_token = fetch_s3_objects_using_page_token(
            bucket_name=settings.s3_bucket_name,
            continuation_token=query_params.page_token,
            max_keys=query_params.page_size,
        )
    else:
        files, next_page_token = fetch_s3_objects_metadata(
            bucket_name=settings.s3_bucket_name,
            prefix=query_params.directory,
            max_keys=query_params.page_size,
        )

    file_metadata_objs = [
        FileMetadata(
            file_path=f"{item['Key']}",
            last_modified=item["LastModified"],
            size_bytes=item["Size"],
        )
        for item in files
    ]
    return GetFilesResponse(files=file_metadata_objs, next_page_token=next_page_token if next_page_token else None)


@ROUTER.head(
    "/v1/files/{file_path:path}",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "File not found for the given `file_path`.",
        },
        status.HTTP_200_OK: {
            "headers": {
                "Content-Type": {
                    "description": "The [MIME type](https://developer.mozilla.org/en-US/docs/Web/HTTP/Basics_of_HTTP/MIME_types/Common_types) of the file.",
                    "example": "text/plain",
                    "schema": {"type": "string"},
                },
                "Content-Length": {
                    "description": "The size of the file in bytes.",
                    "example": 512,
                    "schema": {"type": "integer"},
                },
                "Last-Modified": {
                    "description": "The last modified date of the file.",
                    "example": "Thu, 01 Jan 2022 00:00:00 GMT",
                    "schema": {"type": "string", "format": "date-time"},
                },
            }
        },
    },
)
async def get_file_metadata(request: Request, file_path: str, response: Response) -> Response:
    """
    Retrieve file metadata.

    Note: by convention, HEAD requests MUST NOT return a body in the response.
    """
    settings: Settings = request.app.state.settings

    object_exists = object_exists_in_s3(bucket_name=settings.s3_bucket_name, object_key=file_path)
    if not object_exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    get_object_response = fetch_s3_object(settings.s3_bucket_name, object_key=file_path)
    response.headers["Content-Type"] = get_object_response["ContentType"]
    response.headers["Content-Length"] = str(get_object_response["ContentLength"])
    response.headers["Last-Modified"] = get_object_response["LastModified"].strftime("%a, %d %b %Y %H:%M:%S GMT")
    response.status_code = status.HTTP_200_OK
    return response


@ROUTER.get(
    "/v1/files/{file_path:path}",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "File not found for the given `file_path`.",
        },
        status.HTTP_200_OK: {
            "description": "The file content.",
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
    },
)
async def get_file(
    request: Request,
    file_path: str,
) -> StreamingResponse:
    """Retrieve a file."""
    settings: Settings = request.app.state.settings

    object_exists = object_exists_in_s3(bucket_name=settings.s3_bucket_name, object_key=file_path)
    if not object_exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    get_object_response = fetch_s3_object(settings.s3_bucket_name, object_key=file_path)
    return StreamingResponse(
        content=get_object_response["Body"],
        media_type=get_object_response["ContentType"],
    )


@ROUTER.delete(
    "/v1/files/{file_path:path}",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "File not found for the given `file_path`.",
        },
        status.HTTP_204_NO_CONTENT: {
            "description": "File deleted successfully.",
        },
    },
)
async def delete_file(
    request: Request,
    file_path: str,
    response: Response,
) -> Response:
    """
    Delete a file.

    NOTE: DELETE requests MUST NOT return a body in the response.
    """
    settings: Settings = request.app.state.settings
    if not object_exists_in_s3(settings.s3_bucket_name, object_key=file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    delete_s3_object(settings.s3_bucket_name, object_key=file_path)

    response.status_code = status.HTTP_204_NO_CONTENT
    return response

@ROUTER.get(
    "/v1/invoices/{invoice_id}",
    response_model=GetInvoiceResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Invoice not found.",
        },
        status.HTTP_200_OK: {
            "description": "Invoice metadata retrieved successfully.",
            "model": GetInvoiceResponse
        },
    },
)
async def get_invoice(
    request: Request,
    invoice_id: int = Path(..., description="The ID of the invoice to retrieve")
) -> GetInvoiceResponse:
    """Retrieve invoice metadata."""
    invoice_data = get_invoice_metadata(invoice_id)
    
    if not invoice_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice with ID {invoice_id} not found"
        )

    # Convert datetime strings to datetime objects
    for field in ['invoice_date', 'upload_date', 'processing_date', 'completion_date']:
        if invoice_data.get(field):
            invoice_data[field] = datetime.fromisoformat(invoice_data[field].replace('Z', '+00:00'))

    # Convert decimal strings to Decimal objects
    for field in ['reported_weight_kg', 'unit_price', 'total_amount']:
        if invoice_data.get(field):
            invoice_data[field] = Decimal(str(invoice_data[field]))

    return GetInvoiceResponse(invoice=InvoiceMetadata(**invoice_data))

@ROUTER.get(
    "/v1/{vendor_id}/invoices",
    response_model=InvoiceListResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "List of invoices retrieved successfully.",
            "model": InvoiceListResponse
        },
    },
)
async def list_invoices(
    request: Request,
    vendor_id: str = Path(..., description="The ID of the vendor to get invoices for")
) -> InvoiceListResponse:
    """Retrieve list of invoices for the table view."""
    invoices, total_count = get_invoices_list(vendor_id)
    
    return InvoiceListResponse(
        invoices=[InvoiceListItem(**invoice) for invoice in invoices],
        total_count=total_count
    )

@ROUTER.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint that returns status of various system components"""
    from files_api.msg_queue import QueueFactory
    from files_api.vlm.load_models import ModelManager
    
    health_status = {
        "status": "ok",
        "components": {
            "api": "ready",
            "queue": "initializing",
            "models": "initializing"
        },
        "ready": False
    }
    
    # Check queue status
    try:
        queue = QueueFactory.get_queue_handler()
        health_status["components"]["queue"] = "ready"
    except Exception as e:
        health_status["components"]["queue"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check models status WITHOUT loading them
    try:
        model_manager = ModelManager()
        model_status = model_manager.check_model_status()
        
        if model_status["ready"]:
            health_status["components"]["models"] = "ready"
        else:
            health_status["components"]["models"] = "loading"
            health_status["status"] = "initializing"
            
        # Add detailed model status for debugging
        health_status["models_detail"] = {
            "rag_loaded": model_status["rag_initialized"],
            "vlm_loaded": model_status["vlm_initialized"]
        }
    except Exception as e:
        health_status["components"]["models"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Set overall ready status - consider ready if API and queue are ready,
    # even if models are still loading (they'll load when needed)
    if all(v == "ready" for v in {k: health_status["components"][k] for k in ["api", "queue"]}.values()):
        health_status["ready"] = True
    
    return health_status
    