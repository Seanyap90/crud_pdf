"""
Files API Database Layer

This module contains Files API-specific database services that provide 
document-based operations for vendors, invoices, and categories using 
embedded document patterns.
"""

from .vendor_service import VendorService, get_vendor_service
from .invoice_service import InvoiceService, get_invoice_service
from .category_service import CategoryService, get_category_service

__all__ = [
    'VendorService', 'get_vendor_service',
    'InvoiceService', 'get_invoice_service',
    'CategoryService', 'get_category_service'
]