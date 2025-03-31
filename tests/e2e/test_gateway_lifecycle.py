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
import re
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
    result = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], 
                           capture_output=True, text=True, check=False)
    images = result.stdout.splitlines()
    print(f"Available images: {images}")
    for image in images:
        if "gateway-simulator" in image:
            print(f"Found gateway image: {image}")
            return image
    raise Exception("No gateway-simulator image found.")

def get_api_url_for_container():
    """Determine the best API URL for the container environment"""
    # Check if we're in GitHub Actions
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return "http://172.17.0.1:8000"  # GitHub Actions: use Docker bridge
    
    # Try to detect if host.docker.internal works on this system
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "busybox", "ping", "-c", "1", "-W", "1", "host.docker.internal"],
            capture_output=True, check=False
        )
        if result.returncode == 0:
            return "http://host.docker.internal:8000"  # host.docker.internal is accessible
    except Exception:
        pass
    
    # Default fallback
    return "http://172.17.0.1:8000"

def get_mqtt_broker_address():
    """Discover the MQTT broker address with Docker Desktop priority"""
    # Check if we're in Docker Desktop/WSL environment
    is_docker_desktop = False
    
    # Check for WSL
    if os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        is_docker_desktop = True
        print("WSL environment detected")
    
    # Check if host.docker.internal is pingable
    try:
        ping_result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", "host.docker.internal"],
            capture_output=True, text=True, check=False
        )
        if ping_result.returncode == 0:
            is_docker_desktop = True
            print("host.docker.internal is reachable, Docker Desktop detected")
    except Exception:
        pass
    
    # In Docker Desktop, always use host.docker.internal
    if is_docker_desktop:
        print("Using host.docker.internal:1883 for Docker Desktop environment")
        return "host.docker.internal:1883"
    
    # For non-Docker Desktop, proceed with discovery
    # Try docker inspect to get container IP (most accurate)
    try:
        print("Attempting to get MQTT broker IP via Docker inspect...")
        inspect_result = subprocess.run(
            ["docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "mqtt-broker"],
            capture_output=True, text=True, check=False
        )
        
        if inspect_result.returncode == 0 and inspect_result.stdout.strip():
            ip_address = inspect_result.stdout.strip()
            print(f"Found MQTT broker IP via Docker inspect: {ip_address}")
            return f"{ip_address}:1883"
    except Exception as e:
        print(f"Error inspecting mqtt-broker container: {str(e)}")
    
    # Try to get broker's IP from any container in the network
    try:
        network_info = subprocess.run(
            ["docker", "network", "inspect", "iot-network", "--format", "{{json .Containers}}"],
            capture_output=True, text=True, check=False
        )
        
        if network_info.returncode == 0 and network_info.stdout.strip():
            # Look for containers with 'mqtt' or 'broker' in the name
            import json
            try:
                containers = json.loads(network_info.stdout)
                for container_id, container_info in containers.items():
                    if 'mqtt' in container_info.get('Name', '').lower() or 'broker' in container_info.get('Name', '').lower():
                        ip = container_info.get('IPv4Address', '').split('/')[0]
                        if ip:
                            print(f"Found broker container in network with IP: {ip}")
                            return f"{ip}:1883"
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"Error getting network info: {str(e)}")
    
    # Fallback to service name with no IP
    print("No broker IP found, using service name: mqtt-broker:1883")
    return "mqtt-broker:1883"

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
    
    print("Docker images:")
    images_result = subprocess.run(["docker", "images"], capture_output=True, text=True, check=False)
    print(f"Output: {images_result.stdout}")
    
    print("Docker networks:")
    networks_result = subprocess.run(["docker", "network", "ls"], capture_output=True, text=True, check=False)
    print(f"Output: {networks_result.stdout}")
    
    # Test connectivity within the network
    print("Testing network connectivity:")
    network_test = subprocess.run(["docker", "run", "--rm", "--network", "iot-network", "busybox", "ping", "-c", "3", "mqtt-broker"], 
                                capture_output=True, text=True, check=False)
    print(f"Network test output: {network_test.stdout}")
    print(f"Network test errors: {network_test.stderr}")
    
    print("Existing containers:")
    containers_result = subprocess.run(["docker", "ps", "-a"], capture_output=True, text=True, check=False)
    print(f"Output: {containers_result.stdout}")
    
    print(f"Cert files exist: {cert_path.exists()} {key_path.exists()}")
    
    subprocess.run(["docker", "rm", "-f", gateway_id], check=False)
    image_name = get_gateway_simulator_image()
    
    # Use mqtt-broker directly and DON'T override MQTT_BROKER_ADDRESS
    docker_cmd = [
        "docker", "run", "-d", "--name", gateway_id, "--network", "iot-network",
        "-v", f"{cert_path.absolute()}:/app/certs/cert.pem",
        "-v", f"{key_path.absolute()}:/app/certs/key.pem",
        "-e", f"GATEWAY_ID={gateway_id}",
        "-e", f"MQTT_BROKER_ADDRESS={get_mqtt_broker_address()}",  # Set directly to mqtt-broker
        "-e", f"API_URL={get_api_url_for_container()}",  # Keep this for API connectivity
    ]
    if subprocess.run(["docker", "network", "inspect", "iot-network"], capture_output=True).returncode == 0:
        docker_cmd.append(image_name)
    else:
        raise Exception("iot-network not found. Ensure it's created in the workflow.")
    
    print(f"Running command: {' '.join(docker_cmd)}")
    try:
        result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
        print(f"Container started: {result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"Docker run failed with exit code {e.returncode}")
        print(f"Output: {e.stdout}")
        print(f"Error: {e.stderr}")
        raise Exception(f"Failed to start gateway container: {e.stderr or str(e)}")
    
    time.sleep(5)
    print("Gateway container status:")
    status_result = subprocess.run(["docker", "ps", "-a", "--filter", f"name={gateway_id}"], 
                                   capture_output=True, text=True, check=False)
    print(f"Output: {status_result.stdout}")
    
    print("Gateway container logs:")
    logs_result = subprocess.run(["docker", "logs", gateway_id], 
                                 capture_output=True, text=True, check=False)
    print(f"Output: {logs_result.stdout}")
    print(f"Errors (if any): {logs_result.stderr}")
    
    print("Testing connectivity to mqtt-broker:")
    ping_result = subprocess.run(["docker", "exec", gateway_id, "ping", "-c", "4", "mqtt-broker"], 
                                 capture_output=True, text=True, check=False)
    print(f"Output: {ping_result.stdout}")
    print(f"Errors (if any): {ping_result.stderr}")
    
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