"""
Category service for Files API NoSQL operations.
Categories are now embedded in invoice documents rather than stored separately.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from database.local import get_nosql_adapter

logger = logging.getLogger(__name__)


class CategoryService:
    """Service for managing category operations via embedded documents in invoices"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
    
    def get_category_by_id(self, category_id: int) -> Optional[Dict[str, Any]]:
        """Get category by ID from any invoice document that contains it"""
        try:
            # Query invoices for this category
            invoices = self.adapter.query_documents('vendor_invoices', {
                'category.category_id': category_id
            }, limit=1)
            
            if invoices and invoices[0].get('category'):
                return invoices[0]['category']
            return None
            
        except Exception as e:
            logger.error(f"Error getting category by ID: {e}")
            raise
    
    def get_category_by_name(self, category_name: str) -> Optional[Dict[str, Any]]:
        """Get category by name from any invoice document that contains it"""
        try:
            # Query invoices for this category name
            invoices = self.adapter.query_documents('vendor_invoices', {
                'category.category_name': category_name
            }, limit=1)
            
            if invoices and invoices[0].get('category'):
                return invoices[0]['category']
            return None
            
        except Exception as e:
            logger.error(f"Error getting category by name: {e}")
            raise
    
    def list_all_categories(self) -> List[Dict[str, Any]]:
        """Get all unique categories from invoice documents"""
        try:
            # Get all invoices that have categories
            invoices = self.adapter.query_documents('vendor_invoices', {}, limit=10000)
            
            # Extract unique categories
            categories_map = {}
            for invoice in invoices:
                category = invoice.get('category')
                if not category or not category.get('category_id'):
                    continue
                
                category_id = category['category_id']
                
                # Keep track of unique categories
                if category_id not in categories_map:
                    categories_map[category_id] = category
            
            # Return sorted list
            categories = list(categories_map.values())
            return sorted(categories, key=lambda x: x.get('category_name', ''))
            
        except Exception as e:
            logger.error(f"Error listing categories: {e}")
            raise
    
    def get_category_statistics(self, category_id: int) -> Dict[str, Any]:
        """Get statistics for a specific category"""
        try:
            # Query all invoices for this category
            invoices = self.adapter.query_documents('vendor_invoices', {
                'category.category_id': category_id
            }, limit=10000)
            
            if not invoices:
                return {
                    'category_id': category_id,
                    'total_invoices': 0,
                    'total_amount': 0.0,
                    'total_weight_kg': 0.0,
                    'unique_vendors': 0,
                    'status_breakdown': {},
                    'date_range': None
                }
            
            # Calculate statistics
            total_invoices = len(invoices)
            total_amount = 0.0
            total_weight = 0.0
            vendor_ids = set()
            status_counts = {}
            dates = []
            
            for invoice in invoices:
                # Sum amounts
                if invoice.get('total_amount'):
                    total_amount += float(invoice['total_amount'])
                
                # Sum weights
                if invoice.get('reported_weight_kg'):
                    total_weight += float(invoice['reported_weight_kg'])
                
                # Track unique vendors
                vendor = invoice.get('vendor', {})
                if vendor.get('vendor_id'):
                    vendor_ids.add(vendor['vendor_id'])
                
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
                'category_id': category_id,
                'category_name': invoices[0]['category']['category_name'],
                'total_invoices': total_invoices,
                'total_amount': round(total_amount, 2),
                'total_weight_kg': round(total_weight, 2),
                'unique_vendors': len(vendor_ids),
                'status_breakdown': status_counts,
                'date_range': date_range,
                'average_amount': round(total_amount / total_invoices, 2) if total_invoices > 0 else 0,
                'average_weight': round(total_weight / total_invoices, 2) if total_invoices > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting category statistics: {e}")
            raise
    
    def get_category_vendors(self, category_id: int) -> List[Dict[str, Any]]:
        """Get all vendors that have invoices in this category"""
        try:
            # Get all invoices for this category
            invoices = self.adapter.query_documents('vendor_invoices', {
                'category.category_id': category_id
            }, limit=10000)
            
            # Extract unique vendors
            vendors_map = {}
            for invoice in invoices:
                vendor = invoice.get('vendor')
                if vendor and vendor.get('vendor_id'):
                    vendor_id = vendor['vendor_id']
                    vendors_map[vendor_id] = vendor
            
            return list(vendors_map.values())
            
        except Exception as e:
            logger.error(f"Error getting category vendors: {e}")
            raise
    
    def search_categories(self, search_term: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search categories by name"""
        try:
            # Get all categories first
            all_categories = self.list_all_categories()
            
            # Filter by search term (case-insensitive)
            search_term = search_term.lower()
            matching_categories = []
            
            for category in all_categories:
                category_name = category.get('category_name', '').lower()
                description = category.get('description', '').lower() if category.get('description') else ''
                
                if search_term in category_name or search_term in description:
                    matching_categories.append(category)
            
            # Sort by relevance (exact matches first, then partial matches)
            def sort_key(category):
                name = category.get('category_name', '').lower()
                if name == search_term:
                    return 0  # Exact match
                elif name.startswith(search_term):
                    return 1  # Starts with search term
                else:
                    return 2  # Contains search term
            
            matching_categories.sort(key=sort_key)
            return matching_categories[:limit]
            
        except Exception as e:
            logger.error(f"Error searching categories: {e}")
            raise
    
    def get_category_usage_over_time(self, category_id: int) -> List[Dict[str, Any]]:
        """Get category usage statistics over time (monthly breakdown)"""
        try:
            # Get all invoices for this category
            invoices = self.adapter.query_documents('vendor_invoices', {
                'category.category_id': category_id
            }, limit=10000)
            
            # Group by month
            monthly_stats = {}
            for invoice in invoices:
                if not invoice.get('invoice_date'):
                    continue
                
                # Extract year-month
                invoice_date = invoice['invoice_date']
                if isinstance(invoice_date, str):
                    date_obj = datetime.fromisoformat(invoice_date.replace('Z', '+00:00'))
                else:
                    date_obj = invoice_date
                
                month_key = date_obj.strftime('%Y-%m')
                
                if month_key not in monthly_stats:
                    monthly_stats[month_key] = {
                        'month': month_key,
                        'invoice_count': 0,
                        'total_amount': 0.0,
                        'total_weight': 0.0
                    }
                
                monthly_stats[month_key]['invoice_count'] += 1
                
                if invoice.get('total_amount'):
                    monthly_stats[month_key]['total_amount'] += float(invoice['total_amount'])
                
                if invoice.get('reported_weight_kg'):
                    monthly_stats[month_key]['total_weight'] += float(invoice['reported_weight_kg'])
            
            # Convert to list and sort by month
            result = list(monthly_stats.values())
            result.sort(key=lambda x: x['month'])
            
            # Round amounts
            for month_data in result:
                month_data['total_amount'] = round(month_data['total_amount'], 2)
                month_data['total_weight'] = round(month_data['total_weight'], 2)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting category usage over time: {e}")
            raise
    
    def get_top_categories_by_amount(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top categories by total amount"""
        try:
            all_categories = self.list_all_categories()
            category_stats = []
            
            for category in all_categories:
                stats = self.get_category_statistics(category['category_id'])
                category_stats.append({
                    'category_id': category['category_id'],
                    'category_name': category['category_name'],
                    'total_amount': stats['total_amount'],
                    'total_invoices': stats['total_invoices'],
                    'total_weight_kg': stats['total_weight_kg']
                })
            
            # Sort by total amount descending
            category_stats.sort(key=lambda x: x['total_amount'], reverse=True)
            return category_stats[:limit]
            
        except Exception as e:
            logger.error(f"Error getting top categories by amount: {e}")
            raise
    
    def get_top_categories_by_weight(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top categories by total weight"""
        try:
            all_categories = self.list_all_categories()
            category_stats = []
            
            for category in all_categories:
                stats = self.get_category_statistics(category['category_id'])
                category_stats.append({
                    'category_id': category['category_id'],
                    'category_name': category['category_name'],
                    'total_weight_kg': stats['total_weight_kg'],
                    'total_invoices': stats['total_invoices'],
                    'total_amount': stats['total_amount']
                })
            
            # Sort by total weight descending
            category_stats.sort(key=lambda x: x['total_weight_kg'], reverse=True)
            return category_stats[:limit]
            
        except Exception as e:
            logger.error(f"Error getting top categories by weight: {e}")
            raise


# Global service instance
_category_service = None

def get_category_service(db_path: str = "recycling.db") -> CategoryService:
    """Get or create category service instance"""
    global _category_service
    if _category_service is None or _category_service.db_path != db_path:
        _category_service = CategoryService(db_path)
    return _category_service