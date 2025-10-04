"""
Vendor service for Files API NoSQL operations.
Vendors are now embedded in invoice documents rather than stored separately.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class VendorService:
    """Service for managing vendor operations via embedded documents in invoices"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def get_vendor_by_id(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        """Get vendor by ID from any invoice document that contains it"""
        try:
            # Query invoices for this vendor
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': vendor_id
            }, limit=1)
            
            if invoices:
                return invoices[0]['vendor']
            return None
            
        except Exception as e:
            logger.error(f"Error getting vendor by ID: {e}")
            raise
    
    def get_vendor_by_name(self, vendor_name: str) -> Optional[Dict[str, Any]]:
        """Get vendor by name from any invoice document that contains it"""
        try:
            # Query invoices for this vendor name
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_name': vendor_name
            }, limit=1)
            
            if invoices:
                return invoices[0]['vendor']
            return None
            
        except Exception as e:
            logger.error(f"Error getting vendor by name: {e}")
            raise
    
    def list_all_vendors(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """Get all unique vendors from invoice documents"""
        try:
            # Get all invoices
            invoices = self.adapter.query_documents('vendor_invoices', {}, limit=10000)
            
            # Extract unique vendors
            vendors_map = {}
            for invoice in invoices:
                vendor = invoice.get('vendor')
                if not vendor:
                    continue
                
                vendor_id = vendor['vendor_id']
                
                # Skip inactive vendors if requested
                if not include_inactive and not vendor.get('is_active', True):
                    continue
                
                # Keep the most recently created vendor entry
                if vendor_id not in vendors_map:
                    vendors_map[vendor_id] = vendor
                else:
                    # Compare creation dates, keep the newer one
                    existing_created = vendors_map[vendor_id].get('created_at')
                    new_created = vendor.get('created_at')
                    
                    if new_created and (not existing_created or new_created > existing_created):
                        vendors_map[vendor_id] = vendor
            
            # Return sorted list
            vendors = list(vendors_map.values())
            return sorted(vendors, key=lambda x: x.get('vendor_name', ''))
            
        except Exception as e:
            logger.error(f"Error listing vendors: {e}")
            raise
    
    def get_vendor_statistics(self, vendor_id: str) -> Dict[str, Any]:
        """Get statistics for a specific vendor"""
        try:
            # Query all invoices for this vendor
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': vendor_id
            }, limit=10000)
            
            if not invoices:
                return {
                    'vendor_id': vendor_id,
                    'total_invoices': 0,
                    'total_amount': 0.0,
                    'total_weight_kg': 0.0,
                    'status_breakdown': {},
                    'date_range': None
                }
            
            # Calculate statistics
            total_invoices = len(invoices)
            total_amount = 0.0
            total_weight = 0.0
            status_counts = {}
            dates = []
            
            for invoice in invoices:
                # Sum amounts
                if invoice.get('total_amount'):
                    total_amount += float(invoice['total_amount'])
                
                # Sum weights
                if invoice.get('reported_weight_kg'):
                    total_weight += float(invoice['reported_weight_kg'])
                
                # Count statuses
                status = invoice.get('extraction_status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
                
                # Collect dates
                if invoice.get('invoice_date'):
                    dates.append(invoice['invoice_date'])
            
            # Determine date range
            date_range = None
            if dates:
                dates.sort()
                date_range = {
                    'earliest': dates[0],
                    'latest': dates[-1]
                }
            
            return {
                'vendor_id': vendor_id,
                'vendor_name': invoices[0]['vendor']['vendor_name'],
                'total_invoices': total_invoices,
                'total_amount': round(total_amount, 2),
                'total_weight_kg': round(total_weight, 2),
                'status_breakdown': status_counts,
                'date_range': date_range,
                'average_amount': round(total_amount / total_invoices, 2) if total_invoices > 0 else 0,
                'average_weight': round(total_weight / total_invoices, 2) if total_invoices > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting vendor statistics: {e}")
            raise
    
    def update_vendor_status(self, vendor_id: str, is_active: bool) -> bool:
        """Update vendor active status across all invoice documents"""
        try:
            # Get all invoices for this vendor
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': vendor_id
            }, limit=10000)
            
            if not invoices:
                return False
            
            updated_count = 0
            for invoice in invoices:
                # Update vendor status in this invoice
                invoice['vendor']['is_active'] = is_active
                
                # Update the document
                if self.adapter.update_document('vendor_invoices', invoice['invoice_id'], invoice):
                    updated_count += 1
            
            logger.info(f"Updated vendor status for {updated_count} invoices")
            return updated_count > 0
            
        except Exception as e:
            logger.error(f"Error updating vendor status: {e}")
            raise
    
    def merge_vendors(self, primary_vendor_id: str, duplicate_vendor_id: str) -> bool:
        """Merge duplicate vendor entries by updating all invoices to use primary vendor"""
        try:
            # Get primary vendor details
            primary_vendor = self.get_vendor_by_id(primary_vendor_id)
            if not primary_vendor:
                raise ValueError(f"Primary vendor {primary_vendor_id} not found")
            
            # Get all invoices with the duplicate vendor
            duplicate_invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': duplicate_vendor_id
            }, limit=10000)
            
            if not duplicate_invoices:
                return False
            
            updated_count = 0
            for invoice in duplicate_invoices:
                # Replace vendor with primary vendor details
                invoice['vendor'] = primary_vendor.copy()
                
                # Update the document
                if self.adapter.update_document('vendor_invoices', invoice['invoice_id'], invoice):
                    updated_count += 1
            
            logger.info(f"Merged {updated_count} invoices from vendor {duplicate_vendor_id} to {primary_vendor_id}")
            return updated_count > 0
            
        except Exception as e:
            logger.error(f"Error merging vendors: {e}")
            raise
    
    def search_vendors(self, search_term: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search vendors by name (fuzzy search across invoice documents)"""
        try:
            # Get all vendors first
            all_vendors = self.list_all_vendors(include_inactive=True)
            
            # Filter by search term (case-insensitive)
            search_term = search_term.lower()
            matching_vendors = []
            
            for vendor in all_vendors:
                vendor_name = vendor.get('vendor_name', '').lower()
                vendor_id = vendor.get('vendor_id', '').lower()
                
                if search_term in vendor_name or search_term in vendor_id:
                    matching_vendors.append(vendor)
            
            # Sort by relevance (exact matches first, then partial matches)
            def sort_key(vendor):
                name = vendor.get('vendor_name', '').lower()
                if name == search_term:
                    return 0  # Exact match
                elif name.startswith(search_term):
                    return 1  # Starts with search term
                else:
                    return 2  # Contains search term
            
            matching_vendors.sort(key=sort_key)
            return matching_vendors[:limit]
            
        except Exception as e:
            logger.error(f"Error searching vendors: {e}")
            raise
    
    def get_vendor_invoice_count(self, vendor_id: str) -> int:
        """Get count of invoices for a specific vendor"""
        try:
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': vendor_id
            }, limit=10000)
            return len(invoices)
            
        except Exception as e:
            logger.error(f"Error getting vendor invoice count: {e}")
            raise
    
    def get_vendor_categories(self, vendor_id: str) -> List[Dict[str, Any]]:
        """Get all categories used by a specific vendor"""
        try:
            # Get all invoices for this vendor
            invoices = self.adapter.query_documents('vendor_invoices', {
                'vendor.vendor_id': vendor_id
            }, limit=10000)
            
            # Extract unique categories
            categories_map = {}
            for invoice in invoices:
                category = invoice.get('category')
                if category and category.get('category_id'):
                    cat_id = category['category_id']
                    categories_map[cat_id] = category
            
            return list(categories_map.values())
            
        except Exception as e:
            logger.error(f"Error getting vendor categories: {e}")
            raise


# Global service instance
_vendor_service = None

def get_vendor_service(db_path: str = "recycling.db") -> VendorService:
    """Get or create vendor service instance"""
    global _vendor_service
    if _vendor_service is None or _vendor_service.db_path != db_path:
        _vendor_service = VendorService(db_path)
    return _vendor_service