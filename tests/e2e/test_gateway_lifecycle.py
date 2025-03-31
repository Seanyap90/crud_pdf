"""
E2E test for the complete IoT gateway lifecycle.
This test covers adding a gateway, generating certificates, injecting them into a container,
and verifying the gateway connects successfully.
"""
import pytest
import time
import os
from playwright.sync_api import expect

# Test configuration
TEST_GATEWAY_NAME = "E2E Test Gateway"
TEST_GATEWAY_LOCATION = "E2E Testing Environment"
MAX_WAIT_TIME = 60  # seconds to wait for gateway to connect
MAX_RETRIES = 5
RETRY_DELAY = 5

# Mark these tests as requiring the IoT environment setup
pytestmark = [pytest.mark.e2e, pytest.mark.iot, pytest.mark.usefixtures("iot_backend")]

def test_add_gateway_and_verify_connection(iot_page, iot_api, gateway_utils):
    """Test the full gateway lifecycle: add, generate certificate, inject, verify connection"""
    page = iot_page  # alias for clarity
    
    # Step 1: Add a new gateway through the UI
    print("\n=== Adding new gateway ===")
    
    # Click the Add Gateway button
    add_button = page.locator("button", has_text="Add Gateway")
    add_button.click()
    
    # Wait for the form dialog to appear
    page.wait_for_selector("text=Add New Gateway", timeout=5000)
    
    # Fill in the gateway form
    page.fill('input[name="name"]', TEST_GATEWAY_NAME)
    page.fill('input[name="location"]', TEST_GATEWAY_LOCATION)
    
    # Submit the form
    submit_button = page.locator("button[type='submit']")
    submit_button.click()
    
    # Wait for success message
    page.wait_for_selector("text=Gateway created successfully", timeout=10000)
    
    # Step 2: Get the gateway ID from the API
    print("\n=== Getting gateway ID ===")
    gateways = iot_api.get_gateways()
    
    target_gateway = None
    for gateway in gateways:
        if gateway.get("name") == TEST_GATEWAY_NAME:
            target_gateway = gateway
            break
    
    assert target_gateway is not None, f"Gateway '{TEST_GATEWAY_NAME}' not found in API response"
    gateway_id = target_gateway.get("gateway_id") or target_gateway.get("id")
    print(f"Found gateway with ID: {gateway_id}")
    
    # Step 3: Generate certificates
    print("\n=== Generating certificates ===")
    gateway_utils.generate_certificate(gateway_id)
    
    # Step 4: Inject certificates
    print("\n=== Injecting certificates ===")
    for attempt in range(MAX_RETRIES):
        try:
            gateway_utils.inject_certificate(gateway_id)
            break
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise
    
    # Step 5: Wait for gateway to connect and verify status
    print("\n=== Waiting for gateway to connect ===")
    
    # Verify connection via API
    connected = iot_api.wait_for_gateway_status(gateway_id, "connected", timeout=MAX_WAIT_TIME)
    assert connected, f"Gateway did not connect within {MAX_WAIT_TIME} seconds"
    
    # Step 6: Verify status in UI after refreshing
    page.reload()
    
    # Wait for the gateway table to load
    page.wait_for_selector("table tbody tr", timeout=5000)
    
    # Find the row with our gateway name
    gateway_row = page.locator("table tbody tr", has=page.locator(f"text={TEST_GATEWAY_NAME}"))
    
    # Check the status column (6th column is status based on your gateway-table.tsx file)
    status_cell = gateway_row.locator("td:nth-child(6)")
    status_badge = status_cell.locator("span")
    
    # Verify status is "Connected"
    expect(status_badge).to_contain_text("Connected")
    
    print(f"\n=== Test completed successfully: Gateway {gateway_id} is connected ===")


# def test_gateway_details_display(iot_page, iot_api, gateway_utils):
#     """Test viewing gateway details after it's connected"""
#     page = iot_page
    
#     # Get the first connected gateway from API
#     gateways = iot_api.get_gateways()
#     connected_gateway = next((g for g in gateways if g.get("status") == "connected"), None)
    
#     # Skip test if no connected gateway exists
#     if not connected_gateway:
#         pytest.skip("No connected gateway found to test details view")
    
#     gateway_id = connected_gateway.get("gateway_id") or connected_gateway.get("id")
#     gateway_name = connected_gateway.get("name")
    
#     # Navigate to gateway table
#     page.goto("http://localhost:3000")
#     page.wait_for_selector("table tbody tr", timeout=5000)
    
#     # Find and click the view details button in the gateway's row
#     gateway_row = page.locator("table tbody tr", has=page.locator(f"text={gateway_name}"))
#     details_button = gateway_row.locator('button[title="View Details"]')
#     details_button.click()
    
#     # Wait for details dialog to appear
#     page.wait_for_selector("text=Gateway Details", timeout=5000)
    
#     # Check that important elements are displayed
#     assert page.locator("text=Basic Information").is_visible()
#     assert page.locator("text=Certificate Status").is_visible()
    
#     # Verify gateway ID is shown
#     id_text = page.locator("text=ID:").first.text_content()
#     assert gateway_id in id_text, f"Gateway ID {gateway_id} not found in details"
    
#     # Verify certificate status shows "Installed"
#     cert_section = page.locator("text=Certificate Status").locator("xpath=../..")
#     assert cert_section.locator("text=Installed").is_visible()
    
#     # Close the dialog
#     close_button = page.locator("button", has_text="Close")
#     close_button.click()