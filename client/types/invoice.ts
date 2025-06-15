/**
 * TypeScript interfaces for Files API with embedded document objects
 * Supports the NoSQL document structure with embedded vendor and category objects
 */

// Base vendor information
export interface Vendor {
  vendor_id: string;
  vendor_name: string;
  created_at: string;
  is_active: boolean;
}

// Base category information  
export interface Category {
  category_id: number;
  category_name: string;
  description: string;
}

// Invoice with embedded vendor and category objects (NoSQL structure)
export interface Invoice {
  invoice_id: number;
  vendor: Vendor;                           // Embedded vendor document
  category?: Category;                      // Embedded category document (optional)
  invoice_number: string;
  invoice_date: string;
  upload_date: string;
  filename: string;
  filepath: string;
  reported_weight_kg?: number | null;
  unit_price?: number | null;
  total_amount?: number | null;
  extraction_status: 'pending' | 'processing' | 'completed' | 'failed';
  processing_date?: string | null;
  completion_date?: string | null;
  error_message?: string | null;
}

// API response for invoice list
export interface InvoiceListResponse {
  invoices: Invoice[];
  total_count: number;
}

// API response for single invoice
export interface InvoiceResponse {
  invoice: Invoice;
}

// Upload form data interface
export interface InvoiceUploadData {
  invoiceNumber: string;
  invoiceDate: string;
  categoryId: string;
  categoryName?: string;
  vendorName?: string;
  vendorId?: string;
}

// Upload API request parameters
export interface InvoiceUploadParams {
  vendor_name: string;
  vendor_id: string;
  category_id: string;
  invoice_number: string;
  invoice_date: string;
}

// Upload API response
export interface InvoiceUploadResponse {
  message: string;
  invoice_id: number;
  filename: string;
  upload_status: string;
}

// Vendor statistics response
export interface VendorStatistics {
  vendor_id: string;
  vendor_name: string;
  total_invoices: number;
  total_amount: number;
  total_weight_kg: number;
  completed_invoices: number;
  pending_invoices: number;
  failed_invoices: number;
}

// Category statistics response
export interface CategoryStatistics {
  category_id: number;
  category_name: string;
  total_invoices: number;
  total_amount: number;
  total_weight_kg: number;
  unique_vendors: number;
}

// Search and filter parameters
export interface InvoiceFilters {
  vendor_id?: string;
  category_id?: number;
  status?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

// API error response
export interface ApiError {
  detail: string;
  error_code?: string;
  timestamp?: string;
}

// Health check response
export interface HealthResponse {
  ready: boolean;
  status: string;
  timestamp: string;
}