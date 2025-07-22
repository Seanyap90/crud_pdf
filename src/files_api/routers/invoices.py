from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status
)
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from typing import Optional
from files_api.schemas import (
    InvoiceMetadata,
    GetInvoiceResponse,
    InvoiceListResponse,
    InvoiceListItem,
    InvoiceStatusUpdate,
    InvoiceResultUpdate
)
from files_api.config.settings import Settings
from files_api.services.database import get_invoice_service, get_category_service
from files_api.adapters.queue import QueueFactory

router = APIRouter()

@router.post("/invoices/{file_key}/process")
async def process_invoice(
    request: Request,
    file_key: str = Path(..., description="The key/path of the invoice file to process")
):
    """
    Submit an invoice file for VLM processing.
    
    Args:
        file_key: The key/path of the invoice file to process
        settings: Application settings
        
    Returns:
        dict: Processing confirmation with task details
    """
    try:
        settings: Settings = request.app.state.settings
        
        # Get queue handler
        queue = QueueFactory.get_queue_handler()
        
        # Create task for VLM processing
        task_data = {
            "file_key": file_key,
            "task_type": "invoice_processing",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "queued"
        }
        
        # Submit task to queue
        task_id = await queue.send_message(task_data)
        
        # Store initial invoice record
        invoice_service = get_invoice_service()
        invoice_service.create_invoice(
            file_key=file_key,
            task_id=task_id,
            status="processing"
        )
        
        return {
            "message": f"Invoice processing started for '{file_key}'",
            "task_id": task_id,
            "file_key": file_key,
            "status": "queued"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start invoice processing: {str(e)}"
        )

@router.get("/invoices", response_model=InvoiceListResponse)
async def get_invoices(
    request: Request,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of invoices to return"),
    offset: int = Query(0, ge=0, description="Number of invoices to skip"),
    status_filter: Optional[str] = Query(None, description="Filter by processing status")
):
    """
    Retrieve a list of processed invoices with optional filtering.
    
    Args:
        limit: Maximum number of invoices to return
        offset: Number of invoices to skip for pagination
        status_filter: Optional status filter
        
    Returns:
        InvoiceListResponse: List of invoices with metadata
    """
    try:
        settings: Settings = request.app.state.settings
        
        invoice_service = get_invoice_service()
        
        # Get invoices with filtering using list_invoices
        invoices, total_count = invoice_service.list_invoices(
            limit=limit,
            status=status_filter
        )
        
        # Apply offset (since list_invoices doesn't support offset)
        invoices = invoices[offset:offset + limit]
        
        # Convert to response format (invoices already have correct NoSQL structure)
        invoice_items = [
            InvoiceListItem(
                invoice_id=invoice.get('invoice_id'),
                invoice_number=invoice.get('invoice_number', ''),
                vendor=invoice.get('vendor', {}),
                category=invoice.get('category'),
                filename=invoice.get('filename', ''),
                reported_weight_kg=invoice.get('reported_weight_kg'),
                total_amount=invoice.get('total_amount'),
                upload_date=invoice.get('upload_date'),
                extraction_status=invoice.get('extraction_status', 'pending')
            )
            for invoice in invoices
        ]
        
        return InvoiceListResponse(
            invoices=invoice_items,
            total_count=total_count,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve invoices: {str(e)}"
        )

@router.get("/{vendor_id}/invoices", response_model=InvoiceListResponse)
async def get_vendor_invoices(
    request: Request,
    vendor_id: str = Path(..., description="The vendor ID to filter invoices"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of invoices to return"),
    offset: int = Query(0, ge=0, description="Number of invoices to skip"),
    status_filter: Optional[str] = Query(None, description="Filter by processing status")
):
    """
    Retrieve invoices for a specific vendor.
    
    Args:
        vendor_id: The vendor ID to filter invoices
        limit: Maximum number of invoices to return
        offset: Number of invoices to skip for pagination
        status_filter: Optional status filter
        
    Returns:
        InvoiceListResponse: List of invoices for the vendor
    """
    try:
        settings: Settings = request.app.state.settings
        
        invoice_service = get_invoice_service()
        
        # Get invoices filtered by vendor_id using list_invoices
        invoices, total_invoices = invoice_service.list_invoices(
            vendor_id=vendor_id,
            limit=limit + offset,  # Get more to handle offset
            status=status_filter
        )
        
        # Apply offset and limit
        invoices = invoices[offset:offset + limit]
        
        # Convert to response format (invoices already have correct NoSQL structure)
        invoice_items = [
            InvoiceListItem(
                invoice_id=invoice.get('invoice_id'),
                invoice_number=invoice.get('invoice_number', ''),
                vendor=invoice.get('vendor', {}),
                category=invoice.get('category'),
                filename=invoice.get('filename', ''),
                reported_weight_kg=invoice.get('reported_weight_kg'),
                total_amount=invoice.get('total_amount'),
                upload_date=invoice.get('upload_date'),
                extraction_status=invoice.get('extraction_status', 'pending')
            )
            for invoice in invoices
        ]
        
        return InvoiceListResponse(
            invoices=invoice_items,
            total_count=total_invoices,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve vendor invoices: {str(e)}"
        )

@router.get("/invoices/{invoice_id}", response_model=GetInvoiceResponse)
async def get_invoice(
    request: Request,
    invoice_id: int = Path(..., description="The ID of the invoice to retrieve")
):
    """
    Retrieve detailed information about a specific invoice.
    
    Args:
        invoice_id: The ID of the invoice to retrieve
        settings: Application settings
        
    Returns:
        GetInvoiceResponse: Detailed invoice information
    """
    try:
        settings: Settings = request.app.state.settings
        
        invoice_service = get_invoice_service()
        
        # Get invoice by ID
        invoice = invoice_service.get_invoice(invoice_id)
        
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invoice with ID {invoice_id} not found"
            )
        
        return GetInvoiceResponse(
            invoice=InvoiceMetadata(
                invoice_id=invoice.get('invoice_id'),
                vendor=invoice.get('vendor', {}),
                category=invoice.get('category'),
                invoice_number=invoice.get('invoice_number', ''),
                invoice_date=invoice.get('invoice_date'),
                upload_date=invoice.get('upload_date'),
                filename=invoice.get('filename', ''),
                filepath=invoice.get('filepath', ''),
                reported_weight_kg=invoice.get('reported_weight_kg'),
                unit_price=invoice.get('unit_price'),
                total_amount=invoice.get('total_amount'),
                extraction_status=invoice.get('extraction_status', 'pending'),
                processing_date=invoice.get('processing_date'),
                completion_date=invoice.get('completion_date'),
                error_message=invoice.get('error_message')
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve invoice: {str(e)}"
        )

@router.patch("/invoices/{invoice_id}/status")
async def update_invoice_status(
    request: Request,
    invoice_id: int = Path(..., description="The ID of the invoice to update"),
    status_update: InvoiceStatusUpdate = ...
):
    """
    Update the processing status of an invoice.
    
    Args:
        invoice_id: The ID of the invoice to update
        status_update: New status information
        settings: Application settings
        
    Returns:
        dict: Update confirmation
    """
    try:
        settings: Settings = request.app.state.settings
        
        invoice_service = get_invoice_service()
        
        # Update invoice status
        updated = invoice_service.update_invoice_status(
            invoice_id=invoice_id,
            status=status_update.status,
            error_message=status_update.error_message
        )
        
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invoice with ID {invoice_id} not found"
            )
        
        return {
            "message": f"Invoice {invoice_id} status updated to '{status_update.status}'",
            "invoice_id": invoice_id,
            "status": status_update.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update invoice status: {str(e)}"
        )

@router.patch("/invoices/{invoice_id}/result")
async def update_invoice_result(
    request: Request,
    invoice_id: int = Path(..., description="The ID of the invoice to update"),
    result_update: InvoiceResultUpdate = ...
):
    """
    Update the processing result of an invoice.
    
    Args:
        invoice_id: The ID of the invoice to update
        result_update: Processing result data
        settings: Application settings
        
    Returns:
        dict: Update confirmation
    """
    try:
        settings: Settings = request.app.state.settings
        
        invoice_service = get_invoice_service()
        
        # Update invoice result using the existing update_invoice_status method
        updated = invoice_service.update_invoice_status(
            invoice_id=invoice_id,
            status=result_update.status,
            total_amount=result_update.total_amount,
            reported_weight_kg=result_update.reported_weight,
            error_message=result_update.error_message
        )
        
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invoice with ID {invoice_id} not found"
            )
        
        return {
            "message": f"Invoice {invoice_id} processing result updated",
            "invoice_id": invoice_id,
            "status": result_update.status,
            "total_amount": result_update.total_amount,
            "reported_weight": result_update.reported_weight
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update invoice result: {str(e)}"
        )