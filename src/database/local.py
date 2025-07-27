import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
import logging
from .nosql_adapter import NoSQLAdapter
from .schemas import VendorSchema, CategorySchema, VendorInvoiceSchema

logger = logging.getLogger(__name__)

# Global NoSQL adapter instance
_nosql_adapter = None

def get_nosql_adapter(db_path: str = "recycling.db"):
    """Final version with multi-writer solution"""
    import os
    
    if os.getenv('DEPLOYMENT_MODE') == 'aws-prod':
        from .sqlite_http_adapter import SQLiteHTTPAdapter
        return SQLiteHTTPAdapter(
            host=os.getenv('DATABASE_HOST'),
            port=int(os.getenv('DATABASE_PORT', '8080'))
        )
    else:
        return NoSQLAdapter(db_path)

def init_db(db_path: str = "recycling.db") -> None:
    """Initialize database with all required tables and NoSQL collections."""
    conn = None
    try:
        # Initialize NoSQL collections first
        adapter = get_nosql_adapter(db_path)
        adapter.init_collections()
        
        # Initialize document indexes for performance
        from .indexes import DocumentIndexManager
        index_manager = DocumentIndexManager(db_path)
        index_manager.create_all_indexes()
        
        # Initialize traditional SQL tables for backward compatibility
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create vendors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vendors (
                vendor_id VARCHAR(50) PRIMARY KEY,
                vendor_name VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create material_categories table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS material_categories (
                category_id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_name VARCHAR(50) UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create vendor_invoices table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vendor_invoices (
                invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id VARCHAR(50),
                vendor_name VARCHAR(100) NOT NULL,
                category_id INTEGER,
                invoice_number VARCHAR(50),
                invoice_date DATE NOT NULL,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                filename VARCHAR(255) NOT NULL,
                filepath VARCHAR(500) NOT NULL,
                reported_weight_kg DECIMAL(10,2) NULL,    -- Updated by worker after PDF processing
                unit_price DECIMAL(10,2) NULL,            -- Updated by worker after PDF processing
                total_amount DECIMAL(10,2) NULL,          -- Updated by worker after PDF processing
                extraction_status VARCHAR(20) DEFAULT 'pending',  -- pending, processing, completed, failed
                processing_date TIMESTAMP NULL,            -- When worker started processing
                completion_date TIMESTAMP NULL,            -- When worker finished processing
                error_message TEXT NULL,                   -- In case of extraction failures
                FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id),
                FOREIGN KEY (category_id) REFERENCES material_categories(category_id),
                UNIQUE(vendor_id, invoice_number)
            )
        ''')

        # Create events table for event sourcing
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Initialize default categories if they don't exist
        default_categories = [
            (1, 'General Waste', 'Non-recyclable waste materials'),
            (2, 'Recyclable', 'Materials that can be recycled'),
            (3, 'Hazardous', 'Dangerous or toxic materials'),
            (4, 'Organic', 'Biodegradable materials'),
            (5, 'Metal', 'Metal waste and scrap'),
            (6, 'Paper', 'Paper and cardboard materials'),
            (7, 'Plastic', 'Plastic materials and products'),
            (8, 'Glass', 'Glass materials and products'),
            (9, 'Electronic', 'Electronic waste and components'),
            (10, 'Construction', 'Construction and demolition waste')
        ]
        
        cursor.executemany('''
            INSERT OR IGNORE INTO material_categories (category_id, category_name, description)
            VALUES (?, ?, ?)
        ''', default_categories)
        
        conn.commit()
    finally:
        if conn:
            conn.close()

def get_or_create_vendor(vendor_name: str, vendor_id: Optional[str] = None, db_path: str = "recycling.db") -> str:
    """Get existing vendor or create new one if doesn't exist."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # If vendor_id is provided, check if it exists
        if vendor_id:
            cursor.execute('SELECT vendor_id FROM vendors WHERE vendor_id = ?', (vendor_id,))
            result = cursor.fetchone()
            if result:
                return vendor_id
        
        # Check if vendor exists by name
        cursor.execute('SELECT vendor_id FROM vendors WHERE vendor_name = ?', (vendor_name,))
        result = cursor.fetchone()
        
        if result:
            return result[0]
        
        # Create new vendor if doesn't exist
        if not vendor_id:
            # Generate a new vendor_id if not provided
            vendor_id = f"V{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
        cursor.execute(
            'INSERT INTO vendors (vendor_id, vendor_name) VALUES (?, ?)',
            (vendor_id, vendor_name)
        )
        conn.commit()
        return vendor_id
    finally:
        conn.close()

def add_invoice(filename: str, 
                filepath: str, 
                vendor_name: str,
                vendor_id: Optional[str] = None,
                category_id: Optional[int] = None,
                invoice_number: Optional[str] = None,
                invoice_date: Optional[str] = None,
                db_path: str = "recycling.db") -> int:
    """Add initial invoice record when PDF is uploaded."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get or create vendor
        vendor_id = get_or_create_vendor(vendor_name, vendor_id)
        
        # If invoice_number not provided, generate one
        if not invoice_number:
            invoice_number = f"INV{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
        # If invoice_date not provided, use current date
        if not invoice_date:
            invoice_date = datetime.now().date().isoformat()
            
        # Insert basic invoice record
        cursor.execute('''
            INSERT INTO vendor_invoices 
            (vendor_id, vendor_name, category_id, invoice_number, invoice_date, filename, filepath, extraction_status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (vendor_id, vendor_name, category_id, invoice_number, invoice_date, filename, filepath))
        
        invoice_id = cursor.lastrowid
        conn.commit()
        return invoice_id
    finally:
        conn.close()

def update_invoice_processing_status(
    invoice_id: int,
    status: str,
    processing_date: str,
    db_path: str = "recycling.db"
) -> None:
    """Update invoice status when processing starts."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE vendor_invoices 
            SET extraction_status = ?,
                processing_date = ?
            WHERE invoice_id = ?
        ''', (status, processing_date, invoice_id))
        
        conn.commit()
    finally:
        conn.close()

def update_invoice_with_extracted_data(
    invoice_id: int,
    total_amount: Optional[float],
    reported_weight_kg: Optional[float],
    status: str,
    completion_date: str,
    error_message: Optional[str] = None,
    db_path: str = "recycling.db"
) -> None:
    """Update invoice with extracted data or error information."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE vendor_invoices 
            SET total_amount = ?,
                reported_weight_kg = ?,
                extraction_status = ?,
                completion_date = ?,
                error_message = ?
            WHERE invoice_id = ?
        ''', (total_amount, reported_weight_kg, status, completion_date, error_message, invoice_id))
        
        conn.commit()
    finally:
        conn.close()

def get_invoice_metadata(invoice_id: int, db_path: str = "recycling.db") -> Optional[dict]:
    """Retrieve invoice metadata from database."""
    try:
        conn = sqlite3.connect(db_path)
        # Set row_factory to get dictionary-like results
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM vendor_invoices 
            WHERE invoice_id = ?
        ''', (invoice_id,))
        
        row = cursor.fetchone()
        if row:
            # Convert Row object to dict
            return dict(row)
        return None
    finally:
        conn.close()

def get_invoices_list(vendor_id: str, db_path: str = "recycling.db") -> tuple[list, int]:
    """Retrieve list of invoices with their metadata for a specific vendor."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get total count for this vendor
        cursor.execute(
            'SELECT COUNT(*) as count FROM vendor_invoices WHERE vendor_id = ?',
            (vendor_id,)
        )
        total_count = cursor.fetchone()['count']
        
        # Get invoice list with category name, filtered by vendor_id
        cursor.execute('''
            SELECT 
                vi.invoice_id,
                vi.invoice_number,
                COALESCE(mc.category_name, 'Uncategorized') as category,
                vi.filename,
                vi.reported_weight_kg,
                vi.total_amount,
                vi.upload_date,
                vi.extraction_status
            FROM vendor_invoices vi
            LEFT JOIN material_categories mc ON vi.category_id = mc.category_id
            WHERE vi.vendor_id = ?
            ORDER BY vi.upload_date DESC
        ''', (vendor_id,))
        
        rows = cursor.fetchall()
        invoices = [dict(row) for row in rows]
        return invoices, total_count
    finally:
        conn.close()









# ====================================================================
# NoSQL Document-Based Functions
# ====================================================================

def create_invoice_doc(
    filename: str,
    filepath: str,
    vendor_name: str,
    vendor_id: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
    invoice_date: Optional[datetime] = None,
    db_path: str = "recycling.db"
) -> int:
    """Create invoice document with embedded vendor and category"""
    try:
        adapter = get_nosql_adapter(db_path)
        
        # Generate IDs if not provided
        if not vendor_id:
            vendor_id = f"V{datetime.now().strftime('%Y%m%d%H%M%S')}"
        if not invoice_number:
            invoice_number = f"INV{datetime.now().strftime('%Y%m%d%H%M%S')}"
        if not invoice_date:
            invoice_date = datetime.now()
        
        # Get next invoice ID
        count = adapter.count_documents('vendor_invoices')
        invoice_id = count + 1
        
        # Create vendor document
        vendor_doc = {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "created_at": datetime.now(),
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
            "invoice_date": invoice_date,
            "upload_date": datetime.now(),
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
        
        # Validate and create document
        created_id = adapter.create_document('vendor_invoices', invoice_doc)
        logger.info(f"Created invoice document with ID: {created_id}")
        return created_id
        
    except Exception as e:
        logger.error(f"Error creating invoice document: {e}")
        raise


def get_invoice_doc(invoice_id: int, db_path: str = "recycling.db") -> Optional[Dict[str, Any]]:
    """Get invoice document by ID"""
    try:
        adapter = get_nosql_adapter(db_path)
        return adapter.get_document('vendor_invoices', invoice_id)
    except Exception as e:
        logger.error(f"Error getting invoice document: {e}")
        raise


def update_invoice_doc_status(
    invoice_id: int,
    status: str,
    processing_date: Optional[datetime] = None,
    completion_date: Optional[datetime] = None,
    total_amount: Optional[float] = None,
    reported_weight_kg: Optional[float] = None,
    error_message: Optional[str] = None,
    db_path: str = "recycling.db"
) -> bool:
    """Update invoice document status and extracted data"""
    try:
        adapter = get_nosql_adapter(db_path)
        
        # Get existing document
        doc = adapter.get_document('vendor_invoices', invoice_id)
        if not doc:
            logger.warning(f"Invoice document {invoice_id} not found")
            return False
        
        # Update fields
        doc['extraction_status'] = status
        if processing_date:
            doc['processing_date'] = processing_date
        if completion_date:
            doc['completion_date'] = completion_date
        if total_amount is not None:
            doc['total_amount'] = total_amount
        if reported_weight_kg is not None:
            doc['reported_weight_kg'] = reported_weight_kg
        if error_message is not None:
            doc['error_message'] = error_message
        
        return adapter.update_document('vendor_invoices', invoice_id, doc)
        
    except Exception as e:
        logger.error(f"Error updating invoice document: {e}")
        raise


def query_invoices(
    vendor_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: str = "recycling.db"
) -> Tuple[List[Dict[str, Any]], int]:
    """Query invoice documents with filters"""
    try:
        adapter = get_nosql_adapter(db_path)
        
        # Build query
        query = {}
        if vendor_id:
            query['vendor.vendor_id'] = vendor_id
        if status:
            query['extraction_status'] = status
        
        # Get documents and count
        documents = adapter.query_documents('vendor_invoices', query, limit, offset)
        total_count = adapter.count_documents('vendor_invoices', query)
        
        return documents, total_count
        
    except Exception as e:
        logger.error(f"Error querying invoice documents: {e}")
        raise


