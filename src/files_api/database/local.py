import sqlite3
from datetime import datetime
from typing import Optional

def init_db(db_path: str = "recycling.db") -> None:
    """Initialize database with all required tables."""
    try:
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
        
        conn.commit()
    finally:
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
            vendor_id = f"V{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            
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
            invoice_number = f"INV{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            
        # If invoice_date not provided, use current date
        if not invoice_date:
            invoice_date = datetime.utcnow().date().isoformat()
            
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