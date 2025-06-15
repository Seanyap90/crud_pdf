"""
Unit Tests for NoSQL Document Operations.
Tests individual services and document operations in isolation.
"""

import pytest
import os
from datetime import datetime
from tests.fixtures.nosql_fixtures import get_nosql_fixtures

# Import services to test
from database.nosql_adapter import NoSQLAdapter
from database.indexes import DocumentIndexManager
from files_api.db_layer import get_vendor_service, get_invoice_service, get_category_service
from iot.db_layer import get_gateway_service, get_device_service, get_measurement_service


class TestNoSQLAdapter:
    """Test NoSQL adapter CRUD operations"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_adapter_initialization(self):
        """Test adapter initialization and collection setup"""
        adapter = NoSQLAdapter(self.db_path)
        adapter.init_collections()
        
        # Verify collections exist by trying to count documents
        assert adapter.count_documents('vendor_invoices') == 0
        assert adapter.count_documents('gateways') == 0
        assert adapter.count_documents('devices') == 0
        assert adapter.count_documents('measurements') == 0
    
    def test_document_crud_operations(self):
        """Test basic document CRUD operations"""
        adapter = self.fixtures.adapter
        
        # Test create
        test_doc = {"test_id": 1, "name": "Test Document", "value": 123.45}
        doc_id = adapter.create_document('vendor_invoices', test_doc)
        assert doc_id == 1
        
        # Test read
        retrieved_doc = adapter.get_document('vendor_invoices', doc_id)
        assert retrieved_doc is not None
        assert retrieved_doc['name'] == "Test Document"
        assert retrieved_doc['value'] == 123.45
        
        # Test update
        retrieved_doc['name'] = "Updated Document"
        updated = adapter.update_document('vendor_invoices', doc_id, retrieved_doc)
        assert updated is True
        
        # Verify update
        updated_doc = adapter.get_document('vendor_invoices', doc_id)
        assert updated_doc['name'] == "Updated Document"
        
        # Test delete
        deleted = adapter.delete_document('vendor_invoices', doc_id)
        assert deleted is True
        
        # Verify deletion
        deleted_doc = adapter.get_document('vendor_invoices', doc_id)
        assert deleted_doc is None
    
    def test_document_querying(self):
        """Test document query operations"""
        adapter = self.fixtures.adapter
        self.fixtures.populate_files_api_data()
        
        # Test simple query
        results = adapter.query_documents('vendor_invoices', {'extraction_status': 'completed'})
        assert len(results) == 2
        
        # Test query with embedded field
        results = adapter.query_documents('vendor_invoices', {'vendor.vendor_id': 'V001'})
        assert len(results) == 2
        
        # Test query with limit
        results = adapter.query_documents('vendor_invoices', {}, limit=1)
        assert len(results) == 1
        
        # Test count
        count = adapter.count_documents('vendor_invoices')
        assert count == 3


class TestDocumentIndexes:
    """Test document index creation and management"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_index_creation(self):
        """Test creating document indexes"""
        index_manager = self.fixtures.index_manager
        
        # Indexes should already be created in setup
        analysis = index_manager.analyze_indexes()
        
        # Verify indexes exist
        assert analysis['total_indexes'] > 0
        assert len(analysis['indexes']) > 0
        
        # Check for specific index types
        index_names = [idx['name'] for idx in analysis['indexes']]
        assert any('invoice' in name for name in index_names)
        assert any('gateway' in name for name in index_names)
        assert any('device' in name for name in index_names)
        assert any('measurement' in name for name in index_names)
    
    def test_index_rebuild(self):
        """Test dropping and rebuilding indexes"""
        index_manager = self.fixtures.index_manager
        
        # Get initial index count
        initial_analysis = index_manager.analyze_indexes()
        initial_count = initial_analysis['total_indexes']
        
        # Drop all indexes
        index_manager.drop_all_indexes()
        dropped_analysis = index_manager.analyze_indexes()
        assert dropped_analysis['total_indexes'] == 0
        
        # Rebuild indexes
        index_manager.create_all_indexes()
        rebuilt_analysis = index_manager.analyze_indexes()
        assert rebuilt_analysis['total_indexes'] == initial_count


class TestFilesAPIServices:
    """Test Files API NoSQL services"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        self.fixtures.populate_files_api_data()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_vendor_service_operations(self):
        """Test vendor service with embedded documents"""
        vendor_service = get_vendor_service(self.db_path)
        
        # Test get vendor by ID
        vendor = vendor_service.get_vendor_by_id('V001')
        assert vendor is not None
        assert vendor['vendor_name'] == 'GreenTech Recycling'
        
        # Test get vendor by name
        vendor = vendor_service.get_vendor_by_name('EcoWaste Solutions')
        assert vendor is not None
        assert vendor['vendor_id'] == 'V002'
        
        # Test list all vendors
        vendors = vendor_service.list_all_vendors()
        assert len(vendors) == 3
        
        # Test list active vendors only
        active_vendors = vendor_service.list_all_vendors(include_inactive=False)
        assert len(active_vendors) == 2
        
        # Test vendor statistics
        stats = vendor_service.get_vendor_statistics('V001')
        assert stats['total_invoices'] == 2
        assert stats['vendor_name'] == 'GreenTech Recycling'
        assert stats['total_amount'] == 75.25  # Only completed invoice
        
        # Test search vendors
        search_results = vendor_service.search_vendors('green')
        assert len(search_results) == 1
        assert search_results[0]['vendor_name'] == 'GreenTech Recycling'
    
    def test_invoice_service_operations(self):
        """Test invoice service operations"""
        invoice_service = get_invoice_service(self.db_path)
        
        # Test get invoice
        invoice = invoice_service.get_invoice(1)
        assert invoice is not None
        assert invoice['invoice_number'] == 'INV-2023-001'
        assert invoice['vendor']['vendor_id'] == 'V001'
        
        # Test create invoice
        new_invoice_id = invoice_service.create_invoice(
            filename="test_invoice.pdf",
            filepath="/test/path/test_invoice.pdf",
            vendor_name="Test Vendor",
            vendor_id="V999",
            category_id=1,
            category_name="Recyclable"
        )
        assert new_invoice_id == 4  # Should be next ID
        
        # Test update invoice status
        updated = invoice_service.update_invoice_status(
            new_invoice_id,
            status="completed",
            total_amount=100.0,
            reported_weight_kg=50.0
        )
        assert updated is True
        
        # Verify update
        updated_invoice = invoice_service.get_invoice(new_invoice_id)
        assert updated_invoice['extraction_status'] == 'completed'
        assert updated_invoice['total_amount'] == 100.0
        
        # Test list invoices with filters
        completed_invoices, count = invoice_service.list_invoices(status='completed')
        assert count == 3  # Original 2 + new one
        
        vendor_invoices, count = invoice_service.list_invoices(vendor_id='V001')
        assert count == 2
        
        # Test search invoices
        search_results = invoice_service.search_invoices('greentech')
        assert len(search_results) >= 1
    
    def test_category_service_operations(self):
        """Test category service with embedded documents"""
        category_service = get_category_service(self.db_path)
        
        # Test get category by ID
        category = category_service.get_category_by_id(1)
        assert category is not None
        assert category['category_name'] == 'Recyclable'
        
        # Test get category by name
        category = category_service.get_category_by_name('Metal')
        assert category is not None
        assert category['category_id'] == 2
        
        # Test list all categories
        categories = category_service.list_all_categories()
        assert len(categories) == 3
        
        # Test category statistics
        stats = category_service.get_category_statistics(1)
        assert stats['total_invoices'] == 1
        assert stats['category_name'] == 'Recyclable'
        
        # Test search categories
        search_results = category_service.search_categories('metal')
        assert len(search_results) == 1
        assert search_results[0]['category_name'] == 'Metal'


class TestIoTServices:
    """Test IoT NoSQL services"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        self.fixtures.populate_iot_data()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_gateway_service_operations(self):
        """Test gateway service operations"""
        gateway_service = get_gateway_service(self.db_path)
        
        # Test get gateway
        gateway = gateway_service.get_gateway('GW001')
        assert gateway is not None
        assert gateway['name'] == 'Factory Floor Gateway'
        assert gateway['status'] == 'connected'
        
        # Test create gateway
        new_gateway = gateway_service.create_gateway(
            gateway_id='GW003',
            name='Test Gateway',
            location='Test Location'
        )
        assert new_gateway['gateway_id'] == 'GW003'
        
        # Test update gateway
        updated = gateway_service.update_gateway(
            'GW003',
            name='Updated Gateway',
            location='Updated Location',
            status='connected'
        )
        assert updated is True
        
        # Test list gateways
        gateways = gateway_service.list_gateways()
        assert len(gateways) == 3  # Original 2 + new one
        
        # Test get connected gateways
        connected = gateway_service.get_connected_gateways()
        assert len(connected) >= 1
    
    def test_device_service_operations(self):
        """Test device service operations"""
        device_service = get_device_service(self.db_path)
        
        # Test get device
        device = device_service.get_device('SCALE001')
        assert device is not None
        assert device['name'] == 'Industrial Scale #1'
        assert device['gateway_id'] == 'GW001'
        
        # Test register device
        new_device = device_service.register_device(
            device_id='SCALE003',
            gateway_id='GW001',
            device_type='scale',
            status='online'
        )
        assert new_device['device_id'] == 'SCALE003'
        
        # Test list devices
        devices = device_service.list_devices()
        assert len(devices) == 3  # Original 2 + new one
        
        # Test list devices by gateway
        gw1_devices = device_service.list_devices(gateway_id='GW001')
        assert len(gw1_devices) == 2  # SCALE001 + new SCALE003
        
        # Test list online devices only
        online_devices = device_service.list_devices(include_offline=False)
        assert len(online_devices) >= 1
    
    def test_measurement_service_operations(self):
        """Test measurement service with embedded device info"""
        measurement_service = get_measurement_service(self.db_path)
        
        # Test store measurement
        measurement_id = measurement_service.store_measurement(
            device_id='SCALE001',
            gateway_id='GW001',
            measurement_type='weight_measurement',
            payload={'weight_kg': 99.9, 'material_type': 'glass'}
        )
        assert measurement_id == 4  # Should be next ID
        
        # Test get measurements
        measurements = measurement_service.get_measurements()
        assert len(measurements) == 4  # Original 3 + new one
        
        # Test get measurements by device
        device_measurements = measurement_service.get_measurements(device_id='SCALE001')
        assert len(device_measurements) == 3  # 2 original + new one
        
        # Test get measurements by gateway
        gateway_measurements = measurement_service.get_measurements(gateway_id='GW001')
        assert len(gateway_measurements) == 3
        
        # Test get unprocessed measurements
        unprocessed = measurement_service.get_unprocessed_measurements()
        assert len(unprocessed) >= 1


class TestEmbeddedDocuments:
    """Test embedded document operations and consistency"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        self.fixtures.populate_all_data()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_vendor_embedding_in_invoices(self):
        """Test vendor data embedded in invoice documents"""
        invoice_service = get_invoice_service(self.db_path)
        
        # Get invoice and verify embedded vendor
        invoice = invoice_service.get_invoice(1)
        assert 'vendor' in invoice
        assert invoice['vendor']['vendor_id'] == 'V001'
        assert invoice['vendor']['vendor_name'] == 'GreenTech Recycling'
        assert invoice['vendor']['is_active'] is True
    
    def test_category_embedding_in_invoices(self):
        """Test category data embedded in invoice documents"""
        invoice_service = get_invoice_service(self.db_path)
        
        # Get invoice and verify embedded category
        invoice = invoice_service.get_invoice(2)
        assert 'category' in invoice
        assert invoice['category']['category_id'] == 2
        assert invoice['category']['category_name'] == 'Metal'
    
    def test_device_info_embedding_in_measurements(self):
        """Test device info embedded in measurement documents"""
        measurement_service = get_measurement_service(self.db_path)
        
        # Get measurement and verify embedded device info
        measurements = measurement_service.get_measurements(device_id='SCALE001')
        assert len(measurements) > 0
        
        measurement = measurements[0]
        assert 'device_info' in measurement
        assert measurement['device_info']['device_id'] == 'SCALE001'
        assert measurement['device_info']['gateway_id'] == 'GW001'
        assert measurement['device_info']['device_type'] == 'scale'


class TestDocumentQueries:
    """Test complex document query operations"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        self.fixtures.populate_all_data()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_files_api_queries(self):
        """Test Files API query scenarios"""
        scenarios = self.fixtures.get_test_query_scenarios()['files_api']
        adapter = self.fixtures.adapter
        
        for scenario_name, scenario in scenarios.items():
            results = adapter.query_documents(
                scenario['collection'], 
                scenario['query']
            )
            assert len(results) == scenario['expected_count'], \
                f"Scenario {scenario_name} failed: expected {scenario['expected_count']}, got {len(results)}"
    
    def test_iot_queries(self):
        """Test IoT query scenarios"""
        scenarios = self.fixtures.get_test_query_scenarios()['iot']
        adapter = self.fixtures.adapter
        
        for scenario_name, scenario in scenarios.items():
            results = adapter.query_documents(
                scenario['collection'], 
                scenario['query']
            )
            assert len(results) == scenario['expected_count'], \
                f"Scenario {scenario_name} failed: expected {scenario['expected_count']}, got {len(results)}"
    
    def test_embedded_field_queries(self):
        """Test queries on embedded document fields"""
        adapter = self.fixtures.adapter
        
        # Query by embedded vendor name
        results = adapter.query_documents('vendor_invoices', {
            'vendor.vendor_name': 'GreenTech Recycling'
        })
        assert len(results) == 2
        
        # Query by embedded device info
        results = adapter.query_documents('measurements', {
            'device_info.device_type': 'scale'
        })
        assert len(results) == 3
        
        # Query by nested payload field
        results = adapter.query_documents('measurements', {
            'payload.material_type': 'plastic'
        })
        assert len(results) == 1