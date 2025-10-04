"""
Document Indexes for NoSQL Performance Optimization.

This module provides index management for document collections to optimize
query performance across vendor invoices, gateways, devices, and measurements.
"""

import sqlite3
import logging
from typing import List, Dict, Any, Union
from .nosql_adapter import NoSQLAdapter


logger = logging.getLogger(__name__)


class DocumentIndexManager:
    """Manages document indexes for optimal query performance"""
    
    def __init__(self, connection_string: str = None):
        self.connection_string = connection_string or self._get_default_connection()
        
    def _get_default_connection(self) -> str:
        """Get default connection string based on deployment mode"""
        return "recycling.db"  # Always use SQLite
        
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection - always SQLite"""
        return sqlite3.connect(self.connection_string)
    
    def create_all_indexes(self) -> None:
        """Create all document indexes for optimal performance"""
        conn = None
        try:
            conn = self._get_connection()
            
            # Check if we're using MongoDB or SQLite
            if hasattr(conn, 'list_database_names'):  # MongoDB client
                self._create_mongodb_indexes(conn)
                logger.info("MongoDB document indexes created successfully")
            else:  # SQLite connection
                self._create_sqlite_indexes(conn)
                logger.info("SQLite document indexes created successfully")
                
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _create_mongodb_indexes(self, client) -> None:
        """Create MongoDB indexes for aws-prod mode"""
        try:
            db = client.crud_pdf
            
            # Vendor invoice indexes
            db.vendor_invoices.create_index([("vendor.vendor_id", 1)])
            db.vendor_invoices.create_index([("date_created", -1)])
            db.vendor_invoices.create_index([("total_amount", 1)])
            db.vendor_invoices.create_index([("vendor.vendor_id", 1), ("date_created", -1)])
            
            # IoT gateway indexes
            db.gateways.create_index([("gateway_id", 1)])
            db.gateways.create_index([("location", 1)])
            db.gateways.create_index([("status", 1)])
            
            # IoT device indexes  
            db.devices.create_index([("device_id", 1)])
            db.devices.create_index([("gateway_id", 1)])
            db.devices.create_index([("device_type", 1)])
            
            # Measurement indexes
            db.measurements.create_index([("device_id", 1)])
            db.measurements.create_index([("timestamp", -1)])
            db.measurements.create_index([("measurement_type", 1)])
            db.measurements.create_index([("device_id", 1), ("timestamp", -1)])
            
            # Config indexes
            db.config.create_index([("config_key", 1)])
            
            logger.info("MongoDB indexes created successfully")
            
        except Exception as e:
            logger.error(f"Error creating MongoDB indexes: {e}")
            raise
    
    def _create_sqlite_indexes(self, conn) -> None:
        """Create SQLite indexes for local-dev/aws-mock modes"""
        cursor = conn.cursor()
        
        # Create vendor invoice indexes
        self._create_invoice_indexes(cursor)
        
        # Create IoT indexes
        self._create_gateway_indexes(cursor)
        self._create_device_indexes(cursor)
        self._create_measurement_indexes(cursor)
        
        # Create config indexes
        self._create_config_indexes(cursor)
        
        conn.commit()
        logger.info("SQLite indexes created successfully")
    
    def _create_invoice_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for vendor invoice documents"""
        
        # Vendor-based queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_vendor_id 
            ON vendor_invoices_docs(json_extract(document, '$.vendor.vendor_id'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_vendor_name 
            ON vendor_invoices_docs(json_extract(document, '$.vendor.vendor_name'))
        ''')
        
        # Status and processing queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_status 
            ON vendor_invoices_docs(json_extract(document, '$.extraction_status'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_upload_date 
            ON vendor_invoices_docs(json_extract(document, '$.upload_date'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_processing_date 
            ON vendor_invoices_docs(json_extract(document, '$.processing_date'))
        ''')
        
        # Category-based queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_category_id 
            ON vendor_invoices_docs(json_extract(document, '$.category.category_id'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_category_name 
            ON vendor_invoices_docs(json_extract(document, '$.category.category_name'))
        ''')
        
        # Financial data queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_total_amount 
            ON vendor_invoices_docs(json_extract(document, '$.total_amount'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_weight 
            ON vendor_invoices_docs(json_extract(document, '$.reported_weight_kg'))
        ''')
        
        # Composite index for common queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_vendor_status 
            ON vendor_invoices_docs(
                json_extract(document, '$.vendor.vendor_id'),
                json_extract(document, '$.extraction_status')
            )
        ''')
        
        logger.info("Invoice document indexes created")
    
    def _create_gateway_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for gateway documents"""
        
        # Status-based queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_status 
            ON gateways_docs(json_extract(document, '$.status'))
        ''')
        
        # Timestamp queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_last_updated 
            ON gateways_docs(json_extract(document, '$.last_updated'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_last_heartbeat 
            ON gateways_docs(json_extract(document, '$.last_heartbeat'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_created_at 
            ON gateways_docs(json_extract(document, '$.created_at'))
        ''')
        
        # Location and name queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_location 
            ON gateways_docs(json_extract(document, '$.location'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_name 
            ON gateways_docs(json_extract(document, '$.name'))
        ''')
        
        # Health monitoring
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_health 
            ON gateways_docs(json_extract(document, '$.health'))
        ''')
        
        # Composite index for monitoring queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_status_health 
            ON gateways_docs(
                json_extract(document, '$.status'),
                json_extract(document, '$.health')
            )
        ''')
        
        logger.info("Gateway document indexes created")
    
    def _create_device_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for device documents"""
        
        # Gateway relationship queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_gateway_id 
            ON devices_docs(json_extract(document, '$.gateway_id'))
        ''')
        
        # Status queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_status 
            ON devices_docs(json_extract(document, '$.status'))
        ''')
        
        # Device type queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_type 
            ON devices_docs(json_extract(document, '$.device_type'))
        ''')
        
        # Timestamp queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_last_updated 
            ON devices_docs(json_extract(document, '$.last_updated'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_last_measurement 
            ON devices_docs(json_extract(document, '$.last_measurement'))
        ''')
        
        # Location queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_location 
            ON devices_docs(json_extract(document, '$.location'))
        ''')
        
        # Composite indexes for common queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_gateway_status 
            ON devices_docs(
                json_extract(document, '$.gateway_id'),
                json_extract(document, '$.status')
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_gateway_type 
            ON devices_docs(
                json_extract(document, '$.gateway_id'),
                json_extract(document, '$.device_type')
            )
        ''')
        
        logger.info("Device document indexes created")
    
    def _create_measurement_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for measurement documents with embedded device info"""
        
        # Device relationship queries (embedded)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_device_id 
            ON measurements_docs(json_extract(document, '$.device_info.device_id'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_gateway_id 
            ON measurements_docs(json_extract(document, '$.device_info.gateway_id'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_device_type 
            ON measurements_docs(json_extract(document, '$.device_info.device_type'))
        ''')
        
        # Measurement type queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_type 
            ON measurements_docs(json_extract(document, '$.measurement_type'))
        ''')
        
        # Timestamp queries (critical for time-series data)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_timestamp 
            ON measurements_docs(json_extract(document, '$.timestamp'))
        ''')
        
        # Processing status queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_processed 
            ON measurements_docs(json_extract(document, '$.processed'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_uploaded 
            ON measurements_docs(json_extract(document, '$.uploaded_to_cloud'))
        ''')
        
        # Payload data queries (for specific measurement types)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_weight 
            ON measurements_docs(json_extract(document, '$.payload.weight_kg'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_material_type 
            ON measurements_docs(json_extract(document, '$.payload.material_type'))
        ''')
        
        # Composite indexes for time-series queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_device_timestamp 
            ON measurements_docs(
                json_extract(document, '$.device_info.device_id'),
                json_extract(document, '$.timestamp')
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_gateway_timestamp 
            ON measurements_docs(
                json_extract(document, '$.device_info.gateway_id'),
                json_extract(document, '$.timestamp')
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_type_timestamp 
            ON measurements_docs(
                json_extract(document, '$.measurement_type'),
                json_extract(document, '$.timestamp')
            )
        ''')
        
        # Complex composite for analytics queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_analytics 
            ON measurements_docs(
                json_extract(document, '$.device_info.gateway_id'),
                json_extract(document, '$.measurement_type'),
                json_extract(document, '$.timestamp'),
                json_extract(document, '$.processed')
            )
        ''')
        
        logger.info("Measurement document indexes created")
    
    def _create_config_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create indexes for configuration update documents"""
        
        # Gateway relationship
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_gateway_id 
            ON config_updates_docs(json_extract(document, '$.gateway_id'))
        ''')
        
        # State queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_state 
            ON config_updates_docs(json_extract(document, '$.state'))
        ''')
        
        # Timestamp queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_created_at 
            ON config_updates_docs(json_extract(document, '$.created_at'))
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_last_updated 
            ON config_updates_docs(json_extract(document, '$.last_updated'))
        ''')
        
        # Version tracking
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_version 
            ON config_updates_docs(json_extract(document, '$.config_version'))
        ''')
        
        # Composite for status tracking
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_config_gateway_state 
            ON config_updates_docs(
                json_extract(document, '$.gateway_id'),
                json_extract(document, '$.state')
            )
        ''')
        
        logger.info("Config document indexes created")
    
    def drop_all_indexes(self) -> None:
        """Drop all document indexes (useful for testing or rebuilding)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Get all indexes for our document tables
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' 
                AND name LIKE 'idx_%'
                AND sql IS NOT NULL
            """)
            
            indexes = [row[0] for row in cursor.fetchall()]
            
            for index_name in indexes:
                cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
            
            conn.commit()
            logger.info(f"Dropped {len(indexes)} document indexes")
            
        except Exception as e:
            logger.error(f"Error dropping indexes: {e}")
            raise
        finally:
            conn.close()
    
    def analyze_indexes(self) -> Dict[str, Any]:
        """Analyze index usage and performance"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Get index information
            cursor.execute("""
                SELECT name, tbl_name, sql 
                FROM sqlite_master 
                WHERE type='index' 
                AND name LIKE 'idx_%'
                AND sql IS NOT NULL
                ORDER BY tbl_name, name
            """)
            
            indexes = []
            for row in cursor.fetchall():
                indexes.append({
                    "name": row[0],
                    "table": row[1],
                    "sql": row[2]
                })
            
            # Get table sizes
            table_stats = {}
            for table in ['vendor_invoices_docs', 'gateways_docs', 'devices_docs', 'measurements_docs']:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                table_stats[table] = count
            
            return {
                "indexes": indexes,
                "table_stats": table_stats,
                "total_indexes": len(indexes)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing indexes: {e}")
            raise
        finally:
            conn.close()
    
    def rebuild_indexes(self) -> None:
        """Rebuild all indexes (drop and recreate)"""
        logger.info("Rebuilding all document indexes...")
        self.drop_all_indexes()
        self.create_all_indexes()
        logger.info("Index rebuild completed")


# Global index manager instance
_index_manager = None

def get_index_manager(db_path: str = "recycling.db") -> DocumentIndexManager:
    """Get or create index manager instance"""
    global _index_manager
    if _index_manager is None or _index_manager.db_path != db_path:
        _index_manager = DocumentIndexManager(db_path)
    return _index_manager

def create_all_indexes(db_path: str = "recycling.db") -> None:
    """Convenience function to create all indexes"""
    manager = get_index_manager(db_path)
    manager.create_all_indexes()

def analyze_query_performance(db_path: str = "recycling.db") -> Dict[str, Any]:
    """Convenience function to analyze index performance"""
    manager = get_index_manager(db_path)
    return manager.analyze_indexes()