"""
E2E test for the complete IoT gateway lifecycle.
This test covers adding a gateway, generating certificates, injecting them into a container,
and verifying the gateway connects successfully.
"""
import pytest
import subprocess
from pathlib import Path
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

def get_gateway_simulator_image():
    """Dynamically find the gateway-simulator image name."""
    result = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], 
                           capture_output=True, text=True, check=False)
    images = result.stdout.splitlines()
    print(f"Available images: {images}")
    for image in images:
        if "gateway-simulator" in image:  # Match any image containing "gateway-simulator"
            print(f"Found gateway image: {image}")
            return image
    raise Exception("No gateway-simulator image found. Ensure it’s built in the workflow.")

def test_add_gateway_and_verify_connection(iot_page, iot_api, gateway_utils):
    page = iot_page
    
    print("\n=== Adding new gateway ===")
    add_button = page.locator("button", has_text="Add Gateway")
    add_button.click()
    page.wait_for_selector("text=Add New Gateway", timeout=5000)
    page.fill('input[name="name"]', TEST_GATEWAY_NAME)
    page.fill('input[name="location"]', TEST_GATEWAY_LOCATION)
    submit_button = page.locator("button[type='submit']")
    submit_button.click()
    page.wait_for_selector("text=Gateway created successfully", timeout=10000)
    
    print("\n=== Getting gateway ID ===")
    gateways = iot_api.get_gateways()
    target_gateway = next((g for g in gateways if g.get("name") == TEST_GATEWAY_NAME), None)
    assert target_gateway is not None, f"Gateway '{TEST_GATEWAY_NAME}' not found"
    gateway_id = target_gateway.get("gateway_id") or target_gateway.get("id")
    print(f"Found gateway with ID: {gateway_id}")
    
    print("\n=== Generating certificates ===")
    gateway_utils.generate_certificate(gateway_id)
    
    print("\n=== Starting gateway container ===")
    cert_path = Path("certs") / gateway_id / "cert.pem"
    key_path = Path("certs") / gateway_id / "key.pem"
    
    # Debug: Check prerequisites
    print("Docker images:")
    images_result = subprocess.run(["docker", "images"], capture_output=True, text=True, check=False)
    print(f"Output: {images_result.stdout}")
    print(f"Errors (if any): {images_result.stderr}")
    
    print("Docker networks:")
    networks_result = subprocess.run(["docker", "network", "ls"], capture_output=True, text=True, check=False)
    print(f"Output: {networks_result.stdout}")
    print(f"Errors (if any): {networks_result.stderr}")
    
    print("Existing containers:")
    containers_result = subprocess.run(["docker", "ps", "-a"], capture_output=True, text=True, check=False)
    print(f"Output: {containers_result.stdout}")
    print(f"Errors (if any): {containers_result.stderr}")
    
    print(f"Cert files exist: {cert_path.exists()} {key_path.exists()}")
    
    # Clean up any existing container
    subprocess.run(["docker", "rm", "-f", gateway_id], check=False)
    
    # Get the correct image name
    image_name = get_gateway_simulator_image()
    
    docker_cmd = [
        "docker", "run", "-d", "--name", gateway_id, "--network", "iot-network",
        "-v", f"{cert_path.absolute()}:/app/certs/cert.pem",
        "-v", f"{key_path.absolute()}:/app/certs/key.pem",
        "-e", f"GATEWAY_ID={gateway_id}", "-e", "MQTT_BROKER=mqtt-broker",
        image_name
    ]
    print(f"Running command: {' '.join(docker_cmd)}")
    try:
        result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
        print(f"Container started: {result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"Docker run failed with exit code {e.returncode}")
        print(f"Output: {e.stdout}")
        print(f"Error: {e.stderr}")
        raise Exception(f"Failed to start gateway container: {e.stderr or str(e)}")
    
    print("\n=== Waiting for gateway to connect ===")
    connected = iot_api.wait_for_gateway_status(gateway_id, "connected", timeout=MAX_WAIT_TIME)
    assert connected, f"Gateway did not connect within {MAX_WAIT_TIME} seconds"
    
    print("\n=== Verifying UI ===")
    page.reload()
    page.wait_for_selector("table tbody tr", timeout=5000)
    gateway_row = page.locator("table tbody tr", has=page.locator(f"text={TEST_GATEWAY_NAME}"))
    status_cell = gateway_row.locator("td:nth-child(6)")
    status_badge = status_cell.locator("span")
    expect(status_badge).to_contain_text("Connected")
    
    print(f"\n=== Test completed: Gateway {gateway_id} connected ===")


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