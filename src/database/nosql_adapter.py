"""
Unified NoSQL adapter for document-based operations.
Provides a unified interface for SQLite JSON documents with future MongoDB compatibility.
"""

import sqlite3
import json
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from .schemas import DOCUMENT_VALIDATORS, DOCUMENT_SCHEMAS

logger = logging.getLogger(__name__)


class NoSQLAdapter:
    """Unified adapter for document-based database operations"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.connection_pool = {}  # Simple connection management
        
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with JSON support"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable JSON support
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    def _validate_document(self, collection: str, document: Dict[str, Any]) -> None:
        """Validate document against schema"""
        if collection in DOCUMENT_VALIDATORS:
            try:
                DOCUMENT_VALIDATORS[collection](document)
            except Exception as e:
                logger.error(f"Document validation failed for {collection}: {e}")
                raise ValueError(f"Document validation failed: {e}")
    
    def _serialize_document(self, document: Dict[str, Any]) -> str:
        """Serialize document to JSON string"""
        def json_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        return json.dumps(document, default=json_serializer)
    
    def _deserialize_document(self, json_str: str) -> Dict[str, Any]:
        """Deserialize JSON string to document"""
        return json.loads(json_str)
    
    def init_collections(self) -> None:
        """Initialize document collections (tables)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Create vendor_invoices collection
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vendor_invoices_docs (
                    invoice_id INTEGER PRIMARY KEY,
                    document TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create gateways collection
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gateways_docs (
                    gateway_id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create devices collection
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices_docs (
                    device_id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create measurements collection
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS measurements_docs (
                    measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create config_updates collection
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config_updates_docs (
                    update_id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for common queries
            self._create_basic_indexes(cursor)
            
            conn.commit()
            logger.info("NoSQL collections initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing collections: {e}")
            raise
        finally:
            conn.close()
    
    def _create_basic_indexes(self, cursor: sqlite3.Cursor) -> None:
        """Create basic indexes for document queries"""
        # Invoice indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_vendor_id 
            ON vendor_invoices_docs(json_extract(document, '$.vendor.vendor_id'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_status 
            ON vendor_invoices_docs(json_extract(document, '$.extraction_status'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_invoice_upload_date 
            ON vendor_invoices_docs(json_extract(document, '$.upload_date'))
        ''')
        
        # Gateway indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_status 
            ON gateways_docs(json_extract(document, '$.status'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_gateway_last_updated 
            ON gateways_docs(json_extract(document, '$.last_updated'))
        ''')
        
        # Device indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_gateway_id 
            ON devices_docs(json_extract(document, '$.gateway_id'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_device_status 
            ON devices_docs(json_extract(document, '$.status'))
        ''')
        
        # Measurement indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_device_id 
            ON measurements_docs(json_extract(document, '$.device_info.device_id'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_gateway_id 
            ON measurements_docs(json_extract(document, '$.device_info.gateway_id'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_timestamp 
            ON measurements_docs(json_extract(document, '$.timestamp'))
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_measurement_type 
            ON measurements_docs(json_extract(document, '$.measurement_type'))
        ''')
    
    def create_document(self, collection: str, document: Dict[str, Any]) -> Union[str, int]:
        """Create a new document in the collection"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if collection == 'vendor_invoices':
                # Validate document first for collections with predefined IDs
                self._validate_document(collection, document)
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    INSERT INTO vendor_invoices_docs (invoice_id, document)
                    VALUES (?, ?)
                ''', (document['invoice_id'], doc_json))
                doc_id = document['invoice_id']
                
            elif collection == 'gateways':
                # Validate document first for collections with predefined IDs
                self._validate_document(collection, document)
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    INSERT INTO gateways_docs (gateway_id, document)
                    VALUES (?, ?)
                ''', (document['gateway_id'], doc_json))
                doc_id = document['gateway_id']
                
            elif collection == 'devices':
                # Validate document first for collections with predefined IDs
                self._validate_document(collection, document)
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    INSERT INTO devices_docs (device_id, document)
                    VALUES (?, ?)
                ''', (document['device_id'], doc_json))
                doc_id = document['device_id']
                
            elif collection == 'measurements':
                # For measurements, assign ID first, then validate
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    INSERT INTO measurements_docs (document)
                    VALUES (?)
                ''', (doc_json,))
                doc_id = cursor.lastrowid
                # Update the document with the generated ID
                document['measurement_id'] = doc_id
                # Now validate the complete document
                self._validate_document(collection, document)
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    UPDATE measurements_docs SET document = ? WHERE measurement_id = ?
                ''', (doc_json, doc_id))
                
            elif collection == 'config_updates':
                # Validate document first for collections with predefined IDs
                self._validate_document(collection, document)
                doc_json = self._serialize_document(document)
                cursor.execute('''
                    INSERT INTO config_updates_docs (update_id, document)
                    VALUES (?, ?)
                ''', (document['update_id'], doc_json))
                doc_id = document['update_id']
                
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            conn.commit()
            logger.info(f"Created document in {collection} with ID: {doc_id}")
            return doc_id
            
        except Exception as e:
            logger.error(f"Error creating document in {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def get_document(self, collection: str, doc_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        """Get a document by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if collection == 'vendor_invoices':
                cursor.execute('SELECT document FROM vendor_invoices_docs WHERE invoice_id = ?', (doc_id,))
            elif collection == 'gateways':
                cursor.execute('SELECT document FROM gateways_docs WHERE gateway_id = ?', (doc_id,))
            elif collection == 'devices':
                cursor.execute('SELECT document FROM devices_docs WHERE device_id = ?', (doc_id,))
            elif collection == 'measurements':
                cursor.execute('SELECT document FROM measurements_docs WHERE measurement_id = ?', (doc_id,))
            elif collection == 'config_updates':
                cursor.execute('SELECT document FROM config_updates_docs WHERE update_id = ?', (doc_id,))
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            row = cursor.fetchone()
            if row:
                return self._deserialize_document(row['document'])
            return None
            
        except Exception as e:
            logger.error(f"Error getting document from {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def update_document(self, collection: str, doc_id: Union[str, int], document: Dict[str, Any]) -> bool:
        """Update a document by ID"""
        # Validate document
        self._validate_document(collection, document)
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            doc_json = self._serialize_document(document)
            
            if collection == 'vendor_invoices':
                cursor.execute('''
                    UPDATE vendor_invoices_docs 
                    SET document = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE invoice_id = ?
                ''', (doc_json, doc_id))
            elif collection == 'gateways':
                cursor.execute('''
                    UPDATE gateways_docs 
                    SET document = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE gateway_id = ?
                ''', (doc_json, doc_id))
            elif collection == 'devices':
                cursor.execute('''
                    UPDATE devices_docs 
                    SET document = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE device_id = ?
                ''', (doc_json, doc_id))
            elif collection == 'measurements':
                cursor.execute('''
                    UPDATE measurements_docs 
                    SET document = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE measurement_id = ?
                ''', (doc_json, doc_id))
            elif collection == 'config_updates':
                cursor.execute('''
                    UPDATE config_updates_docs 
                    SET document = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE update_id = ?
                ''', (doc_json, doc_id))
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            success = cursor.rowcount > 0
            conn.commit()
            
            if success:
                logger.info(f"Updated document in {collection} with ID: {doc_id}")
            else:
                logger.warning(f"No document found to update in {collection} with ID: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error updating document in {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def delete_document(self, collection: str, doc_id: Union[str, int]) -> bool:
        """Delete a document by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if collection == 'vendor_invoices':
                cursor.execute('DELETE FROM vendor_invoices_docs WHERE invoice_id = ?', (doc_id,))
            elif collection == 'gateways':
                cursor.execute('DELETE FROM gateways_docs WHERE gateway_id = ?', (doc_id,))
            elif collection == 'devices':
                cursor.execute('DELETE FROM devices_docs WHERE device_id = ?', (doc_id,))
            elif collection == 'measurements':
                cursor.execute('DELETE FROM measurements_docs WHERE measurement_id = ?', (doc_id,))
            elif collection == 'config_updates':
                cursor.execute('DELETE FROM config_updates_docs WHERE update_id = ?', (doc_id,))
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            success = cursor.rowcount > 0
            conn.commit()
            
            if success:
                logger.info(f"Deleted document from {collection} with ID: {doc_id}")
            else:
                logger.warning(f"No document found to delete in {collection} with ID: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error deleting document from {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def query_documents(self, collection: str, query: Dict[str, Any], limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Query documents with filters"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Build the query based on collection and filters
            table_name = f"{collection}_docs"
            base_query = f"SELECT document FROM {table_name}"
            where_clauses = []
            params = []
            
            # Build WHERE clauses based on query filters
            for key, value in query.items():
                if key == '_id':
                    # Special case for document ID
                    if collection == 'vendor_invoices':
                        where_clauses.append("invoice_id = ?")
                    elif collection == 'gateways':
                        where_clauses.append("gateway_id = ?")
                    elif collection == 'devices':
                        where_clauses.append("device_id = ?")
                    elif collection == 'measurements':
                        where_clauses.append("measurement_id = ?")
                    elif collection == 'config_updates':
                        where_clauses.append("update_id = ?")
                    params.append(value)
                else:
                    # JSON path query
                    json_path = f"$.{key}"
                    where_clauses.append(f"json_extract(document, ?) = ?")
                    params.extend([json_path, value])
            
            if where_clauses:
                base_query += " WHERE " + " AND ".join(where_clauses)
            
            base_query += f" LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(base_query, params)
            rows = cursor.fetchall()
            
            documents = []
            for row in rows:
                documents.append(self._deserialize_document(row['document']))
            
            return documents
            
        except Exception as e:
            logger.error(f"Error querying documents from {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def count_documents(self, collection: str, query: Dict[str, Any] = None) -> int:
        """Count documents matching query"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            table_name = f"{collection}_docs"
            base_query = f"SELECT COUNT(*) as count FROM {table_name}"
            params = []
            
            if query:
                where_clauses = []
                for key, value in query.items():
                    if key == '_id':
                        # Special case for document ID
                        if collection == 'vendor_invoices':
                            where_clauses.append("invoice_id = ?")
                        elif collection == 'gateways':
                            where_clauses.append("gateway_id = ?")
                        elif collection == 'devices':
                            where_clauses.append("device_id = ?")
                        elif collection == 'measurements':
                            where_clauses.append("measurement_id = ?")
                        elif collection == 'config_updates':
                            where_clauses.append("update_id = ?")
                        params.append(value)
                    else:
                        # JSON path query
                        json_path = f"$.{key}"
                        where_clauses.append(f"json_extract(document, ?) = ?")
                        params.extend([json_path, value])
                
                if where_clauses:
                    base_query += " WHERE " + " AND ".join(where_clauses)
            
            cursor.execute(base_query, params)
            result = cursor.fetchone()
            return result['count']
            
        except Exception as e:
            logger.error(f"Error counting documents in {collection}: {e}")
            raise
        finally:
            conn.close()
    
    def aggregate_documents(self, collection: str, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Simple aggregation support for common operations"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # This is a simplified aggregation - in a real implementation,
            # you'd want to build a proper query builder for complex aggregations
            table_name = f"{collection}_docs"
            
            # For now, support basic group by operations
            if len(pipeline) == 1 and '$group' in pipeline[0]:
                group_stage = pipeline[0]['$group']
                group_by_field = group_stage.get('_id', {}).get('$', '')
                
                if group_by_field:
                    query = f'''
                        SELECT 
                            json_extract(document, '$.{group_by_field}') as group_key,
                            COUNT(*) as count
                        FROM {table_name}
                        WHERE json_extract(document, '$.{group_by_field}') IS NOT NULL
                        GROUP BY json_extract(document, '$.{group_by_field}')
                        ORDER BY count DESC
                    '''
                    
                    cursor.execute(query)
                    rows = cursor.fetchall()
                    
                    results = []
                    for row in rows:
                        results.append({
                            '_id': row['group_key'],
                            'count': row['count']
                        })
                    
                    return results
            
            # Fallback to simple document retrieval
            cursor.execute(f"SELECT document FROM {table_name}")
            rows = cursor.fetchall()
            
            documents = []
            for row in rows:
                documents.append(self._deserialize_document(row['document']))
            
            return documents
            
        except Exception as e:
            logger.error(f"Error aggregating documents from {collection}: {e}")
            raise
        finally:
            conn.close()