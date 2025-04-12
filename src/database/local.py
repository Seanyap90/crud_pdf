import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

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
        
        # Create gateways table for read model
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gateways (
                gateway_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL,
                last_updated TEXT,
                last_heartbeat TEXT,
                uptime TEXT,
                health TEXT,
                error TEXT,
                created_at TEXT,
                connected_at TEXT,
                disconnected_at TEXT,
                deleted_at TEXT,
                certificate_info TEXT
            )
        ''')

        # Create end_devices table with flexible schema
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS end_devices (
                device_id TEXT PRIMARY KEY,
                gateway_id TEXT NOT NULL,
                device_type TEXT NOT NULL,
                name TEXT,
                location TEXT,
                status TEXT NOT NULL,
                last_updated TEXT,
                last_measurement TEXT,
                last_config_fetch TEXT,
                config_version TEXT,
                config_hash TEXT,
                device_config TEXT,  -- JSON blob of the device's configuration
                FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id)
            )
        ''')

        # Create measurements table with fully dynamic payload
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS measurements (
                measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                gateway_id TEXT NOT NULL,
                measurement_type TEXT NOT NULL,  -- e.g., "weight_measurement"
                timestamp TEXT NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                uploaded_to_cloud BOOLEAN DEFAULT FALSE,
                payload TEXT NOT NULL,  -- Full JSON payload with all measurement data
                FOREIGN KEY (device_id) REFERENCES end_devices(device_id),
                FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id)
            )
        ''')

        # Create index for faster queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_measurements_device ON measurements(device_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_measurements_gateway ON measurements(gateway_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_measurements_type ON measurements(measurement_type, timestamp)')

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

def register_end_device(
    device_id: str,
    gateway_id: str,
    device_type: str,
    config_version: Optional[str] = None,
    config_hash: Optional[str] = None,
    device_config: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    location: Optional[str] = None,
    status: str = "online",
    db_path: str = "recycling.db"
) -> Dict[str, Any]:
    """Register a new end device or update existing one with dynamic configuration."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check if device already exists
        cursor.execute('SELECT * FROM end_devices WHERE device_id = ?', (device_id,))
        existing_device = cursor.fetchone()
        
        timestamp = datetime.now().isoformat()
        
        # Convert device_config to JSON if provided
        device_config_json = None
        if device_config:
            device_config_json = json.dumps(device_config)
        
        if existing_device:
            # Update existing device
            query = '''
                UPDATE end_devices
                SET gateway_id = ?,
                    device_type = ?,
                    name = ?,
                    location = ?,
                    status = ?,
                    last_updated = ?
            '''
            params = [gateway_id, device_type, name, location, status, timestamp]
            
            # Only update config fields if provided
            if config_version:
                query += ", config_version = ?, last_config_fetch = ?"
                params.extend([config_version, timestamp])
            
            if config_hash:
                query += ", config_hash = ?"
                params.append(config_hash)
                
            if device_config_json:
                query += ", device_config = ?"
                params.append(device_config_json)
                
            query += " WHERE device_id = ?"
            params.append(device_id)
            
            cursor.execute(query, params)
        else:
            # Create new device
            if not name:
                name = f"Device {device_id}"
            
            if not location:
                location = "Unknown"
            
            query = '''
                INSERT INTO end_devices
                (device_id, gateway_id, device_type, name, location, status, last_updated
            '''
            
            params = [device_id, gateway_id, device_type, name, location, status, timestamp]
            
            # Add config fields if provided
            if config_version:
                query += ", config_version, last_config_fetch"
                params.extend([config_version, timestamp])
            
            if config_hash:
                query += ", config_hash"
                params.append(config_hash)
                
            if device_config_json:
                query += ", device_config"
                params.append(device_config_json)
                
            query += ") VALUES (" + ", ".join(["?"] * len(params)) + ")"
            
            cursor.execute(query, params)
        
        conn.commit()
        
        # Return the device info
        cursor.execute('SELECT * FROM end_devices WHERE device_id = ?', (device_id,))
        device = cursor.fetchone()
        result = dict(device) if device else {"device_id": device_id, "error": "Failed to fetch device after registration"}
        
        # Parse device_config JSON if present
        if result.get('device_config'):
            try:
                result['device_config'] = json.loads(result['device_config'])
            except:
                pass
                
        return result
    finally:
        conn.close()

def store_measurement(
    device_id: str,
    gateway_id: str,
    measurement_type: str,
    payload: Dict[str, Any],
    timestamp: Optional[str] = None,
    db_path: str = "recycling.db"
) -> int:
    """Store a measurement with fully dynamic payload."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        # Convert payload to JSON
        payload_json = json.dumps(payload)
        
        cursor.execute('''
            INSERT INTO measurements
            (device_id, gateway_id, measurement_type, timestamp, payload)
            VALUES (?, ?, ?, ?, ?)
        ''', (device_id, gateway_id, measurement_type, timestamp, payload_json))
        
        measurement_id = cursor.lastrowid
        
        # Also update the device's last_measurement time
        cursor.execute('''
            UPDATE end_devices
            SET last_measurement = ?, last_updated = ?
            WHERE device_id = ?
        ''', (timestamp, timestamp, device_id))
        
        conn.commit()
        return measurement_id
    finally:
        conn.close()

def get_measurements(
    device_id: Optional[str] = None,
    gateway_id: Optional[str] = None,
    measurement_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    db_path: str = "recycling.db"
) -> List[Dict[str, Any]]:
    """Get measurements with flexible filtering options."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = 'SELECT * FROM measurements WHERE 1=1'
        params = []
        
        if device_id:
            query += ' AND device_id = ?'
            params.append(device_id)
            
        if gateway_id:
            query += ' AND gateway_id = ?'
            params.append(gateway_id)
            
        if measurement_type:
            query += ' AND measurement_type = ?'
            params.append(measurement_type)
            
        if start_date:
            query += ' AND timestamp >= ?'
            params.append(start_date)
            
        if end_date:
            query += ' AND timestamp <= ?'
            params.append(end_date)
            
        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        
        measurements = []
        for row in cursor.fetchall():
            measurement = dict(row)
            
            # Parse payload JSON
            if measurement.get('payload'):
                try:
                    measurement['payload'] = json.loads(measurement['payload'])
                except:
                    # If parsing fails, leave as string
                    pass
                    
            measurements.append(measurement)
            
        return measurements
    finally:
        conn.close()

def extract_measurement_field(
    field_name: str,
    device_id: Optional[str] = None,
    gateway_id: Optional[str] = None,
    measurement_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: str = "recycling.db"
) -> List[Dict[str, Any]]:
    """Extract and analyze a specific field from measurement payloads using JSON functions."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Use SQLite JSON function to extract the field from the payload
        # The approach differs based on where the field is in the payload structure
        
        # First try with direct field extraction
        query = f'''
            SELECT 
                device_id, 
                gateway_id,
                measurement_type,
                timestamp,
                json_extract(payload, '$.{field_name}') as field_value
            FROM measurements 
            WHERE json_extract(payload, '$.{field_name}') IS NOT NULL
        '''
        
        # Try with nested payload extraction if field not found directly
        fallback_query = f'''
            SELECT 
                device_id, 
                gateway_id,
                measurement_type,
                timestamp,
                json_extract(payload, '$.payload.{field_name}') as field_value
            FROM measurements 
            WHERE json_extract(payload, '$.payload.{field_name}') IS NOT NULL
        '''
        
        params = []
        
        if device_id:
            query += ' AND device_id = ?'
            fallback_query += ' AND device_id = ?'
            params.append(device_id)
            
        if gateway_id:
            query += ' AND gateway_id = ?'
            fallback_query += ' AND gateway_id = ?'
            params.append(gateway_id)
            
        if measurement_type:
            query += ' AND measurement_type = ?'
            fallback_query += ' AND measurement_type = ?'
            params.append(measurement_type)
            
        if start_date:
            query += ' AND timestamp >= ?'
            fallback_query += ' AND timestamp >= ?'
            params.append(start_date)
            
        if end_date:
            query += ' AND timestamp <= ?'
            fallback_query += ' AND timestamp <= ?'
            params.append(end_date)
            
        # Try direct extraction first
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # If no results, try nested extraction
        if not results:
            cursor.execute(fallback_query, params)
            results = cursor.fetchall()
        
        return [dict(row) for row in results]
    finally:
        conn.close()

def get_measurement_summary(
    field_name: str,
    gateway_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    measurement_type: str = "weight_measurement",
    db_path: str = "recycling.db"
) -> List[Dict[str, Any]]:
    """Get summary of measurements by a specific field (e.g., material type) within a date range."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Try to extract from both the top level and the nested payload structure
        query = f'''
            WITH data AS (
                SELECT 
                    json_extract(payload, '$.{field_name}') as field_value,
                    CAST(json_extract(payload, '$.payload.weight_kg') AS REAL) as weight_kg
                FROM measurements
                WHERE measurement_type = ? AND
                      (json_extract(payload, '$.{field_name}') IS NOT NULL OR 
                       json_extract(payload, '$.payload.{field_name}') IS NOT NULL)
                
                UNION ALL
                
                SELECT 
                    json_extract(payload, '$.payload.{field_name}') as field_value,
                    CAST(json_extract(payload, '$.payload.weight_kg') AS REAL) as weight_kg
                FROM measurements
                WHERE measurement_type = ? AND
                      json_extract(payload, '$.payload.{field_name}') IS NOT NULL AND
                      json_extract(payload, '$.{field_name}') IS NULL
            )
            SELECT 
                field_value,
                COUNT(*) as measurement_count,
                SUM(weight_kg) as total_weight_kg,
                AVG(weight_kg) as avg_weight_kg,
                MIN(weight_kg) as min_weight_kg,
                MAX(weight_kg) as max_weight_kg
            FROM data
            WHERE field_value IS NOT NULL AND weight_kg IS NOT NULL
            GROUP BY field_value
            ORDER BY total_weight_kg DESC
        '''
        
        params = [measurement_type, measurement_type]
        
        if gateway_id or start_date or end_date:
            # Modify the query to add additional filters
            filtered_query = f'''
                WITH data AS (
                    SELECT 
                        json_extract(payload, '$.{field_name}') as field_value,
                        CAST(json_extract(payload, '$.payload.weight_kg') AS REAL) as weight_kg
                    FROM measurements
                    WHERE measurement_type = ? 
            '''
            
            if gateway_id:
                filtered_query += ' AND gateway_id = ?'
                params.append(gateway_id)
                
            if start_date:
                filtered_query += ' AND timestamp >= ?'
                params.append(start_date)
                
            if end_date:
                filtered_query += ' AND timestamp <= ?'
                params.append(end_date)
                
            filtered_query += f''' AND (json_extract(payload, '$.{field_name}') IS NOT NULL OR 
                                    json_extract(payload, '$.payload.{field_name}') IS NOT NULL)
                
                UNION ALL
                
                SELECT 
                    json_extract(payload, '$.payload.{field_name}') as field_value,
                    CAST(json_extract(payload, '$.payload.weight_kg') AS REAL) as weight_kg
                FROM measurements
                WHERE measurement_type = ? 
            '''
            
            # Add the same filters again for the second part of the UNION
            if gateway_id:
                filtered_query += ' AND gateway_id = ?'
                params.append(gateway_id)
                
            if start_date:
                filtered_query += ' AND timestamp >= ?'
                params.append(start_date)
                
            if end_date:
                filtered_query += ' AND timestamp <= ?'
                params.append(end_date)
                
            filtered_query += f''' AND json_extract(payload, '$.payload.{field_name}') IS NOT NULL AND
                                   json_extract(payload, '$.{field_name}') IS NULL
            )
            SELECT 
                field_value,
                COUNT(*) as measurement_count,
                SUM(weight_kg) as total_weight_kg,
                AVG(weight_kg) as avg_weight_kg,
                MIN(weight_kg) as min_weight_kg,
                MAX(weight_kg) as max_weight_kg
            FROM data
            WHERE field_value IS NOT NULL AND weight_kg IS NOT NULL
            GROUP BY field_value
            ORDER BY total_weight_kg DESC
            '''
            
            params = [measurement_type] + params[1:] + [measurement_type] + params[1:]
            query = filtered_query
            
        cursor.execute(query, params)
        summary = [dict(row) for row in cursor.fetchall()]
        return summary
    finally:
        conn.close()