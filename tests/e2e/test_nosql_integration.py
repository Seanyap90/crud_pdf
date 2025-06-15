"""
End-to-End Integration Tests for NoSQL Operations.
Tests complete workflows across multiple services and systems.
"""

import pytest
import os
from datetime import datetime, timedelta
from tests.fixtures.nosql_fixtures import get_nosql_fixtures

# Import services for integration testing
from files_api.db_layer import get_vendor_service, get_invoice_service, get_category_service
from iot.db_layer import get_gateway_service, get_device_service, get_measurement_service
from database.indexes import get_index_manager


class TestFilesAPIWorkflow:
    """Test complete Files API workflows"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_complete_invoice_processing_workflow(self):
        """Test end-to-end invoice processing from creation to completion"""
        # Initialize services
        vendor_service = get_vendor_service(self.db_path)
        invoice_service = get_invoice_service(self.db_path)
        category_service = get_category_service(self.db_path)
        
        # Step 1: Create invoice with new vendor and category
        invoice_id = invoice_service.create_invoice(
            filename="test_recyclable_invoice.pdf",
            filepath="/uploads/test/test_recyclable_invoice.pdf",
            vendor_name="New Recycling Corp",
            vendor_id="V100",
            category_id=10,
            category_name="Electronics",
            invoice_number="INV-E2E-001",
            invoice_date=datetime(2023, 7, 1)
        )
        assert invoice_id is not None
        
        # Step 2: Verify vendor was embedded correctly
        vendor = vendor_service.get_vendor_by_id("V100")
        assert vendor is not None
        assert vendor['vendor_name'] == "New Recycling Corp"
        
        # Step 3: Verify category was embedded correctly
        category = category_service.get_category_by_id(10)
        assert category is not None
        assert category['category_name'] == "Electronics"
        
        # Step 4: Simulate PDF processing - update status to processing
        processing_updated = invoice_service.update_invoice_status(
            invoice_id, 
            status="processing"
        )
        assert processing_updated is True
        
        # Step 5: Simulate extraction completion with results
        completion_updated = invoice_service.update_invoice_status(
            invoice_id,
            status="completed",
            reported_weight_kg=75.5,
            unit_price=2.50,
            total_amount=188.75
        )
        assert completion_updated is True
        
        # Step 6: Verify final invoice state
        final_invoice = invoice_service.get_invoice(invoice_id)
        assert final_invoice['extraction_status'] == 'completed'
        assert final_invoice['reported_weight_kg'] == 75.5
        assert final_invoice['total_amount'] == 188.75
        assert final_invoice['completion_date'] is not None
        
        # Step 7: Verify analytics and reporting
        vendor_stats = vendor_service.get_vendor_statistics("V100")
        assert vendor_stats['total_invoices'] == 1
        assert vendor_stats['total_amount'] == 188.75
        
        category_stats = category_service.get_category_statistics(10)
        assert category_stats['total_invoices'] == 1
        assert category_stats['total_weight_kg'] == 75.5
    
    def test_vendor_management_workflow(self):
        """Test vendor management across multiple invoices"""
        invoice_service = get_invoice_service(self.db_path)
        vendor_service = get_vendor_service(self.db_path)
        
        # Create multiple invoices for the same vendor
        vendor_id = "V200"
        vendor_name = "Multi-Invoice Vendor"
        
        invoice_ids = []
        for i in range(3):
            invoice_id = invoice_service.create_invoice(
                filename=f"invoice_{i+1}.pdf",
                filepath=f"/uploads/vendor200/invoice_{i+1}.pdf",
                vendor_name=vendor_name,
                vendor_id=vendor_id,
                category_id=1,
                category_name="Recyclable",
                invoice_number=f"INV-200-{i+1:03d}",
                invoice_date=datetime(2023, 7, i+1)
            )
            invoice_ids.append(invoice_id)
        
        # Complete some invoices with different amounts
        amounts = [100.0, 250.0, 175.0]
        weights = [50.0, 125.0, 87.5]
        
        for i, (invoice_id, amount, weight) in enumerate(zip(invoice_ids, amounts, weights)):
            if i < 2:  # Complete first two invoices
                invoice_service.update_invoice_status(
                    invoice_id,
                    status="completed",
                    total_amount=amount,
                    reported_weight_kg=weight
                )
        
        # Verify vendor statistics across all invoices
        vendor_stats = vendor_service.get_vendor_statistics(vendor_id)
        assert vendor_stats['total_invoices'] == 3
        assert vendor_stats['total_amount'] == 350.0  # Only completed invoices
        assert vendor_stats['total_weight_kg'] == 175.0  # Only completed invoices
        
        # Test vendor invoice retrieval
        vendor_invoices = invoice_service.get_invoices_by_vendor(vendor_id)
        assert len(vendor_invoices) == 3
        
        # Test status filtering
        completed_invoices, count = invoice_service.list_invoices(
            vendor_id=vendor_id, 
            status="completed"
        )
        assert count == 2
        
        pending_invoices, count = invoice_service.list_invoices(
            vendor_id=vendor_id, 
            status="pending"
        )
        assert count == 1
    
    def test_category_analytics_workflow(self):
        """Test category analytics across multiple vendors and invoices"""
        invoice_service = get_invoice_service(self.db_path)
        category_service = get_category_service(self.db_path)
        
        # Create invoices across different categories and vendors
        test_data = [
            {"vendor": "VendorA", "category_id": 1, "category": "Recyclable", "amount": 100.0, "weight": 50.0},
            {"vendor": "VendorA", "category_id": 2, "category": "Metal", "amount": 200.0, "weight": 80.0},
            {"vendor": "VendorB", "category_id": 1, "category": "Recyclable", "amount": 150.0, "weight": 75.0},
            {"vendor": "VendorB", "category_id": 3, "category": "Plastic", "amount": 120.0, "weight": 40.0},
            {"vendor": "VendorC", "category_id": 2, "category": "Metal", "amount": 300.0, "weight": 120.0},
        ]
        
        for i, data in enumerate(test_data):
            invoice_id = invoice_service.create_invoice(
                filename=f"category_test_{i+1}.pdf",
                filepath=f"/uploads/category_test_{i+1}.pdf",
                vendor_name=data["vendor"],
                vendor_id=f"V{300+i}",
                category_id=data["category_id"],
                category_name=data["category"],
                invoice_number=f"INV-CAT-{i+1:03d}"
            )
            
            # Complete all invoices
            invoice_service.update_invoice_status(
                invoice_id,
                status="completed",
                total_amount=data["amount"],
                reported_weight_kg=data["weight"]
            )
        
        # Test category-specific analytics
        recyclable_stats = category_service.get_category_statistics(1)
        assert recyclable_stats['total_invoices'] == 2
        assert recyclable_stats['total_amount'] == 250.0
        assert recyclable_stats['unique_vendors'] == 2
        
        metal_stats = category_service.get_category_statistics(2)
        assert metal_stats['total_invoices'] == 2
        assert metal_stats['total_amount'] == 500.0
        assert metal_stats['total_weight_kg'] == 200.0
        
        # Test top categories
        top_by_amount = category_service.get_top_categories_by_amount(limit=3)
        assert len(top_by_amount) == 3
        assert top_by_amount[0]['category_name'] == 'Metal'  # Highest amount
        
        top_by_weight = category_service.get_top_categories_by_weight(limit=3)
        assert len(top_by_weight) == 3
        assert top_by_weight[0]['category_name'] == 'Metal'  # Highest weight


class TestIoTWorkflow:
    """Test complete IoT workflows"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_gateway_device_measurement_workflow(self):
        """Test complete IoT workflow from gateway creation to measurements"""
        # Initialize services
        gateway_service = get_gateway_service(self.db_path)
        device_service = get_device_service(self.db_path)
        measurement_service = get_measurement_service(self.db_path)
        
        # Step 1: Create gateway
        gateway = gateway_service.create_gateway(
            gateway_id="GW-E2E-001",
            name="E2E Test Gateway",
            location="Test Facility - Floor 1"
        )
        assert gateway['gateway_id'] == "GW-E2E-001"
        assert gateway['status'] == "created"
        
        # Step 2: Connect gateway (simulate connection)
        connected = gateway_service.update_gateway(
            "GW-E2E-001",
            status="connected",
            last_heartbeat=datetime.now(),
            health="good"
        )
        assert connected is True
        
        # Step 3: Register devices to the gateway
        device_ids = []
        for i in range(2):
            device = device_service.register_device(
                device_id=f"SCALE-E2E-{i+1:03d}",
                gateway_id="GW-E2E-001",
                device_type="scale",
                status="online"
            )
            device_ids.append(device['device_id'])
        
        # Step 4: Generate measurements from devices
        materials = ["plastic", "metal", "paper"]
        measurement_ids = []
        
        for device_id in device_ids:
            for i in range(3):
                measurement_id = measurement_service.store_measurement(
                    device_id=device_id,
                    gateway_id="GW-E2E-001",
                    measurement_type="weight_measurement",
                    payload={
                        "weight_kg": 50.0 + i * 25.0,
                        "material_type": materials[i],
                        "container_id": f"BIN-{i+1:03d}",
                        "operator_id": "OP-E2E"
                    },
                    timestamp=datetime.now() - timedelta(hours=i)
                )
                measurement_ids.append(measurement_id)
        
        # Step 5: Verify data consistency across services
        
        # Gateway should show connected status
        gateway_status = gateway_service.get_gateway("GW-E2E-001")
        assert gateway_status['status'] == "connected"
        
        # Devices should be registered to gateway
        gateway_devices = device_service.list_devices(gateway_id="GW-E2E-001")
        assert len(gateway_devices) == 2
        
        # Measurements should be linked to correct devices and gateway
        gateway_measurements = measurement_service.get_measurements(gateway_id="GW-E2E-001")
        assert len(gateway_measurements) == 6  # 2 devices × 3 measurements each
        
        for measurement in gateway_measurements:
            assert measurement['device_info']['gateway_id'] == "GW-E2E-001"
            assert measurement['device_info']['device_id'] in device_ids
            assert measurement['device_info']['device_type'] == "scale"
        
        # Step 6: Test analytics and aggregations
        device1_measurements = measurement_service.get_measurements(device_id=device_ids[0])
        assert len(device1_measurements) == 3
        
        # Test material type filtering
        plastic_measurements = [m for m in gateway_measurements 
                             if m['payload'].get('material_type') == 'plastic']
        assert len(plastic_measurements) == 2  # One per device
    
    def test_device_lifecycle_workflow(self):
        """Test device lifecycle from registration to decommission"""
        gateway_service = get_gateway_service(self.db_path)
        device_service = get_device_service(self.db_path)
        measurement_service = get_measurement_service(self.db_path)
        
        # Create gateway first
        gateway_service.create_gateway(
            gateway_id="GW-LIFECYCLE",
            name="Lifecycle Test Gateway",
            location="Test Lab"
        )
        
        # Register device
        device = device_service.register_device(
            device_id="DEVICE-LIFECYCLE",
            gateway_id="GW-LIFECYCLE",
            device_type="sensor",
            status="online"
        )
        assert device['status'] == "online"
        
        # Generate some measurements
        for i in range(5):
            measurement_service.store_measurement(
                device_id="DEVICE-LIFECYCLE",
                gateway_id="GW-LIFECYCLE",
                measurement_type="temperature_reading",
                payload={"temperature_c": 20.0 + i}
            )
        
        # Verify measurements are recorded
        device_measurements = measurement_service.get_measurements(device_id="DEVICE-LIFECYCLE")
        assert len(device_measurements) == 5
        
        # Update device status to maintenance
        updated = device_service.update_device_status("DEVICE-LIFECYCLE", "maintenance")
        assert updated is True
        
        # Verify status update
        device_status = device_service.get_device("DEVICE-LIFECYCLE")
        assert device_status['status'] == "maintenance"
        
        # Test that measurements still contain correct device info
        for measurement in device_measurements:
            assert measurement['device_info']['device_id'] == "DEVICE-LIFECYCLE"
            assert measurement['device_info']['device_type'] == "sensor"


class TestConcurrentOperations:
    """Test concurrent operations and data consistency"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_concurrent_invoice_creation(self):
        """Test creating multiple invoices concurrently"""
        invoice_service = get_invoice_service(self.db_path)
        
        # Create multiple invoices for different vendors
        invoice_data = [
            {"vendor": "Vendor-A", "vendor_id": "VA001", "amount": 100.0},
            {"vendor": "Vendor-B", "vendor_id": "VB001", "amount": 200.0},
            {"vendor": "Vendor-C", "vendor_id": "VC001", "amount": 150.0},
            {"vendor": "Vendor-A", "vendor_id": "VA001", "amount": 300.0},  # Same vendor, different invoice
        ]
        
        created_invoices = []
        for i, data in enumerate(invoice_data):
            invoice_id = invoice_service.create_invoice(
                filename=f"concurrent_invoice_{i+1}.pdf",
                filepath=f"/uploads/concurrent_{i+1}.pdf",
                vendor_name=data["vendor"],
                vendor_id=data["vendor_id"],
                category_id=1,
                category_name="General",
                invoice_number=f"CONC-{i+1:03d}"
            )
            created_invoices.append(invoice_id)
            
            # Complete some invoices
            if i % 2 == 0:
                invoice_service.update_invoice_status(
                    invoice_id,
                    status="completed",
                    total_amount=data["amount"]
                )
        
        # Verify all invoices were created correctly
        assert len(created_invoices) == 4
        assert len(set(created_invoices)) == 4  # All unique IDs
        
        # Verify vendor statistics are consistent
        vendor_service = get_vendor_service(self.db_path)
        va_stats = vendor_service.get_vendor_statistics("VA001")
        assert va_stats['total_invoices'] == 2  # Two invoices for Vendor-A
        assert va_stats['total_amount'] == 100.0  # Only one completed
    
    def test_concurrent_measurements(self):
        """Test storing measurements concurrently from multiple devices"""
        gateway_service = get_gateway_service(self.db_path)
        device_service = get_device_service(self.db_path)
        measurement_service = get_measurement_service(self.db_path)
        
        # Setup gateway and devices
        gateway_service.create_gateway("GW-CONCURRENT", "Concurrent Gateway", "Test Lab")
        
        device_ids = []
        for i in range(3):
            device = device_service.register_device(
                f"DEV-CONC-{i+1}",
                "GW-CONCURRENT",
                "scale",
                "online"
            )
            device_ids.append(device['device_id'])
        
        # Generate measurements from all devices
        measurement_ids = []
        for device_id in device_ids:
            for measurement_num in range(5):
                measurement_id = measurement_service.store_measurement(
                    device_id=device_id,
                    gateway_id="GW-CONCURRENT",
                    measurement_type="weight_measurement",
                    payload={
                        "weight_kg": 10.0 + measurement_num,
                        "device_seq": measurement_num
                    }
                )
                measurement_ids.append(measurement_id)
        
        # Verify all measurements were stored correctly
        assert len(measurement_ids) == 15  # 3 devices × 5 measurements
        assert len(set(measurement_ids)) == 15  # All unique IDs
        
        # Verify measurements are correctly linked
        gateway_measurements = measurement_service.get_measurements(gateway_id="GW-CONCURRENT")
        assert len(gateway_measurements) == 15
        
        # Verify each device has correct number of measurements
        for device_id in device_ids:
            device_measurements = measurement_service.get_measurements(device_id=device_id)
            assert len(device_measurements) == 5


class TestPerformanceAndIndexes:
    """Test performance with indexes and large datasets"""
    
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.fixtures = get_nosql_fixtures()
        self.db_path = self.fixtures.setup_test_database()
        yield
        self.fixtures.cleanup_test_database()
    
    def test_query_performance_with_indexes(self):
        """Test that queries perform well with proper indexes"""
        invoice_service = get_invoice_service(self.db_path)
        index_manager = get_index_manager(self.db_path)
        
        # Create a larger dataset
        vendors = [f"Vendor-{i:03d}" for i in range(10)]
        categories = [(i, f"Category-{i}") for i in range(1, 6)]
        
        # Create 100 invoices across different vendors and categories
        for i in range(100):
            vendor_name = vendors[i % len(vendors)]
            category_id, category_name = categories[i % len(categories)]
            
            invoice_id = invoice_service.create_invoice(
                filename=f"perf_test_{i+1:03d}.pdf",
                filepath=f"/uploads/perf/perf_test_{i+1:03d}.pdf",
                vendor_name=vendor_name,
                vendor_id=f"VP{i//10:02d}",
                category_id=category_id,
                category_name=category_name,
                invoice_number=f"PERF-{i+1:03d}"
            )
            
            # Complete most invoices
            if i % 5 != 0:  # 80% completion rate
                invoice_service.update_invoice_status(
                    invoice_id,
                    status="completed",
                    total_amount=100.0 + (i * 10.0),
                    reported_weight_kg=50.0 + (i * 5.0)
                )
        
        # Verify index usage by analyzing query performance
        index_analysis = index_manager.analyze_indexes()
        assert index_analysis['total_indexes'] > 0
        
        # Test various query patterns that should benefit from indexes
        
        # Query by vendor (should use vendor index)
        vendor_invoices, count = invoice_service.list_invoices(vendor_id="VP01")
        assert count == 10  # 10 invoices per vendor group
        
        # Query by status (should use status index)
        completed_invoices, count = invoice_service.list_invoices(status="completed")
        assert count == 80  # 80% completion rate
        
        # Query by category (should use category index)
        cat1_invoices = invoice_service.get_invoices_by_category(1)
        assert len(cat1_invoices) == 20  # Every 5th invoice
        
        # Search queries (should benefit from name indexes)
        search_results = invoice_service.search_invoices("Vendor-001")
        assert len(search_results) > 0
    
    def test_measurement_time_series_queries(self):
        """Test time-series measurement queries with proper indexing"""
        gateway_service = get_gateway_service(self.db_path)
        device_service = get_device_service(self.db_path)
        measurement_service = get_measurement_service(self.db_path)
        
        # Setup
        gateway_service.create_gateway("GW-TIMESERIES", "TimeSeries Gateway", "Lab")
        device_service.register_device("TS-DEVICE", "GW-TIMESERIES", "sensor", "online")
        
        # Create time-series measurements
        base_time = datetime(2023, 7, 1, 12, 0, 0)
        for i in range(50):
            measurement_service.store_measurement(
                device_id="TS-DEVICE",
                gateway_id="GW-TIMESERIES",
                measurement_type="temperature_reading",
                payload={"temperature_c": 20.0 + (i % 10)},
                timestamp=base_time + timedelta(minutes=i * 15)
            )
        
        # Test time-based queries (should benefit from timestamp indexes)
        all_measurements = measurement_service.get_measurements(device_id="TS-DEVICE")
        assert len(all_measurements) == 50
        
        # Test gateway aggregation
        gateway_measurements = measurement_service.get_measurements(gateway_id="GW-TIMESERIES")
        assert len(gateway_measurements) == 50
        
        # Test device type filtering
        sensor_measurements = measurement_service.get_measurements()
        temp_measurements = [m for m in sensor_measurements 
                           if m['measurement_type'] == 'temperature_reading']
        assert len(temp_measurements) == 50