"""
Invoice service for Files API NoSQL operations.
Handles invoice documents with embedded vendor and category information.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class InvoiceService:
    """Service for managing invoice documents with embedded vendor and category"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def create_invoice(
        self,
        filename: str,
        filepath: str,
        vendor_name: str,
        vendor_id: Optional[str] = None,
        category_id: Optional[int] = None,
        category_name: Optional[str] = None,
        invoice_number: Optional[str] = None,
        invoice_date: Optional[datetime] = None
    ) -> int:
        """Create a new invoice document with embedded vendor and category"""
        try:
            # Generate IDs if not provided
            if not vendor_id:
                vendor_id = f"V{datetime.now().strftime('%Y%m%d%H%M%S')}"
            if not invoice_number:
                invoice_number = f"INV{datetime.now().strftime('%Y%m%d%H%M%S')}"
            if not invoice_date:
                invoice_date = datetime.now()
            
            # Get next invoice ID
            count = self.adapter.count_documents('vendor_invoices')
            invoice_id = count + 1
            
            # Create vendor document
            vendor_doc = {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "created_at": datetime.now().isoformat(),
                "is_active": True
            }
            
            # Create category document if provided
            category_doc = None
            if category_id and category_name:
                category_doc = {
                    "category_id": category_id,
                    "category_name": category_name,
                    "description": None
                }
            
            # Create invoice document
            invoice_doc = {
                "invoice_id": invoice_id,
                "vendor": vendor_doc,
                "category": category_doc,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date.isoformat() if invoice_date else None,
                "upload_date": datetime.now().isoformat(),
                "filename": filename,
                "filepath": filepath,
                "reported_weight_kg": None,
                "unit_price": None,
                "total_amount": None,
                "extraction_status": "pending",
                "processing_date": None,
                "completion_date": None,
                "error_message": None
            }
            
            self.adapter.create_document('vendor_invoices', invoice_doc)
            logger.info(f"Created invoice document: {invoice_id}")
            return invoice_id
            
        except Exception as e:
            logger.error(f"Error creating invoice: {e}")
            raise
    
    def get_invoice(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """Get invoice document by ID"""
        try:
            return self.adapter.get_document('vendor_invoices', invoice_id)
        except Exception as e:
            logger.error(f"Error getting invoice: {e}")
            raise
    
    def update_invoice_status(
        self,
        invoice_id: int,
        status: str,
        reported_weight_kg: Optional[float] = None,
        unit_price: Optional[float] = None,
        total_amount: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Update invoice processing status and extracted data"""
        try:
            # Get existing document
            invoice_doc = self.adapter.get_document('vendor_invoices', invoice_id)
            if not invoice_doc:
                return False
            
            # Update status and timestamps
            timestamp_str = datetime.now().isoformat()
            invoice_doc["extraction_status"] = status
            invoice_doc["processing_date"] = timestamp_str
            
            if status == "completed":
                invoice_doc["completion_date"] = timestamp_str
            
            # Update extracted data if provided
            if reported_weight_kg is not None:
                invoice_doc["reported_weight_kg"] = reported_weight_kg
            if unit_price is not None:
                invoice_doc["unit_price"] = unit_price
            if total_amount is not None:
                invoice_doc["total_amount"] = total_amount
            if error_message is not None:
                invoice_doc["error_message"] = error_message
            
            return self.adapter.update_document('vendor_invoices', invoice_id, invoice_doc)
            
        except Exception as e:
            logger.error(f"Error updating invoice status: {e}")
            raise
    
    def list_invoices(
        self,
        vendor_id: Optional[str] = None,
        status: Optional[str] = None,
        category_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List invoices with filtering options"""
        try:
            # Build query
            query = {}
            if vendor_id:
                query["vendor.vendor_id"] = vendor_id
            if status:
                query["extraction_status"] = status
            if category_id:
                query["category.category_id"] = category_id
            
            # Get documents
            documents = self.adapter.query_documents('vendor_invoices', query, limit)
            
            # Apply date filtering if needed (done in Python since adapter doesn't support date ranges yet)
            if start_date or end_date:
                filtered_docs = []
                for doc in documents:
                    doc_date = datetime.fromisoformat(doc['upload_date'].replace('Z', '+00:00'))
                    if start_date and doc_date < start_date:
                        continue
                    if end_date and doc_date > end_date:
                        continue
                    filtered_docs.append(doc)
                documents = filtered_docs
            
            total_count = len(documents)
            
            return documents, total_count
            
        except Exception as e:
            logger.error(f"Error listing invoices: {e}")
            raise
    
    def search_invoices(self, search_term: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search invoices by vendor name, invoice number, or filename"""
        try:
            # Get all invoices (in a real implementation, this would be optimized)
            all_invoices, _ = self.list_invoices(limit=10000)
            
            # Filter by search term (case-insensitive)
            search_term = search_term.lower()
            matching_invoices = []
            
            for invoice in all_invoices:
                # Search in multiple fields
                searchable_text = " ".join([
                    invoice.get('vendor', {}).get('vendor_name', ''),
                    invoice.get('invoice_number', ''),
                    invoice.get('filename', ''),
                    invoice.get('category', {}).get('category_name', '') if invoice.get('category') else ''
                ]).lower()
                
                if search_term in searchable_text:
                    matching_invoices.append(invoice)
            
            # Sort by relevance and date
            matching_invoices.sort(key=lambda x: x.get('upload_date', ''), reverse=True)
            return matching_invoices[:limit]
            
        except Exception as e:
            logger.error(f"Error searching invoices: {e}")
            raise
    
    def get_invoices_by_vendor(self, vendor_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all invoices for a specific vendor"""
        try:
            invoices, _ = self.list_invoices(vendor_id=vendor_id, limit=limit)
            return invoices
        except Exception as e:
            logger.error(f"Error getting invoices by vendor: {e}")
            raise
    
    def get_invoices_by_category(self, category_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all invoices for a specific category"""
        try:
            invoices, _ = self.list_invoices(category_id=category_id, limit=limit)
            return invoices
        except Exception as e:
            logger.error(f"Error getting invoices by category: {e}")
            raise
    
    def get_pending_invoices(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all invoices with pending processing status"""
        try:
            invoices, _ = self.list_invoices(status="pending", limit=limit)
            return invoices
        except Exception as e:
            logger.error(f"Error getting pending invoices: {e}")
            raise
    
    def update_invoice_category(self, invoice_id: int, category_id: int, category_name: str) -> bool:
        """Update the category of an invoice"""
        try:
            # Get existing document
            invoice_doc = self.adapter.get_document('vendor_invoices', invoice_id)
            if not invoice_doc:
                return False
            
            # Update category
            invoice_doc["category"] = {
                "category_id": category_id,
                "category_name": category_name,
                "description": None
            }
            
            return self.adapter.update_document('vendor_invoices', invoice_id, invoice_doc)
            
        except Exception as e:
            logger.error(f"Error updating invoice category: {e}")
            raise
    
    def delete_invoice(self, invoice_id: int) -> bool:
        """Delete an invoice document"""
        try:
            return self.adapter.delete_document('vendor_invoices', invoice_id)
        except Exception as e:
            logger.error(f"Error deleting invoice: {e}")
            raise
    
    def get_invoice_statistics(self) -> Dict[str, Any]:
        """Get overall invoice statistics"""
        try:
            # Get all invoices
            all_invoices, total_count = self.list_invoices(limit=10000)
            
            # Calculate statistics
            status_counts = {}
            vendor_counts = {}
            category_counts = {}
            total_amount = 0.0
            total_weight = 0.0
            
            for invoice in all_invoices:
                # Count statuses
                status = invoice.get('extraction_status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
                
                # Count vendors
                vendor_id = invoice.get('vendor', {}).get('vendor_id')
                if vendor_id:
                    vendor_counts[vendor_id] = vendor_counts.get(vendor_id, 0) + 1
                
                # Count categories
                category = invoice.get('category')
                if category:
                    cat_name = category.get('category_name', 'Unknown')
                    category_counts[cat_name] = category_counts.get(cat_name, 0) + 1
                
                # Sum amounts and weights
                if invoice.get('total_amount'):
                    total_amount += float(invoice['total_amount'])
                if invoice.get('reported_weight_kg'):
                    total_weight += float(invoice['reported_weight_kg'])
            
            return {
                'total_invoices': total_count,
                'status_breakdown': status_counts,
                'top_vendors': dict(sorted(vendor_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
                'category_breakdown': category_counts,
                'total_amount': round(total_amount, 2),
                'total_weight_kg': round(total_weight, 2),
                'average_amount': round(total_amount / total_count, 2) if total_count > 0 else 0,
                'average_weight': round(total_weight / total_count, 2) if total_count > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting invoice statistics: {e}")
            raise


# Global service instance
_invoice_service = None

def get_invoice_service(db_path: str = "recycling.db") -> InvoiceService:
    """Get or create invoice service instance"""
    global _invoice_service
    if _invoice_service is None or _invoice_service.db_path != db_path:
        _invoice_service = InvoiceService(db_path)
    return _invoice_service