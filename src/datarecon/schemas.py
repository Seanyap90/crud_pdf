from typing import List, Optional
from pydantic import BaseModel, Field


class DeviceMeasurementSummary(BaseModel):
    device_id: str
    gateway_id: str
    weight_kg: float = Field(description="Sum of weight_kg for this device")
    event_count: int = Field(description="Number of measurement events from this device")


class ReconciliationLineItem(BaseModel):
    vendor_name: str
    category: str
    invoice_weight_kg: Optional[float] = None
    measured_weight_kg: Optional[float] = None
    discrepancy_kg: Optional[float] = None
    discrepancy_pct: Optional[float] = None
    status: str = Field(description="within_tolerance | over_tolerance | invoice_only | measurement_only")
    invoice_count: int = 0
    measurement_count: int = 0
    measurements_by_device: List[DeviceMeasurementSummary] = Field(default_factory=list)


class ReconciliationSummary(BaseModel):
    total_line_items: int = 0
    within_tolerance: int = 0
    over_tolerance: int = 0
    invoice_only: int = 0
    measurement_only: int = 0
    total_invoice_weight_kg: float = 0.0
    total_measured_weight_kg: float = 0.0


class ReconciliationResponse(BaseModel):
    period: str = Field(description="YYYY-MM format")
    tolerance_pct: float
    generated_at: str = Field(description="ISO timestamp")
    summary: ReconciliationSummary
    line_items: List[ReconciliationLineItem]


class VendorResponse(BaseModel):
    vendor_id: str
    vendor_name: str
    waste_type: str = Field(description="Material category handled by vendor")
