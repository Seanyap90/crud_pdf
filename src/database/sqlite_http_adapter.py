"""SQLite HTTP Adapter - HTTP client implementing NoSQLAdapter interface."""
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Union, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class SQLiteHTTPAdapter:
    """HTTP client adapter implementing NoSQLAdapter interface for EC2 SQLite server."""
    
    def __init__(self, host: str, port: int = 8080, timeout: int = 30):
        """Initialize HTTP adapter for SQLite server.
        
        Args:
            host: EC2 instance private IP or hostname
            port: SQLite HTTP server port (default 8080)
            timeout: Request timeout in seconds
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        
        # Test connectivity on initialization
        self._test_connection()
        
        logger.info(f"SQLiteHTTPAdapter initialized for {self.base_url}")
    
    def _test_connection(self) -> None:
        """Test connection to SQLite HTTP server."""
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=self.timeout
            )
            response.raise_for_status()
            logger.info(f"Successfully connected to SQLite HTTP server at {self.base_url}")
        except requests.RequestException as e:
            logger.error(f"Failed to connect to SQLite HTTP server at {self.base_url}: {e}")
            raise ConnectionError(f"Cannot connect to SQLite HTTP server: {e}")
    
    def _execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute SELECT query via HTTP."""
        try:
            response = self.session.post(
                f"{self.base_url}/query",
                json={'query': query, 'params': list(params)},
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get('results', [])
        except requests.RequestException as e:
            logger.error(f"Query failed: {query} - {e}")
            raise
    
    def _execute_command(self, command: str, params: tuple = ()) -> Dict[str, Any]:
        """Execute INSERT/UPDATE/DELETE command via HTTP."""
        try:
            response = self.session.post(
                f"{self.base_url}/execute",
                json={'command': command, 'params': list(params)},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Command failed: {command} - {e}")
            raise
    
    def _execute_transaction(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute multiple operations in a transaction via HTTP."""
        try:
            response = self.session.post(
                f"{self.base_url}/transaction",
                json={'operations': operations},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Transaction failed: {e}")
            raise
    
    # NoSQLAdapter interface implementation
    
    def create_document(self, collection: str, document: Dict[str, Any]) -> Union[str, int]:
        """Create a new document in the collection (matching NoSQLAdapter structure)."""
        try:
            # Serialize document to JSON
            doc_json = json.dumps(document, default=str)
            
            # Handle different collections with specific ID patterns (same as NoSQLAdapter)
            if collection == 'vendor_invoices':
                doc_id = document['invoice_id']
                self._execute_command(
                    "INSERT INTO vendor_invoices_docs (invoice_id, document) VALUES (?, ?)",
                    (doc_id, doc_json)
                )
                
            elif collection == 'gateways':
                doc_id = document['gateway_id']
                self._execute_command(
                    "INSERT INTO gateways_docs (gateway_id, document) VALUES (?, ?)",
                    (doc_id, doc_json)
                )
                
            elif collection == 'devices':
                doc_id = document['device_id']
                self._execute_command(
                    "INSERT INTO devices_docs (device_id, document) VALUES (?, ?)",
                    (doc_id, doc_json)
                )
                
            elif collection == 'measurements':
                # Auto-increment ID for measurements
                result = self._execute_command(
                    "INSERT INTO measurements_docs (document) VALUES (?)",
                    (doc_json,)
                )
                doc_id = result['lastrowid']
                
            elif collection == 'config_updates':
                doc_id = document['update_id']
                self._execute_command(
                    "INSERT INTO config_updates_docs (update_id, document) VALUES (?, ?)",
                    (doc_id, doc_json)
                )
                
            else:
                # Fallback for unknown collections
                doc_id = document.get('_id') or self._generate_doc_id()
                self._execute_command(
                    f"INSERT INTO {collection}_docs (document) VALUES (?)",
                    (doc_json,)
                )
            
            logger.debug(f"Created document in {collection}: {doc_id}")
            return doc_id
            
        except Exception as e:
            logger.error(f"Failed to create document in {collection}: {e}")
            raise
    
    def get_document(self, collection: str, doc_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        """Get a document by ID from the collection (matching NoSQLAdapter structure)."""
        try:
            # Use collection-specific table and ID column
            table_name = f"{collection}_docs"
            
            if collection == 'vendor_invoices':
                id_column = 'invoice_id'
            elif collection == 'gateways':
                id_column = 'gateway_id'
            elif collection == 'devices':
                id_column = 'device_id'
            elif collection == 'measurements':
                id_column = 'measurement_id'
            elif collection == 'config_updates':
                id_column = 'update_id'
            else:
                id_column = 'id'  # fallback
            
            results = self._execute_query(
                f"SELECT document FROM {table_name} WHERE {id_column} = ?",
                (doc_id,)
            )
            
            if not results:
                return None
            
            # Parse JSON document
            doc_json = results[0]['document']
            document = json.loads(doc_json)
            
            logger.debug(f"Retrieved document from {collection}: {doc_id}")
            return document
            
        except Exception as e:
            logger.error(f"Failed to get document from {collection}: {doc_id} - {e}")
            raise
    
    def update_document(self, collection: str, doc_id: Union[str, int], document: Dict[str, Any]) -> bool:
        """Update an existing document in the collection."""
        try:
            # Serialize document to JSON
            doc_json = json.dumps(document, default=str)
            
            # Update document
            result = self._execute_command(
                """UPDATE documents SET document = ?, updated_at = ? 
                   WHERE collection = ? AND doc_id = ?""",
                (doc_json, datetime.utcnow(), collection, str(doc_id))
            )
            
            success = result.get('rowcount', 0) > 0
            if success:
                logger.debug(f"Updated document in {collection}: {doc_id}")
            else:
                logger.warning(f"No document found to update in {collection}: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to update document in {collection}: {doc_id} - {e}")
            raise
    
    def delete_document(self, collection: str, doc_id: Union[str, int]) -> bool:
        """Delete a document from the collection."""
        try:
            result = self._execute_command(
                "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                (collection, str(doc_id))
            )
            
            success = result.get('rowcount', 0) > 0
            if success:
                logger.debug(f"Deleted document from {collection}: {doc_id}")
            else:
                logger.warning(f"No document found to delete in {collection}: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to delete document from {collection}: {doc_id} - {e}")
            raise
    
    def query_documents(self, collection: str, query: Dict[str, Any], 
                       limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Query documents from the collection with filters (matching NoSQLAdapter structure)."""
        try:
            # Build SQL query using collection-specific table (same as NoSQLAdapter)
            table_name = f"{collection}_docs"
            base_query = f"SELECT document FROM {table_name}"
            where_clauses = []
            params = []
            
            # Build WHERE clauses based on query filters (same logic as NoSQLAdapter)
            for key, value in query.items():
                if key == '_id':
                    # Special case for document ID (same as NoSQLAdapter)
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
                    # JSON path query (same as NoSQLAdapter)
                    json_path = f"$.{key}"
                    where_clauses.append(f"json_extract(document, ?) = ?")
                    params.extend([json_path, value])
            
            if where_clauses:
                base_query += " WHERE " + " AND ".join(where_clauses)
            
            base_query += f" LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            results = self._execute_query(base_query, tuple(params))
            
            # Parse JSON documents
            documents = []
            for row in results:
                doc_json = row['document']
                document = json.loads(doc_json)
                documents.append(document)
            
            logger.debug(f"Queried {len(documents)} documents from {collection}")
            return documents
            
        except Exception as e:
            logger.error(f"Failed to query documents from {collection}: {e}")
            raise
    
    def count_documents(self, collection: str, query: Dict[str, Any] = None) -> int:
        """Count documents in the collection matching the query (matching NoSQLAdapter structure)."""
        try:
            # Use collection-specific table
            table_name = f"{collection}_docs"
            base_query = f"SELECT COUNT(*) as count FROM {table_name}"
            params = []
            
            if query:
                where_clauses = []
                for key, value in query.items():
                    if key == '_id':
                        # Special case for document ID (same as NoSQLAdapter)
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
                        # JSON path query (same as NoSQLAdapter)
                        json_path = f"$.{key}"
                        where_clauses.append(f"json_extract(document, ?) = ?")
                        params.extend([json_path, value])
                
                if where_clauses:
                    base_query += " WHERE " + " AND ".join(where_clauses)
            
            results = self._execute_query(base_query, tuple(params))
            count = results[0]['count'] if results else 0
            logger.debug(f"Counted {count} documents in {collection}")
            return count
            
        except Exception as e:
            logger.error(f"Failed to count documents in {collection}: {e}")
            raise
    
    def init_collections(self) -> None:
        """Initialize collections (ensure database tables exist)."""
        try:
            # The database table is created by the EC2 server on startup
            # Just verify connectivity
            self._test_connection()
            logger.info("Collections initialized (database tables verified)")
            
        except Exception as e:
            logger.error(f"Failed to initialize collections: {e}")
            raise
    
    def _generate_doc_id(self) -> str:
        """Generate a unique document ID."""
        import uuid
        return str(uuid.uuid4())
    
    # Additional utility methods
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get health status from the SQLite HTTP server."""
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Health check failed: {e}")
            raise
    
    def close(self) -> None:
        """Close the HTTP session."""
        if self.session:
            self.session.close()
            logger.info("SQLiteHTTPAdapter session closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
    
    def __del__(self):
        """Destructor to ensure session is closed."""
        try:
            self.close()
        except:
            pass  # Ignore errors during cleanup