####################################
# --- Request/response schemas --- #
####################################

from datetime import datetime
from typing import List, Optional
from decimal import Decimal
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)
from typing_extensions import Self
from enum import Enum

DEFAULT_GET_FILES_PAGE_SIZE = 10
DEFAULT_GET_FILES_MIN_PAGE_SIZE = 10
DEFAULT_GET_FILES_MAX_PAGE_SIZE = 100
DEFAULT_GET_FILES_DIRECTORY = ""


class FileMetadata(BaseModel):
    """Metadata of a file."""
    file_path: str = Field(
        description="The path of the file.",
        json_schema_extra={"example": "invoices/2024/vendor123_invoice.pdf"},
    )
    last_modified: datetime = Field(description="The last modified date of the file.")
    size_bytes: int = Field(description="The size of the file in bytes.")

class GetFilesResponse(BaseModel):
    """Response model for `GET /v1/files`."""
    files: List[FileMetadata]
    next_page_token: Optional[str]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "files": [
                    {
                        "file_path": "invoices/2024/vendor123_invoice.pdf",
                        "last_modified": "2024-01-01T00:00:00Z",
                        "size_bytes": 512,
                    }
                ],
                "next_page_token": "next_page_token_example",
            }
        }
    )

class GetFilesQueryParams(BaseModel):
    """Query parameters for `GET /v1/files`."""
    page_size: int = Field(
        DEFAULT_GET_FILES_PAGE_SIZE,
        ge=DEFAULT_GET_FILES_MIN_PAGE_SIZE,
        le=DEFAULT_GET_FILES_MAX_PAGE_SIZE,
    )
    directory: str = Field(
        DEFAULT_GET_FILES_DIRECTORY,
        description="The directory to list files from.",
    )
    page_token: Optional[str] = Field(
        None,
        description="The token for the next page.",
    )

    @model_validator(mode="after")
    def check_page_token_is_mutually_exclusive_with_page_size_and_directory(self) -> Self:
        if self.page_token:
            get_files_query_params: dict = self.model_dump(exclude_unset=True)
            page_size_set = "page_size" in get_files_query_params.keys()
            directory_set = "directory" in get_files_query_params.keys()
            if page_size_set or directory_set:
                raise ValueError("page_token is mutually exclusive with page_size and directory")
        return self

class DeleteFileResponse(BaseModel):
    """Response model for `DELETE /v1/files/:file_path`."""
    message: str

class ExtractionStatus(str, Enum):
    """Enumeration for possible extraction statuses"""
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

class InvoiceMetadata(BaseModel):
    """Response model for invoice metadata."""
    invoice_id: int
    vendor_id: str
    vendor_name: str
    category_id: Optional[int] = None
    invoice_number: str
    invoice_date: datetime
    upload_date: datetime
    filename: str
    filepath: str
    reported_weight_kg: Optional[Decimal] = Field(None, decimal_places=2, ge=0)
    unit_price: Optional[Decimal] = Field(None, decimal_places=2, ge=0)
    total_amount: Optional[Decimal] = Field(None, decimal_places=2, ge=0)
    extraction_status: ExtractionStatus = Field(
        default=ExtractionStatus.PENDING,
        description="Status of the PDF extraction"
    )
    processing_date: Optional[datetime] = None
    completion_date: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invoice_id": 1,
                "vendor_id": "V20240101123456",
                "vendor_name": "Eco Recycling Corp",
                "category_id": 1,
                "invoice_number": "INV-2024-001",
                "invoice_date": "2024-01-01T00:00:00Z",
                "upload_date": "2024-01-01T12:34:56Z",
                "filename": "january_invoice.pdf",
                "filepath": "invoices/2024/january_invoice.pdf",
                "reported_weight_kg": "1000.50",
                "unit_price": "0.35",
                "total_amount": "350.18",
                "extraction_status": "pending",
                "processing_date": None,
                "completion_date": None,
                "error_message": None
            }
        }
    )

class PutFileResponse(BaseModel):
    """Response model for `PUT /v1/files/:file_path`."""
    file_path: str = Field(
        description="The path of the file.",
        json_schema_extra={"example": "invoices/2024/vendor123_invoice.pdf"},
    )
    message: str = Field(description="A message about the operation.")
    invoice_id: int = Field(description="The ID of the created invoice record")

class GetInvoiceResponse(BaseModel):
    """Response model for getting invoice details."""
    invoice: InvoiceMetadata

class ExtractionStatus(str, Enum):
    """Enumeration for possible extraction statuses"""
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

class InvoiceListItem(BaseModel):
    """Schema for invoice list item displayed in the table."""
    invoice_id: int = Field(description="Invoice ID")
    invoice_number: str = Field(description="Invoice number")
    category: str = Field(description="Material category")
    filename: str = Field(description="Name of the uploaded file")
    reported_weight_kg: Optional[Decimal] = Field(None, description="Weight in kilograms")
    total_amount: Optional[Decimal] = Field(None, description="Total price")
    upload_date: datetime = Field(description="Date of upload")
    extraction_status: ExtractionStatus = Field(description="Status of processing")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invoice_id": 13,
                "invoice_number": "INV-2024-0013",
                "category": "Metal",
                "filename": "invoice_13.pdf",
                "reported_weight_kg": "421.69",
                "total_amount": "8833.86",
                "upload_date": "2024-11-08T10:37:00Z",
                "extraction_status": "failed"
            }
        }
    )

class InvoiceListResponse(BaseModel):
    """Response model for listing invoices."""
    invoices: List[InvoiceListItem]
    total_count: int = Field(description="Total number of invoices")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invoices": [
                    {
                        "invoice_id": 13,
                        "invoice_number": "INV-2024-0013",
                        "category": "Metal",
                        "filename": "invoice_13.pdf",
                        "reported_weight_kg": "421.69",
                        "total_amount": "8833.86",
                        "upload_date": "2024-11-08T10:37:00Z",
                        "extraction_status": "failed"
                    }
                ],
                "total_count": 1
            }
        }
    )

class InvoiceStatusUpdate(BaseModel):
    status: str
    timestamp: Optional[str] = None

class InvoiceResultUpdate(BaseModel):
    status: str
    total_amount: Optional[float] = None
    reported_weight: Optional[float] = None
    completion_timestamp: Optional[str] = None
    error_message: Optional[str] = None
