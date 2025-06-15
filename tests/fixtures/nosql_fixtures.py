"""
NoSQL Test Fixtures for Document-based Operations.
Provides reusable test data for IoT and Files API services.
"""

import os
import tempfile
from datetime import datetime, timedelta
from typing import Dict, Any, List
from database.nosql_adapter import NoSQLAdapter
from database.indexes import DocumentIndexManager


class NoSQLTestFixtures:
    """Provides test fixtures for NoSQL document operations"""
    
    def __init__(self):
        self.temp_db = None
        self.adapter = None
        self.index_manager = None
    
    def setup_test_database(self) -> str:
        """Create a temporary test database"""
        # Create temporary database file
        fd, self.temp_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        # Initialize adapter and indexes
        self.adapter = NoSQLAdapter(self.temp_db)
        self.adapter.init_collections()
        
        self.index_manager = DocumentIndexManager(self.temp_db)
        self.index_manager.create_all_indexes()
        
        return self.temp_db
    
    def cleanup_test_database(self):
        """Clean up temporary test database"""
        if self.temp_db and os.path.exists(self.temp_db):
            os.unlink(self.temp_db)
        self.temp_db = None
        self.adapter = None
        self.index_manager = None
    
    # ==========================================
    # Files API Fixtures
    # ==========================================
    
    def get_sample_vendors(self) -> List[Dict[str, Any]]:
        """Get sample vendor data"""
        return [
            {
                "vendor_id": "V001",
                "vendor_name": "GreenTech Recycling",
                "created_at": datetime(2023, 1, 15),
                "is_active": True
            },
            {
                "vendor_id": "V002", 
                "vendor_name": "EcoWaste Solutions",
                "created_at": datetime(2023, 2, 20),
                "is_active": True
            },
            {
                "vendor_id": "V003",
                "vendor_name": "Metro Disposal Inc",
                "created_at": datetime(2023, 3, 10),
                "is_active": False
            }
        ]
    
    def get_sample_categories(self) -> List[Dict[str, Any]]:
        """Get sample category data"""
        return [
            {
                "category_id": 1,
                "category_name": "Recyclable",
                "description": "Materials that can be recycled"
            },
            {
                "category_id": 2,
                "category_name": "Metal",
                "description": "Metal waste and scrap"
            },
            {
                "category_id": 3,
                "category_name": "Plastic",
                "description": "Plastic materials and products"
            }
        ]
    
    def get_sample_invoices(self) -> List[Dict[str, Any]]:
        """Get sample invoice documents with embedded vendor and category"""
        vendors = self.get_sample_vendors()
        categories = self.get_sample_categories()
        
        return [
            {
                "invoice_id": 1,
                "vendor": vendors[0],
                "category": categories[0],
                "invoice_number": "INV-2023-001",
                "invoice_date": datetime(2023, 6, 1),
                "upload_date": datetime(2023, 6, 1, 10, 30),
                "filename": "greentech_invoice_001.pdf",
                "filepath": "/uploads/2023/06/greentech_invoice_001.pdf",
                "reported_weight_kg": 150.5,
                "unit_price": 0.50,
                "total_amount": 75.25,
                "extraction_status": "completed",
                "processing_date": datetime(2023, 6, 1, 11, 0),
                "completion_date": datetime(2023, 6, 1, 11, 15),
                "error_message": None
            },
            {
                "invoice_id": 2,
                "vendor": vendors[1],
                "category": categories[1],
                "invoice_number": "INV-2023-002",
                "invoice_date": datetime(2023, 6, 5),
                "upload_date": datetime(2023, 6, 5, 14, 20),
                "filename": "ecowaste_metal_002.pdf",
                "filepath": "/uploads/2023/06/ecowaste_metal_002.pdf",
                "reported_weight_kg": 250.0,
                "unit_price": 1.20,
                "total_amount": 300.0,
                "extraction_status": "completed",
                "processing_date": datetime(2023, 6, 5, 14, 45),
                "completion_date": datetime(2023, 6, 5, 15, 0),
                "error_message": None
            },
            {
                "invoice_id": 3,
                "vendor": vendors[0],
                "category": categories[2],
                "invoice_number": "INV-2023-003",
                "invoice_date": datetime(2023, 6, 10),
                "upload_date": datetime(2023, 6, 10, 9, 0),
                "filename": "greentech_plastic_003.pdf",
                "filepath": "/uploads/2023/06/greentech_plastic_003.pdf",
                "reported_weight_kg": None,
                "unit_price": None,
                "total_amount": None,
                "extraction_status": "pending",
                "processing_date": None,
                "completion_date": None,
                "error_message": None
            }
        ]
    
    # ==========================================
    # IoT Fixtures
    # ==========================================
    
    def get_sample_gateways(self) -> List[Dict[str, Any]]:
        """Get sample gateway documents"""
        return [
            {
                "gateway_id": "GW001",
                "name": "Factory Floor Gateway",
                "location": "Building A - Floor 1",
                "status": "connected",
                "last_updated": datetime(2023, 6, 15, 10, 0),
                "last_heartbeat": datetime(2023, 6, 15, 10, 0),
                "uptime": "72h",
                "health": "good",
                "error": None,
                "created_at": datetime(2023, 6, 1, 8, 0),
                "connected_at": datetime(2023, 6, 1, 8, 5),
                "disconnected_at": None,
                "deleted_at": None,
                "certificate_info": '{"status": "installed", "expires": "2024-06-01"}'
            },
            {
                "gateway_id": "GW002",
                "name": "Warehouse Gateway",
                "location": "Warehouse - Zone B",
                "status": "disconnected",
                "last_updated": datetime(2023, 6, 14, 16, 30),
                "last_heartbeat": datetime(2023, 6, 14, 15, 45),
                "uptime": "45h",
                "health": "warning",
                "error": '{"status": "reported offline"}',
                "created_at": datetime(2023, 6, 10, 12, 0),
                "connected_at": datetime(2023, 6, 10, 12, 5),
                "disconnected_at": datetime(2023, 6, 14, 16, 30),
                "deleted_at": None,
                "certificate_info": '{"status": "installed", "expires": "2024-06-10"}'
            }
        ]
    
    def get_sample_devices(self) -> List[Dict[str, Any]]:
        """Get sample device documents"""
        return [
            {
                "device_id": "SCALE001",
                "gateway_id": "GW001",
                "device_type": "scale",
                "name": "Industrial Scale #1",
                "location": "Production Line A",
                "status": "online",
                "last_updated": datetime(2023, 6, 15, 10, 30),
                "last_measurement": datetime(2023, 6, 15, 10, 25),
                "last_config_fetch": datetime(2023, 6, 1, 8, 10),
                "config_version": "v1.2.0",
                "config_hash": "abc123def456",
                "device_config": {
                    "measurement_interval": 30,
                    "precision": 0.1,
                    "max_weight": 1000
                }
            },
            {
                "device_id": "SCALE002",
                "gateway_id": "GW002",
                "device_type": "scale",
                "name": "Industrial Scale #2",
                "location": "Warehouse Station",
                "status": "offline",
                "last_updated": datetime(2023, 6, 14, 16, 0),
                "last_measurement": datetime(2023, 6, 14, 15, 30),
                "last_config_fetch": datetime(2023, 6, 10, 12, 10),
                "config_version": "v1.1.0",
                "config_hash": "xyz789ghi012",
                "device_config": {
                    "measurement_interval": 60,
                    "precision": 0.5,
                    "max_weight": 500
                }
            }
        ]
    
    def get_sample_measurements(self) -> List[Dict[str, Any]]:
        """Get sample measurement documents with embedded device info"""
        devices = self.get_sample_devices()
        
        return [
            {
                "measurement_id": 1,
                "device_info": {
                    "device_id": devices[0]["device_id"],
                    "gateway_id": devices[0]["gateway_id"],
                    "device_type": devices[0]["device_type"],
                    "name": devices[0]["name"],
                    "location": devices[0]["location"],
                    "status": devices[0]["status"]
                },
                "measurement_type": "weight_measurement",
                "timestamp": datetime(2023, 6, 15, 10, 25),
                "processed": True,
                "uploaded_to_cloud": False,
                "payload": {
                    "weight_kg": 125.5,
                    "material_type": "plastic",
                    "container_id": "BIN001",
                    "operator_id": "OP123"
                }
            },
            {
                "measurement_id": 2,
                "device_info": {
                    "device_id": devices[0]["device_id"],
                    "gateway_id": devices[0]["gateway_id"],
                    "device_type": devices[0]["device_type"],
                    "name": devices[0]["name"],
                    "location": devices[0]["location"],
                    "status": devices[0]["status"]
                },
                "measurement_type": "weight_measurement",
                "timestamp": datetime(2023, 6, 15, 10, 55),
                "processed": False,
                "uploaded_to_cloud": False,
                "payload": {
                    "weight_kg": 87.3,
                    "material_type": "metal",
                    "container_id": "BIN002",
                    "operator_id": "OP124"
                }
            },
            {
                "measurement_id": 3,
                "device_info": {
                    "device_id": devices[1]["device_id"],
                    "gateway_id": devices[1]["gateway_id"],
                    "device_type": devices[1]["device_type"],
                    "name": devices[1]["name"],
                    "location": devices[1]["location"],
                    "status": "online"  # Different status for testing
                },
                "measurement_type": "weight_measurement",
                "timestamp": datetime(2023, 6, 14, 15, 30),
                "processed": True,
                "uploaded_to_cloud": True,
                "payload": {
                    "weight_kg": 200.0,
                    "material_type": "paper",
                    "container_id": "BIN003",
                    "operator_id": "OP125"
                }
            }
        ]
    
    # ==========================================
    # Data Setup Methods
    # ==========================================
    
    def populate_files_api_data(self):
        """Populate test database with Files API sample data"""
        if not self.adapter:
            raise RuntimeError("Test database not initialized")
        
        # Create invoice documents
        for invoice in self.get_sample_invoices():
            self.adapter.create_document('vendor_invoices', invoice)
    
    def populate_iot_data(self):
        """Populate test database with IoT sample data"""
        if not self.adapter:
            raise RuntimeError("Test database not initialized")
        
        # Create gateway documents
        for gateway in self.get_sample_gateways():
            self.adapter.create_document('gateways', gateway)
        
        # Create device documents
        for device in self.get_sample_devices():
            self.adapter.create_document('devices', device)
        
        # Create measurement documents
        for measurement in self.get_sample_measurements():
            self.adapter.create_document('measurements', measurement)
    
    def populate_all_data(self):
        """Populate test database with all sample data"""
        self.populate_files_api_data()
        self.populate_iot_data()
    
    # ==========================================
    # Query Test Helpers
    # ==========================================
    
    def get_test_query_scenarios(self) -> Dict[str, Dict[str, Any]]:
        """Get common query scenarios for testing"""
        return {
            'files_api': {
                'vendor_invoices_by_vendor': {
                    'collection': 'vendor_invoices',
                    'query': {'vendor.vendor_id': 'V001'},
                    'expected_count': 2
                },
                'completed_invoices': {
                    'collection': 'vendor_invoices',
                    'query': {'extraction_status': 'completed'},
                    'expected_count': 2
                },
                'metal_category_invoices': {
                    'collection': 'vendor_invoices',
                    'query': {'category.category_name': 'Metal'},
                    'expected_count': 1
                }
            },
            'iot': {
                'connected_gateways': {
                    'collection': 'gateways',
                    'query': {'status': 'connected'},
                    'expected_count': 1
                },
                'scale_devices': {
                    'collection': 'devices',
                    'query': {'device_type': 'scale'},
                    'expected_count': 2
                },
                'measurements_by_device': {
                    'collection': 'measurements',
                    'query': {'device_info.device_id': 'SCALE001'},
                    'expected_count': 2
                },
                'processed_measurements': {
                    'collection': 'measurements',
                    'query': {'processed': True},
                    'expected_count': 2
                }
            }
        }


# Global fixture instance
_fixtures = None

def get_nosql_fixtures() -> NoSQLTestFixtures:
    """Get or create test fixtures instance"""
    global _fixtures
    if _fixtures is None:
        _fixtures = NoSQLTestFixtures()
    return _fixtures