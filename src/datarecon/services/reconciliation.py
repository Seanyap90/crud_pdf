import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from datarecon.schemas import (
    DeviceMeasurementSummary,
    ReconciliationLineItem,
    ReconciliationResponse,
    ReconciliationSummary,
    VendorResponse,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(ts_value) -> Optional[datetime]:
    """Parse timestamp from various formats stored in documents."""
    if ts_value is None:
        return None
    if isinstance(ts_value, datetime):
        return ts_value
    if isinstance(ts_value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            try:
                return datetime.strptime(ts_value, fmt)
            except (ValueError, TypeError):
                continue
    return None


def _in_month(ts_value, year: int, month: int) -> bool:
    """Check if a timestamp falls within the given year/month."""
    dt = _parse_timestamp(ts_value)
    if dt is None:
        return False
    return dt.year == year and dt.month == month


class ReconciliationService:
    def __init__(self, adapter):
        self.adapter = adapter

    def get_vendors(self) -> List[VendorResponse]:
        """Get distinct vendors from completed invoices."""
        docs = self.adapter.query_documents(
            "vendor_invoices", {"extraction_status": "completed"}, limit=10000
        )
        seen = {}
        for doc in docs:
            vendor = doc.get("vendor", {})
            vid = vendor.get("vendor_id", "")
            if vid and vid not in seen:
                category = doc.get("category", {})
                seen[vid] = VendorResponse(
                    vendor_id=vid,
                    vendor_name=vendor.get("vendor_name", ""),
                    waste_type=category.get("category_name", "") if category else "",
                )
        return list(seen.values())

    def reconcile(
        self,
        year: int,
        month: int,
        tolerance_pct: float,
        vendor_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> ReconciliationResponse:
        invoices = self._fetch_invoices(year, month)
        measurements = self._fetch_measurements(year, month)

        invoice_agg = self._aggregate_invoices(invoices)
        measurement_agg = self._aggregate_measurements(measurements)

        line_items = self._join_and_compare(
            invoice_agg, measurement_agg, tolerance_pct, vendor_filter, category_filter
        )
        summary = self._build_summary(line_items)

        return ReconciliationResponse(
            period=f"{year}-{month:02d}",
            tolerance_pct=tolerance_pct,
            generated_at=datetime.utcnow().isoformat(),
            summary=summary,
            line_items=line_items,
        )

    def _fetch_invoices(self, year: int, month: int) -> List[Dict[str, Any]]:
        all_invoices = self.adapter.query_documents(
            "vendor_invoices", {"extraction_status": "completed"}, limit=10000
        )
        return [inv for inv in all_invoices if _in_month(inv.get("invoice_date"), year, month)]

    def _fetch_measurements(self, year: int, month: int) -> List[Dict[str, Any]]:
        all_measurements = self.adapter.query_documents(
            "measurements", {"measurement_type": "weight_measurement"}, limit=50000
        )
        return [m for m in all_measurements if _in_month(m.get("timestamp"), year, month)]

    def _aggregate_invoices(
        self, invoices: List[Dict[str, Any]]
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Group invoices by (vendor_name, category) and sum weights."""
        agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for inv in invoices:
            vendor_name = (inv.get("vendor", {}).get("vendor_name") or "").strip()
            category = ""
            cat_obj = inv.get("category")
            if cat_obj:
                category = (cat_obj.get("category_name") or "").strip()
            key = (vendor_name.lower(), category.lower())

            if key not in agg:
                agg[key] = {
                    "vendor_name": vendor_name,
                    "category": category,
                    "weight": 0.0,
                    "count": 0,
                    "total_amount": 0.0,
                }
            weight = inv.get("reported_weight_kg")
            if weight is not None:
                agg[key]["weight"] += float(weight)
            agg[key]["count"] += 1
            amount = inv.get("total_amount")
            if amount is not None:
                agg[key]["total_amount"] += float(amount)
        return agg

    def _aggregate_measurements(
        self, measurements: List[Dict[str, Any]]
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Group measurements by (vendor, category) and sum weights with per-device breakdown."""
        agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for m in measurements:
            payload = m.get("payload", {})
            device_info = m.get("device_info", {})

            vendor = (payload.get("vendor") or "").strip()
            category = (payload.get("material_category") or "").strip()
            if not vendor:
                continue

            key = (vendor.lower(), category.lower())
            if key not in agg:
                agg[key] = {
                    "vendor": vendor,
                    "category": category,
                    "weight": 0.0,
                    "count": 0,
                    "devices": defaultdict(lambda: {"weight": 0.0, "count": 0, "gateway_id": ""}),
                }

            weight = float(payload.get("weight_kg", 0))
            agg[key]["weight"] += weight
            agg[key]["count"] += 1

            device_id = device_info.get("device_id", "unknown")
            gateway_id = device_info.get("gateway_id", "unknown")
            agg[key]["devices"][device_id]["weight"] += weight
            agg[key]["devices"][device_id]["count"] += 1
            agg[key]["devices"][device_id]["gateway_id"] = gateway_id

        return agg

    def _join_and_compare(
        self,
        invoice_agg: Dict[Tuple[str, str], Dict[str, Any]],
        measurement_agg: Dict[Tuple[str, str], Dict[str, Any]],
        tolerance_pct: float,
        vendor_filter: Optional[str],
        category_filter: Optional[str],
    ) -> List[ReconciliationLineItem]:
        all_keys = set(invoice_agg.keys()) | set(measurement_agg.keys())
        line_items = []

        for key in sorted(all_keys):
            inv_data = invoice_agg.get(key)
            meas_data = measurement_agg.get(key)

            vendor_name = (inv_data or meas_data)["vendor_name" if inv_data else "vendor"]
            category = (inv_data or meas_data)["category"]

            if vendor_filter and vendor_filter.lower() != vendor_name.lower():
                continue
            if category_filter and category_filter.lower() != category.lower():
                continue

            inv_weight = inv_data["weight"] if inv_data else None
            meas_weight = meas_data["weight"] if meas_data else None

            # Compute discrepancy
            discrepancy_kg = None
            discrepancy_pct = None
            if inv_weight is not None and meas_weight is not None:
                discrepancy_kg = round(inv_weight - meas_weight, 2)
                if inv_weight > 0:
                    discrepancy_pct = round(abs(discrepancy_kg) / inv_weight * 100, 2)

            # Determine status
            if inv_data and meas_data:
                if discrepancy_pct is not None and discrepancy_pct > tolerance_pct:
                    status = "over_tolerance"
                else:
                    status = "within_tolerance"
            elif inv_data and not meas_data:
                status = "invoice_only"
            else:
                status = "measurement_only"

            # Per-device breakdown
            devices = []
            if meas_data:
                for device_id, dev_info in meas_data["devices"].items():
                    devices.append(
                        DeviceMeasurementSummary(
                            device_id=device_id,
                            gateway_id=dev_info["gateway_id"],
                            weight_kg=round(dev_info["weight"], 2),
                            event_count=dev_info["count"],
                        )
                    )

            line_items.append(
                ReconciliationLineItem(
                    vendor_name=vendor_name,
                    category=category,
                    invoice_weight_kg=round(inv_weight, 2) if inv_weight is not None else None,
                    measured_weight_kg=round(meas_weight, 2) if meas_weight is not None else None,
                    discrepancy_kg=discrepancy_kg,
                    discrepancy_pct=discrepancy_pct,
                    status=status,
                    invoice_count=inv_data["count"] if inv_data else 0,
                    measurement_count=meas_data["count"] if meas_data else 0,
                    measurements_by_device=devices,
                )
            )

        return line_items

    def _build_summary(self, line_items: List[ReconciliationLineItem]) -> ReconciliationSummary:
        summary = ReconciliationSummary(total_line_items=len(line_items))
        for item in line_items:
            if item.status == "within_tolerance":
                summary.within_tolerance += 1
            elif item.status == "over_tolerance":
                summary.over_tolerance += 1
            elif item.status == "invoice_only":
                summary.invoice_only += 1
            elif item.status == "measurement_only":
                summary.measurement_only += 1

            if item.invoice_weight_kg is not None:
                summary.total_invoice_weight_kg += item.invoice_weight_kg
            if item.measured_weight_kg is not None:
                summary.total_measured_weight_kg += item.measured_weight_kg

        summary.total_invoice_weight_kg = round(summary.total_invoice_weight_kg, 2)
        summary.total_measured_weight_kg = round(summary.total_measured_weight_kg, 2)
        return summary
