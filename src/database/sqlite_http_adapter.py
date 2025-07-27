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
        """Create a new document in the collection."""
        try:
            # Generate document ID if not provided
            doc_id = document.get('id') or document.get('_id') or self._generate_doc_id()
            
            # Serialize document to JSON
            doc_json = json.dumps(document, default=str)
            
            # Insert document
            result = self._execute_command(
                """INSERT INTO documents (collection, doc_id, document, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?)""",
                (collection, str(doc_id), doc_json, datetime.utcnow(), datetime.utcnow())
            )
            
            logger.debug(f"Created document in {collection}: {doc_id}")
            return doc_id
            
        except Exception as e:
            logger.error(f"Failed to create document in {collection}: {e}")
            raise
    
    def get_document(self, collection: str, doc_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        """Get a document by ID from the collection."""
        try:
            results = self._execute_query(
                "SELECT document FROM documents WHERE collection = ? AND doc_id = ?",
                (collection, str(doc_id))
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
        """Query documents from the collection with filters."""
        try:
            # Build SQL query based on document query
            sql_query = "SELECT document FROM documents WHERE collection = ?"
            params = [collection]
            
            # Add query filters (simplified implementation)
            for key, value in query.items():
                if key.startswith('$'):
                    # Skip MongoDB-style operators for now
                    continue
                
                # Simple JSON field matching
                sql_query += " AND JSON_EXTRACT(document, ?) = ?"
                params.extend([f"$.{key}", value])
            
            # Add ordering and pagination
            sql_query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            results = self._execute_query(sql_query, tuple(params))
            
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
        """Count documents in the collection matching the query."""
        try:
            if not query:
                # Simple count all
                results = self._execute_query(
                    "SELECT COUNT(*) as count FROM documents WHERE collection = ?",
                    (collection,)
                )
            else:
                # Build SQL query with filters
                sql_query = "SELECT COUNT(*) as count FROM documents WHERE collection = ?"
                params = [collection]
                
                for key, value in query.items():
                    if key.startswith('$'):
                        continue
                    sql_query += " AND JSON_EXTRACT(document, ?) = ?"
                    params.extend([f"$.{key}", value])
                
                results = self._execute_query(sql_query, tuple(params))
            
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