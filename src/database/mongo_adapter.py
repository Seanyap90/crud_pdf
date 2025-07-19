"""
MongoDB adapter for document-based operations.
Provides identical interface to NoSQLAdapter but uses native MongoDB collections.
"""

import os
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from .schemas import DOCUMENT_VALIDATORS

try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, OperationFailure
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False
    MongoClient = None

logger = logging.getLogger(__name__)


class MongoAdapter:
    """MongoDB adapter for document-based database operations"""
    
    def __init__(self, connection_string: Optional[str] = None):
        if not PYMONGO_AVAILABLE:
            raise ImportError("pymongo is required for MongoAdapter. Install with: pip install pymongo>=4.0.0")
        
        self.connection_string = connection_string or os.getenv('MONGODB_URI')
        if not self.connection_string:
            raise ValueError("MongoDB connection string required. Set MONGODB_URI environment variable or pass connection_string")
        
        self.client = None
        self.db = None
        self._connect()
        
    def _connect(self) -> None:
        """Establish MongoDB connection"""
        try:
            self.client = MongoClient(self.connection_string)
            # Extract database name from connection string
            if '/crud_pdf' in self.connection_string:
                db_name = 'crud_pdf'
            else:
                # Fallback to extracting from URI path
                db_name = self.connection_string.split('/')[-1].split('?')[0]
                if not db_name:
                    db_name = 'crud_pdf'
            
            self.db = self.client[db_name]
            
            # Test connection
            self.client.admin.command('ping')
            logger.info(f"Connected to MongoDB database: {db_name}")
            
        except ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        except Exception as e:
            logger.error(f"MongoDB connection error: {e}")
            raise
    
    def _validate_document(self, collection: str, document: Dict[str, Any]) -> None:
        """Validate document against schema"""
        if collection in DOCUMENT_VALIDATORS:
            try:
                DOCUMENT_VALIDATORS[collection](document)
            except Exception as e:
                logger.error(f"Document validation failed for {collection}: {e}")
                raise ValueError(f"Document validation failed: {e}")
    
    def _serialize_datetime(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Convert datetime objects to MongoDB-compatible format"""
        def convert_value(value):
            if isinstance(value, datetime):
                return value
            elif isinstance(value, str):
                # Try to parse ISO format datetime strings
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    return value
            elif isinstance(value, dict):
                return {k: convert_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [convert_value(item) for item in value]
            return value
        
        return convert_value(document)
    
    def init_collections(self) -> None:
        """Initialize MongoDB collections and indexes"""
        try:
            # Create collections (MongoDB creates them automatically on first insert)
            collections = ['vendor_invoices', 'gateways', 'devices', 'measurements', 'config_updates']
            
            for collection_name in collections:
                collection = self.db[collection_name]
                
                # Create indexes based on the collection
                if collection_name == 'vendor_invoices':
                    collection.create_index([("vendor.vendor_id", 1)])
                    collection.create_index([("extraction_status", 1)])
                    collection.create_index([("upload_date", -1)])
                    collection.create_index([("invoice_id", 1)], unique=True)
                    
                elif collection_name == 'gateways':
                    collection.create_index([("status", 1)])
                    collection.create_index([("last_updated", -1)])
                    collection.create_index([("gateway_id", 1)], unique=True)
                    
                elif collection_name == 'devices':
                    collection.create_index([("gateway_id", 1)])
                    collection.create_index([("status", 1)])
                    collection.create_index([("device_id", 1)], unique=True)
                    
                elif collection_name == 'measurements':
                    collection.create_index([("device_info.device_id", 1)])
                    collection.create_index([("device_info.gateway_id", 1)])
                    collection.create_index([("timestamp", -1)])
                    collection.create_index([("measurement_type", 1)])
                    # measurement_id will be auto-generated _id
                    
                elif collection_name == 'config_updates':
                    collection.create_index([("update_id", 1)], unique=True)
            
            logger.info("MongoDB collections and indexes initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing MongoDB collections: {e}")
            raise
    
    def create_document(self, collection: str, document: Dict[str, Any]) -> Union[str, int]:
        """Create a new document in the collection"""
        try:
            # Validate document
            self._validate_document(collection, document)
            
            # Serialize datetime objects
            document = self._serialize_datetime(document)
            
            collection_obj = self.db[collection]
            
            if collection == 'vendor_invoices':
                doc_id = document['invoice_id']
                result = collection_obj.insert_one(document)
                
            elif collection == 'gateways':
                doc_id = document['gateway_id']
                result = collection_obj.insert_one(document)
                
            elif collection == 'devices':
                doc_id = document['device_id']
                result = collection_obj.insert_one(document)
                
            elif collection == 'measurements':
                # For measurements, use MongoDB's auto-generated _id and add measurement_id
                if 'measurement_id' not in document:
                    # Get next sequence number
                    count = collection_obj.count_documents({})
                    document['measurement_id'] = count + 1
                
                result = collection_obj.insert_one(document)
                doc_id = document['measurement_id']
                
            elif collection == 'config_updates':
                doc_id = document['update_id']
                result = collection_obj.insert_one(document)
                
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            logger.info(f"Created document in {collection} with ID: {doc_id}")
            return doc_id
            
        except Exception as e:
            logger.error(f"Error creating document in {collection}: {e}")
            raise
    
    def get_document(self, collection: str, doc_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        """Get a document by ID"""
        try:
            collection_obj = self.db[collection]
            
            if collection == 'vendor_invoices':
                query = {"invoice_id": doc_id}
            elif collection == 'gateways':
                query = {"gateway_id": doc_id}
            elif collection == 'devices':
                query = {"device_id": doc_id}
            elif collection == 'measurements':
                query = {"measurement_id": doc_id}
            elif collection == 'config_updates':
                query = {"update_id": doc_id}
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            document = collection_obj.find_one(query)
            if document:
                # Remove MongoDB's _id field for compatibility with NoSQLAdapter interface
                document.pop('_id', None)
                return document
            return None
            
        except Exception as e:
            logger.error(f"Error getting document from {collection}: {e}")
            raise
    
    def update_document(self, collection: str, doc_id: Union[str, int], document: Dict[str, Any]) -> bool:
        """Update a document by ID"""
        try:
            # Validate document
            self._validate_document(collection, document)
            
            # Serialize datetime objects
            document = self._serialize_datetime(document)
            
            collection_obj = self.db[collection]
            
            if collection == 'vendor_invoices':
                query = {"invoice_id": doc_id}
            elif collection == 'gateways':
                query = {"gateway_id": doc_id}
            elif collection == 'devices':
                query = {"device_id": doc_id}
            elif collection == 'measurements':
                query = {"measurement_id": doc_id}
            elif collection == 'config_updates':
                query = {"update_id": doc_id}
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            # Add updated_at timestamp
            document['updated_at'] = datetime.now()
            
            result = collection_obj.replace_one(query, document)
            success = result.modified_count > 0
            
            if success:
                logger.info(f"Updated document in {collection} with ID: {doc_id}")
            else:
                logger.warning(f"No document found to update in {collection} with ID: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error updating document in {collection}: {e}")
            raise
    
    def delete_document(self, collection: str, doc_id: Union[str, int]) -> bool:
        """Delete a document by ID"""
        try:
            collection_obj = self.db[collection]
            
            if collection == 'vendor_invoices':
                query = {"invoice_id": doc_id}
            elif collection == 'gateways':
                query = {"gateway_id": doc_id}
            elif collection == 'devices':
                query = {"device_id": doc_id}
            elif collection == 'measurements':
                query = {"measurement_id": doc_id}
            elif collection == 'config_updates':
                query = {"update_id": doc_id}
            else:
                raise ValueError(f"Unknown collection: {collection}")
            
            result = collection_obj.delete_one(query)
            success = result.deleted_count > 0
            
            if success:
                logger.info(f"Deleted document from {collection} with ID: {doc_id}")
            else:
                logger.warning(f"No document found to delete in {collection} with ID: {doc_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error deleting document from {collection}: {e}")
            raise
    
    def query_documents(self, collection: str, query: Dict[str, Any], limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Query documents with filters"""
        try:
            collection_obj = self.db[collection]
            
            # Convert query to MongoDB format
            mongo_query = {}
            for key, value in query.items():
                if key == '_id':
                    # Handle special _id queries
                    if collection == 'vendor_invoices':
                        mongo_query['invoice_id'] = value
                    elif collection == 'gateways':
                        mongo_query['gateway_id'] = value
                    elif collection == 'devices':
                        mongo_query['device_id'] = value
                    elif collection == 'measurements':
                        mongo_query['measurement_id'] = value
                    elif collection == 'config_updates':
                        mongo_query['update_id'] = value
                else:
                    mongo_query[key] = value
            
            # Execute query with pagination
            cursor = collection_obj.find(mongo_query).skip(offset).limit(limit)
            
            documents = []
            for doc in cursor:
                # Remove MongoDB's _id field for compatibility
                doc.pop('_id', None)
                documents.append(doc)
            
            return documents
            
        except Exception as e:
            logger.error(f"Error querying documents from {collection}: {e}")
            raise
    
    def count_documents(self, collection: str, query: Dict[str, Any] = None) -> int:
        """Count documents matching query"""
        try:
            collection_obj = self.db[collection]
            
            if query:
                # Convert query to MongoDB format
                mongo_query = {}
                for key, value in query.items():
                    if key == '_id':
                        # Handle special _id queries
                        if collection == 'vendor_invoices':
                            mongo_query['invoice_id'] = value
                        elif collection == 'gateways':
                            mongo_query['gateway_id'] = value
                        elif collection == 'devices':
                            mongo_query['device_id'] = value
                        elif collection == 'measurements':
                            mongo_query['measurement_id'] = value
                        elif collection == 'config_updates':
                            mongo_query['update_id'] = value
                    else:
                        mongo_query[key] = value
                
                return collection_obj.count_documents(mongo_query)
            else:
                return collection_obj.count_documents({})
            
        except Exception as e:
            logger.error(f"Error counting documents in {collection}: {e}")
            raise
    
    def aggregate_documents(self, collection: str, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """MongoDB native aggregation support"""
        try:
            collection_obj = self.db[collection]
            
            # Use MongoDB's native aggregation pipeline
            cursor = collection_obj.aggregate(pipeline)
            
            documents = []
            for doc in cursor:
                # Remove MongoDB's _id field for compatibility unless it's part of grouping
                if '_id' in doc and not isinstance(doc['_id'], dict):
                    doc.pop('_id', None)
                documents.append(doc)
            
            return documents
            
        except Exception as e:
            logger.error(f"Error aggregating documents from {collection}: {e}")
            raise
    
    def close(self) -> None:
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")