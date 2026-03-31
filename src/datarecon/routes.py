import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from datarecon.schemas import ReconciliationResponse, VendorResponse
from datarecon.services.reconciliation import ReconciliationService
from datarecon.settings import get_settings
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_service() -> ReconciliationService:
    settings = get_settings()
    adapter = get_nosql_adapter(settings.db_path)
    return ReconciliationService(adapter)


def _validate_period(year: int, month: int) -> None:
    now = datetime.utcnow()
    settings = get_settings()
    current_total = now.year * 12 + now.month
    requested_total = year * 12 + month
    if requested_total > current_total:
        raise HTTPException(status_code=400, detail="Cannot query future periods")
    if current_total - requested_total > settings.max_lookback_months:
        raise HTTPException(
            status_code=400,
            detail=f"Can only query up to {settings.max_lookback_months} months back from current month",
        )


@router.get("/vendors", response_model=List[VendorResponse])
async def list_vendors():
    service = _get_service()
    return service.get_vendors()


@router.get("/reconcile", response_model=ReconciliationResponse)
async def reconcile(
    year: Optional[int] = Query(default=None, description="Year (default: current)"),
    month: Optional[int] = Query(default=None, ge=1, le=12, description="Month 1-12 (default: current)"),
    tolerance_pct: Optional[float] = Query(default=None, ge=0, le=100, description="Tolerance % (default: 5.0)"),
    vendor: Optional[str] = Query(default=None, description="Filter by vendor name"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
):
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    settings = get_settings()
    tolerance_pct = tolerance_pct if tolerance_pct is not None else settings.default_tolerance_pct

    _validate_period(year, month)

    service = _get_service()
    return service.reconcile(year, month, tolerance_pct, vendor, category)


@router.get("/health")
async def health():
    return {"status": "ok", "service": "datarecon"}
