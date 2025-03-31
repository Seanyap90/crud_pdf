"""
IoT Gateway E2E testing fixtures optimized for direct execution in VS Code.
These fixtures handle all necessary setup and teardown automatically.
"""
import pytest
import subprocess
import time
import requests
import os
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

# Configuration
FRONTEND_URL = "http://localhost:3000"
BACKEND_URL = "http://localhost:8000"
BACKEND_STARTUP_TIMEOUT = 60  # seconds
FRONTEND_STARTUP_TIMEOUT = 30  # seconds

# Store process handles for cleanup
BACKEND_PROCESS = None
FRONTEND_PROCESS = None

@pytest.fixture(scope="session")
def iot_backend():
    """
    Start backend services and cleanup after tests.
    This fixture handles starting the IoT backend directly without external scripts.
    """
    global BACKEND_PROCESS
    print("\n=== Starting IoT backend services ===")
    
    # Start MQTT broker using docker-compose
    try:
        # Find the src/iot directory
        repo_root = Path(__file__).parent.parent.parent  # From tests/fixtures/iot_fixtures.py
        iot_dir = repo_root / "src" / "iot"
        
        if not iot_dir.exists():
            print(f"Warning: IoT directory not found at {iot_dir}")
            # Try alternative paths that might exist in the project structure
            alt_paths = [
                repo_root / "iot",
                repo_root / "src/backend/iot",
                repo_root
            ]
            for path in alt_paths:
                if path.exists():
                    iot_dir = path
                    print(f"Using alternative IoT directory: {iot_dir}")
                    break
        
        # Start MQTT broker
        print("Starting MQTT broker...")
        subprocess.run(
            ["docker-compose", "up", "-d", "mqtt-broker"],
            cwd=str(iot_dir),
            check=True
        )
        print("MQTT broker started successfully")
        
        # Start the backend API
        print("Starting FastAPI backend...")
        fastapi_startup_cmd = ["python", "-m", "src.iot.cli", "start", "--mode", "local", "--docker-network", "iot-network"]
        
        # Check if the module exists, if not, try alternative module paths
        try:
            __import__("src.iot.cli")
        except ImportError:
            # Try alternative module paths
            alternative_cmds = [
                ["python", "-m", "iot.cli", "start", "--mode", "local", "--docker-network", "iot-network"],
                ["python", "-m", "backend.iot.cli", "start", "--mode", "local", "--docker-network", "iot-network"],
                ["python", "-m", "iot.main", "--mode", "local", "--docker-network", "iot-network"]
            ]
            
            for cmd in alternative_cmds:
                try:
                    # Just test if the module can be imported
                    __import__(cmd[1].replace("-", "."))
                    fastapi_startup_cmd = cmd
                    print(f"Using alternative backend startup command: {' '.join(cmd)}")
                    break
                except ImportError:
                    continue
        
        BACKEND_PROCESS = subprocess.Popen(
            fastapi_startup_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for backend to be ready
        wait_for_backend(BACKEND_STARTUP_TIMEOUT)
    except Exception as e:
        print(f"Error starting backend: {str(e)}")
        cleanup_backend()
        raise
    
    yield
    
    # Cleanup after tests
    cleanup_backend()


def cleanup_backend():
    """Helper function to clean up backend resources"""
    global BACKEND_PROCESS
    
    print("\n=== Cleaning up IoT resources ===")
    
    # Stop the FastAPI backend
    if BACKEND_PROCESS:
        print("Stopping FastAPI backend...")
        BACKEND_PROCESS.terminate()
        try:
            BACKEND_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            BACKEND_PROCESS.kill()
        BACKEND_PROCESS = None
    
    # Stop and remove Docker containers
    print("Stopping Docker containers...")
    try:
        # Stop MQTT broker and other containers
        repo_root = Path(__file__).parent.parent.parent
        iot_dir = repo_root / "src" / "iot"
        
        if iot_dir.exists():
            subprocess.run(
                ["docker-compose", "down"],
                cwd=str(iot_dir),
                check=False
            )
        
        # Remove any gateway containers
        subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=gateway-", "-q"],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
            stderr=subprocess.PIPE
        ).stdout.strip().split('\n')
        
        # If any containers were found, remove them
        containers = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=gateway-", "-q"],
            check=False,
            stdout=subprocess.PIPE,
            text=True
        ).stdout.strip()
        
        if containers:
            subprocess.run(
                ["docker", "rm", "-f"] + containers.split('\n'),
                check=False
            )
    except Exception as e:
        print(f"Error during Docker cleanup: {str(e)}")
    
    print("Backend cleanup completed")


def wait_for_backend(timeout=BACKEND_STARTUP_TIMEOUT):
    """
    Helper function to wait for backend API to be available.
    """
    print(f"Waiting for backend API at {BACKEND_URL}/health (timeout: {timeout}s)...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{BACKEND_URL}/health")
            if response.status_code == 200:
                print("Backend API is ready!")
                return True
        except requests.exceptions.ConnectionError:
            pass
        
        print(f"Backend not ready, retrying... ({int(timeout - (time.time() - start_time))}s left)")
        time.sleep(3)
    
    raise Exception(f"Failed to connect to backend after {timeout} seconds")


@pytest.fixture(scope="session")
def iot_frontend():
    """
    Fixture to ensure frontend server is running.
    This starts the frontend if it's not already running.
    """
    global FRONTEND_PROCESS
    
    # Check if frontend is already running
    try:
        response = requests.get(FRONTEND_URL)
        if response.status_code == 200:
            print("Frontend server already running")
            yield
            return
    except requests.exceptions.ConnectionError:
        print("Frontend not running, will start it")
    
    # Start frontend server
    print("Starting frontend server...")
    
    try:
        # Find the frontend directory
        repo_root = Path(__file__).parent.parent.parent
        frontend_dir = repo_root / "frontend"
        
        if not frontend_dir.exists():
            # Try to find frontend directory in common locations
            alt_paths = [
                repo_root / "client",
                repo_root / "web",
                repo_root / "ui",
                repo_root / "src" / "frontend",
                repo_root / "src" / "client"
            ]
            
            for path in alt_paths:
                if path.exists() and (path / "package.json").exists():
                    frontend_dir = path
                    print(f"Using alternative frontend directory: {frontend_dir}")
                    break
        
        if not frontend_dir.exists():
            print(f"Warning: Frontend directory not found, tests requiring UI will be skipped")
            yield
            return
        
        # Check if node_modules exists, if not, run npm install
        if not (frontend_dir / "node_modules").exists():
            print("Installing frontend dependencies...")
            subprocess.run(
                ["npm", "install"],
                cwd=str(frontend_dir),
                check=True
            )
        
        # Start the frontend server
        print(f"Starting frontend server from {frontend_dir}...")
        FRONTEND_PROCESS = subprocess.Popen(
            ["npm", "start"],
            cwd=str(frontend_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for frontend to be ready
        wait_for_frontend(FRONTEND_STARTUP_TIMEOUT)
    except Exception as e:
        print(f"Error starting frontend: {str(e)}")
    
    yield
    
    # Cleanup
    cleanup_frontend()


def cleanup_frontend():
    """Helper function to clean up frontend resources"""
    global FRONTEND_PROCESS
    
    if FRONTEND_PROCESS:
        print("Stopping frontend server...")
        FRONTEND_PROCESS.terminate()
        try:
            FRONTEND_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            FRONTEND_PROCESS.kill()
        FRONTEND_PROCESS = None


def wait_for_frontend(timeout=FRONTEND_STARTUP_TIMEOUT):
    """Helper function to wait for frontend to be available"""
    print(f"Waiting for frontend at {FRONTEND_URL} (timeout: {timeout}s)...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(FRONTEND_URL)
            if response.status_code == 200:
                print(f"Frontend server is ready at {FRONTEND_URL}!")
                return True
        except requests.exceptions.ConnectionError:
            pass
        
        elapsed = time.time() - start_time
        print(f"Frontend not ready, waiting... ({int(timeout - elapsed)}s left)")
        time.sleep(2)
    
    print(f"WARNING: Frontend did not start within {timeout} seconds")
    return False


@pytest.fixture(scope="session")
def playwright_instance():
    """
    Initialize Playwright for the test session.
    """
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def iot_browser(playwright_instance):
    """
    Initialize Playwright browser for the test session.
    Returns a browser instance that can be used to create pages.
    """
    # For CI environments, use headless=True
    # For local development, headless=False is better for debugging
    headless = os.environ.get("HEADLESS", "0") == "1"  # Default to visible browser for VS Code testing
    browser = playwright_instance.chromium.launch(headless=headless)
    yield browser
    browser.close()


@pytest.fixture
def iot_page(iot_browser, iot_frontend):
    """
    Create a new page for each test.
    Navigates to the frontend URL and waits for basic loading.
    
    This fixture depends on iot_frontend to ensure the frontend is running.
    """
    page = iot_browser.new_page()
    
    # Retry mechanism for page navigation
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Navigating to {FRONTEND_URL} (attempt {attempt+1}/{max_retries})...")
            page.goto(FRONTEND_URL, timeout=10000)
            
            # Wait for the dashboard to load (adjust selector based on your UI)
            selector_found = page.wait_for_selector("text=IoT Gateway Management", timeout=10000, state="visible")
            if selector_found:
                print("Successfully loaded frontend page")
                break
        except Exception as e:
            print(f"Error navigating to frontend (attempt {attempt+1}): {str(e)}")
            if attempt == max_retries - 1:
                raise
            time.sleep(2)
    
    yield page
    page.close()


@pytest.fixture
def iot_api():
    """
    Create a simple API client for interacting with the IoT backend.
    """
    class IoTApiClient:
        @staticmethod
        def get_gateways():
            response = requests.get(f"{BACKEND_URL}/api/gateways")
            response.raise_for_status()
            return response.json().get("gateways", [])
        
        @staticmethod
        def get_gateway(gateway_id):
            response = requests.get(f"{BACKEND_URL}/api/gateways/{gateway_id}")
            response.raise_for_status()
            return response.json()
        
        @staticmethod
        def create_gateway(name, location):
            data = {"name": name, "location": location}
            response = requests.post(f"{BACKEND_URL}/api/gateways", json=data)
            response.raise_for_status()
            return response.json()
        
        @staticmethod
        def delete_gateway(gateway_id):
            response = requests.delete(f"{BACKEND_URL}/api/gateways/{gateway_id}")
            response.raise_for_status()
            return response.json()
        
        @staticmethod
        def wait_for_gateway_status(gateway_id, target_status, timeout=60):
            """Wait for a gateway to reach a specific status"""
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                try:
                    response = requests.get(f"{BACKEND_URL}/api/gateways/{gateway_id}")
                    if response.status_code == 200:
                        gateway_data = response.json()
                        current_status = gateway_data.get("status")
                        print(f"Current gateway status: {current_status}")
                        
                        if current_status == target_status:
                            return True
                except requests.exceptions.RequestException as e:
                    print(f"Error checking gateway status: {e}")
                
                time.sleep(2)
            
            return False

    return IoTApiClient()


@pytest.fixture
def gateway_utils():
    """
    Utilities for gateway management in tests.
    All functionality is implemented directly, without relying on external scripts.
    """
    class GatewayUtils:
        @staticmethod
        def generate_certificate(gateway_id):
            """Generate certificate for a gateway"""
            try:
                print(f"Generating certificate for gateway {gateway_id}...")
                
                # Create certificates directory
                certs_dir = Path("certs") / gateway_id
                certs_dir.mkdir(parents=True, exist_ok=True)
                
                # Generate self-signed certificate
                cert_path = certs_dir / "cert.pem"
                key_path = certs_dir / "key.pem"
                
                cmd = [
                    "openssl", "req", "-x509", 
                    "-newkey", "rsa:2048", 
                    "-keyout", str(key_path),
                    "-out", str(cert_path),
                    "-days", "365",
                    "-nodes",
                    "-subj", f"/CN=gateway-{gateway_id}/O=IoT Gateway Management System"
                ]
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    raise Exception(f"Certificate generation failed: {result.stderr}")
                
                # Set permissions
                os.chmod(cert_path, 0o644)
                os.chmod(key_path, 0o600)
                
                print(f"Certificate generated successfully at {cert_path}")
                return True
            except Exception as e:
                print(f"Error generating certificate: {str(e)}")
                raise
        
        @staticmethod
        def inject_certificate(gateway_id):
            """Inject certificate into a gateway container"""
            try:
                print(f"Injecting certificate into gateway container for {gateway_id}...")
                
                # Check if container exists
                container_name = f"gateway-{gateway_id}"
                result = subprocess.run(
                    ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                    capture_output=True,
                    text=True
                )
                
                if container_name not in result.stdout:
                    raise Exception(f"Container {container_name} not found")
                
                # Check if certificates exist
                cert_path = Path("certs") / gateway_id / "cert.pem"
                key_path = Path("certs") / gateway_id / "key.pem"
                
                if not cert_path.exists() or not key_path.exists():
                    raise Exception(f"Certificate files not found: {cert_path}, {key_path}")
                
                # Check if container is running, start if not
                is_running = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
                    capture_output=True,
                    text=True
                ).stdout.strip()
                
                if is_running != "true":
                    print(f"Container {container_name} is not running, starting...")
                    subprocess.run(["docker", "start", container_name], check=True)
                    time.sleep(2)  # Give container time to start
                
                # Copy certificates to container
                subprocess.run(
                    ["docker", "cp", str(cert_path), f"{container_name}:/app/certs/cert.pem"],
                    check=True
                )
                
                subprocess.run(
                    ["docker", "cp", str(key_path), f"{container_name}:/app/certs/key.pem"],
                    check=True
                )
                
                print(f"Certificates successfully injected into container {container_name}")
                return True
            except Exception as e:
                print(f"Error injecting certificate: {str(e)}")
                raise
    
    return GatewayUtils()