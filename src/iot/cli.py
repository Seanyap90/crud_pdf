import click
import asyncio
import uvicorn
import logging
import requests
import socket
import os
import platform
from enum import Enum
from typing import Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WorkerMode(str, Enum):
    LOCAL = "local"
    MOCK_AWS = "mock_aws"
    AWS = "aws"

class EnvironmentType(str, Enum):
    DOCKER_DESKTOP = "docker_desktop"  # WSL or Docker Desktop on Windows/Mac
    GITHUB_ACTIONS = "github_actions"  # GitHub Actions environment
    STANDARD_LINUX = "standard_linux"  # Standard Linux with Docker

@click.group()
def cli():
    """IoT Gateway Management CLI"""
    pass

@cli.command()
@click.option('--mode', 
              type=click.Choice([mode.value for mode in WorkerMode]), 
              default=WorkerMode.LOCAL.value,
              help='Worker mode to use (local, mock_aws, or aws)')
@click.option('--host', default='0.0.0.0', help='API host address')
@click.option('--port', default=8000, type=int, help='API port')
@click.option('--aws-region', default='us-east-1', help='AWS region (for AWS mode)')
@click.option('--reload/--no-reload', default=True, help='Enable/disable auto-reload for development')
@click.option('--docker-network', default='iot-network', help='Docker network for gateway containers')
@click.option('--mqtt-broker', default='mqtt-broker', help='MQTT broker hostname or container name')
@click.option('--heartbeat-interval', default=30, type=int, help='Heartbeat interval in seconds')
@click.option('--heartbeat-miss-threshold', default=3, type=int, help='Number of missed heartbeats before disconnect')
@click.option('--force-environment', 
              type=click.Choice([env.value for env in EnvironmentType]),
              default=None,
              help='Force specific environment type (overrides auto-detection)')
@click.option('--rules-engine/--no-rules-engine', default=True, help='Enable/disable rules engine integration')
def start(mode: str, host: str, port: int, aws_region: str, reload: bool, 
          docker_network: str, mqtt_broker: str, heartbeat_interval: int,
          heartbeat_miss_threshold: int, force_environment: Optional[str] = None,
          rules_engine: bool = True):
    """Start the IoT Gateway Management service with the specified worker mode"""
    # Import here to avoid circular imports
    from iot.worker.aws_worker import AWSWorker
    from iot.worker.local_worker import LocalWorker
    from iot.worker.mock_aws_worker import MockAWSWorker
    from iot.main import create_app
    from iot.config import update_settings
    
    # Update configuration settings
    settings_update = {
        "mode": mode,
        "host": host,
        "port": port, 
        "aws_region": aws_region,
        "docker_network": docker_network,
        "mqtt_broker": mqtt_broker,
        "heartbeat_interval": heartbeat_interval,
        "heartbeat_miss_threshold": heartbeat_miss_threshold,
        "rules_engine_enabled": rules_engine
    }
    
    # Override environment type if specified
    if force_environment:
        settings_update["environment_type"] = EnvironmentType(force_environment)
        logger.info(f"Forcing environment type: {force_environment}")
    
    # Apply settings updates
    update_settings(**settings_update)
    
    # Setup Docker network with error handling
    setup_docker_network(docker_network, mqtt_broker)
    
    # Get the appropriate worker based on mode
    worker_mode = WorkerMode(mode)
    logger.info(f"Starting service with {worker_mode.value} worker")
    
    if worker_mode == WorkerMode.AWS:
        worker = AWSWorker(aws_region)
    elif worker_mode == WorkerMode.MOCK_AWS:
        worker = MockAWSWorker()
    else:  # default to local
        worker = LocalWorker()
    
    # Create the FastAPI app with the selected worker
    app = create_app(worker)
    
    # Configure and start the uvicorn server
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        reload=reload
    )
    
    server = uvicorn.Server(config)
    asyncio.run(server.serve())

def setup_docker_network(network_name: str, mqtt_broker: str) -> bool:
    """Ensure Docker network exists and MQTT broker is connected to it"""
    try:
        import docker
        client = docker.from_env()
        
        # Check if network exists
        networks = client.networks.list(names=[network_name])
        
        if not networks:
            logger.info(f"Creating Docker network: {network_name}")
            client.networks.create(network_name, driver="bridge")
            logger.info(f"Created Docker network: {network_name}")
        else:
            logger.info(f"Docker network {network_name} already exists")
        
        # Check if MQTT broker is running
        try:
            broker = client.containers.get(mqtt_broker)
            logger.info(f"MQTT broker container '{mqtt_broker}' found")
            
            # Check if it's connected to our network
            broker_networks = [n for n in broker.attrs.get('NetworkSettings', {}).get('Networks', {}).keys()]
            
            if network_name not in broker_networks:
                logger.info(f"Connecting MQTT broker to {network_name}")
                network = client.networks.get(network_name)
                network.connect(mqtt_broker)
                logger.info(f"MQTT broker successfully connected to {network_name}")
        except docker.errors.NotFound:
            logger.warning(f"MQTT broker container '{mqtt_broker}' not found, make sure it's running")
        
        return True
    except Exception as e:
        logger.error(f"Error with Docker network setup: {str(e)}")
        return False

def detect_environment() -> EnvironmentType:
    """Detect the current environment type"""
    # Default to standard Linux
    detected_env = EnvironmentType.STANDARD_LINUX
    
    # Check for GitHub Actions
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        detected_env = EnvironmentType.GITHUB_ACTIONS
        logger.info("GitHub Actions environment detected")
    else:
        # Check for WSL/Docker Desktop
        is_wsl = False
        if platform.system() == "Linux" and os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
            is_wsl = True
            logger.info("WSL environment detected")
        
        # Check if host.docker.internal is resolvable
        try:
            socket.gethostbyname("host.docker.internal")
            detected_env = EnvironmentType.DOCKER_DESKTOP
            logger.info("Docker Desktop environment detected (host.docker.internal is resolvable)")
        except socket.gaierror:
            # Only if we're in WSL but host.docker.internal isn't resolvable, still consider it Docker Desktop
            if is_wsl:
                detected_env = EnvironmentType.DOCKER_DESKTOP
                logger.info("WSL environment without host.docker.internal resolution, still using Docker Desktop settings")
    
    logger.info(f"Environment detected as: {detected_env}")
    return detected_env

@cli.command()
@click.argument('gateway_id')
def test_gateway(gateway_id: str):
    """Send a test message to a specific gateway"""
    try:
        # Check gateway status
        resp = requests.get(f"http://localhost:8000/api/gateways/{gateway_id}")
        if resp.status_code == 200:
            click.echo(f"Gateway status: {resp.json()}")
            
            # Simulate an MQTT heartbeat event
            mqtt_event = {
                "gateway_id": gateway_id,
                "event_type": "heartbeat",
                "payload": {"source": "cli-test", "uptime": "3600s"}
            }
            
            resp = requests.post("http://localhost:8000/api/mqtt/events", json=mqtt_event)
            if resp.status_code == 200:
                click.echo("Sent test MQTT heartbeat")
                click.echo(f"Updated status: {resp.json()['gateway']}")
            else:
                click.echo(f"Failed to send MQTT event: {resp.text}")
        else:
            click.echo(f"Failed to get gateway status: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.option('--include-deleted/--exclude-deleted', default=False, 
              help='Include deleted gateways in the listing')
def list_gateways(include_deleted: bool):
    """List all registered gateways"""
    try:
        resp = requests.get(f"http://localhost:8000/api/gateways?include_deleted={str(include_deleted).lower()}")
        if resp.status_code == 200:
            gateways = resp.json().get("gateways", [])
            if not gateways:
                click.echo("No gateways found")
            else:
                click.echo(f"Found {len(gateways)} gateways:")
                for gw in gateways:
                    # Format status with color
                    status = gw.get('status', 'unknown')
                    if status == 'connected':
                        status_str = click.style(status, fg='green')
                    elif status == 'disconnected':
                        status_str = click.style(status, fg='yellow')
                    elif status == 'deleted':
                        status_str = click.style(status, fg='red')
                    else:  # created or unknown
                        status_str = click.style(status, fg='blue')
                    
                    # Format output
                    click.echo(f"  - {gw['gateway_id']}: {gw.get('name', 'Unnamed')} ({status_str})")
                    
                    # Show certificate info if available
                    cert_info = gw.get('certificate_info')
                    if cert_info:
                        cert_status = cert_info.get('status', 'unknown')
                        if cert_status == 'installed':
                            click.echo(f"    Certificate: {click.style('Installed', fg='green')}")
                        else:
                            click.echo(f"    Certificate: {click.style('Not installed', fg='yellow')}")
        else:
            click.echo(f"Failed to list gateways: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
@click.option('--name', required=True, help='Name for the gateway')
@click.option('--location', required=True, help='Location for the gateway')
def create_gateway(gateway_id: str, name: str, location: str):
    """Create a new gateway"""
    try:
        create_data = {
            "name": name,
            "location": location,
            "gateway_id": gateway_id
        }
        
        resp = requests.post("http://localhost:8000/api/gateways", json=create_data)
        if resp.status_code == 201:
            gateway = resp.json()
            click.echo(f"Created gateway: {gateway['gateway_id']}")
            click.echo(f"  Name: {gateway['name']}")
            click.echo(f"  Location: {gateway['location']}")
            click.echo(f"  Status: {click.style(gateway['status'], fg='blue')}")
        else:
            click.echo(f"Failed to create gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def delete_gateway(gateway_id: str):
    """Delete a gateway permanently"""
    try:
        resp = requests.delete(f"http://localhost:8000/api/gateways/{gateway_id}")
        if resp.status_code == 200:
            click.echo(f"Successfully deleted gateway {gateway_id}")
            click.echo(f"Gateway status: {resp.json()['gateway']}")
        else:
            click.echo(f"Failed to delete gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
@click.option('--installed/--removed', default=True, help='Certificate status (installed/removed)')
def set_certificate_status(gateway_id: str, installed: bool):
    """Set certificate status for a gateway"""
    try:
        resp = requests.post(
            f"http://localhost:8000/api/gateways/{gateway_id}/certificate?status={str(installed).lower()}"
        )
        if resp.status_code == 200:
            status = "installed" if installed else "removed"
            click.echo(f"Successfully set certificate status to {status} for gateway {gateway_id}")
            click.echo(f"Gateway status: {resp.json()['gateway']}")
        else:
            click.echo(f"Failed to set certificate status: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def connect_gateway(gateway_id: str):
    """Connect a gateway to the MQTT broker"""
    try:
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/connect")
        if resp.status_code == 200:
            click.echo(f"Successfully connected gateway {gateway_id}")
            
            gateway = resp.json()['gateway']
            status = gateway.get('status', 'unknown')
            
            # Check if we actually connected (depends on certificate status)
            if status == 'connected':
                click.echo(f"Gateway status: {click.style(status, fg='green')}")
            else:
                click.echo(f"Gateway status: {click.style(status, fg='yellow')}")
                click.echo("Note: Gateway didn't transition to CONNECTED state. Check if certificates are installed.")
        else:
            click.echo(f"Failed to connect gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def disconnect_gateway(gateway_id: str):
    """Disconnect a gateway from the MQTT broker"""
    try:
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/disconnect")
        if resp.status_code == 200:
            click.echo(f"Successfully disconnected gateway {gateway_id}")
            click.echo(f"Gateway status: {click.style('disconnected', fg='yellow')}")
        else:
            click.echo(f"Failed to disconnect gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def send_heartbeat(gateway_id: str):
    """Send a heartbeat from a gateway"""
    try:
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/heartbeat")
        if resp.status_code == 200:
            click.echo(f"Successfully sent heartbeat for gateway {gateway_id}")
            
            gateway = resp.json()['gateway']
            status = gateway.get('status', 'unknown')
            
            # Format status with color
            if status == 'connected':
                status_str = click.style(status, fg='green')
            elif status == 'disconnected':
                status_str = click.style(status, fg='yellow')
            else:
                status_str = click.style(status, fg='blue')
                
            click.echo(f"Gateway status: {status_str}")
            
            # Show last heartbeat time
            if 'last_heartbeat' in gateway:
                click.echo(f"Last heartbeat: {gateway['last_heartbeat']}")
        else:
            click.echo(f"Failed to send heartbeat: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
@click.option('--name', help='New name for the gateway')
@click.option('--location', help='New location for the gateway')
def update_gateway_info(gateway_id: str, name: Optional[str] = None, location: Optional[str] = None):
    """Update gateway information (name, location)"""
    if not name and not location:
        click.echo("Please provide at least one of --name or --location")
        return
    
    try:
        # Get current gateway info to preserve missing fields
        resp = requests.get(f"http://localhost:8000/api/gateways/{gateway_id}")
        if resp.status_code != 200:
            click.echo(f"Failed to get gateway info: {resp.text}")
            return
        
        current_info = resp.json()
        update_data = {
            "name": name or current_info.get("name", "Unnamed Gateway"),
            "location": location or current_info.get("location", "Unknown"),
            "gateway_id": gateway_id  # Include gateway_id for API validation
        }
        
        resp = requests.put(f"http://localhost:8000/api/gateways/{gateway_id}", json=update_data)
        if resp.status_code == 200:
            click.echo(f"Successfully updated gateway {gateway_id}")
            click.echo(f"  Name: {resp.json()['name']}")
            click.echo(f"  Location: {resp.json()['location']}")
        else:
            click.echo(f"Failed to update gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def reset_gateway(gateway_id: str):
    """Reset a gateway by disconnecting and reconnecting"""
    try:
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/reset")
        if resp.status_code == 200:
            click.echo(f"Successfully reset gateway {gateway_id}")
            click.echo(f"Gateway status: {resp.json()['gateway']}")
        else:
            click.echo(f"Failed to reset gateway: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def test_state_machine(gateway_id: str):
    """Test the streamlined state machine flow for a gateway"""
    try:
        # Step 1: Create the gateway if it doesn't exist
        resp = requests.get(f"http://localhost:8000/api/gateways/{gateway_id}")
        
        if resp.status_code == 404:
            click.echo(f"Creating new gateway with ID: {gateway_id}")
            create_data = {
                "name": f"Test Gateway {gateway_id}",
                "location": "Test Location",
                "gateway_id": gateway_id
            }
            resp = requests.post("http://localhost:8000/api/gateways", json=create_data)
            if resp.status_code != 201:
                click.echo(f"Failed to create gateway: {resp.text}")
                return
            
            gateway = resp.json()
            click.echo(f"Created gateway: {gateway}")
        else:
            gateway = resp.json()
            click.echo(f"Using existing gateway: {gateway}")
        
        # Step 2: Set certificate status to installed
        click.echo("\nStep 2: Setting certificate status to installed...")
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/certificate?status=true")
        if resp.status_code != 200:
            click.echo(f"Failed to set certificate status: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        status = gateway.get('status', 'unknown')
        click.echo(f"Set certificate status. Gateway status: {status}")
        
        # Step 3: Connect gateway
        click.echo("\nStep 3: Connecting gateway...")
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/connect")
        if resp.status_code != 200:
            click.echo(f"Failed to connect gateway: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        status = gateway.get('status', 'unknown')
        click.echo(f"Connected gateway. Gateway status: {status}")
        if status == 'connected':
            click.secho("Gateway is now in CONNECTED state", fg="green")
        else:
            click.secho(f"Gateway is in {status} state", fg="yellow")
        
        # Step 4: Send a heartbeat
        click.echo("\nStep 4: Sending heartbeat...")
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/heartbeat")
        if resp.status_code != 200:
            click.echo(f"Failed to send heartbeat: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        click.echo(f"Sent heartbeat. Gateway status: {gateway.get('status')}")
        click.echo(f"Last heartbeat: {gateway.get('last_heartbeat')}")
        
        # Step 5: Disconnect gateway
        click.echo("\nStep 5: Disconnecting gateway...")
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/disconnect")
        if resp.status_code != 200:
            click.echo(f"Failed to disconnect gateway: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        status = gateway.get('status', 'unknown')
        click.echo(f"Disconnected gateway. Gateway status: {status}")
        if status == 'disconnected':
            click.secho("Gateway is now in DISCONNECTED state", fg="yellow")
        else:
            click.secho(f"Gateway is in {status} state", fg="yellow")
        
        # Step 6: Reconnect with heartbeat
        click.echo("\nStep 6: Reconnecting with heartbeat...")
        resp = requests.post(f"http://localhost:8000/api/gateways/{gateway_id}/connect")
        if resp.status_code != 200:
            click.echo(f"Failed to reconnect gateway: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        status = gateway.get('status', 'unknown')
        click.echo(f"Reconnected gateway. Gateway status: {status}")
        if status == 'connected':
            click.secho("Gateway is now back in CONNECTED state", fg="green")
        else:
            click.secho(f"Gateway is in {status} state", fg="yellow")
        
        # Step 7: Delete gateway
        click.echo("\nStep 7: Deleting gateway...")
        resp = requests.delete(f"http://localhost:8000/api/gateways/{gateway_id}")
        if resp.status_code != 200:
            click.echo(f"Failed to delete gateway: {resp.text}")
            return
        
        gateway = resp.json()["gateway"]
        status = gateway.get('status', 'unknown')
        click.echo(f"Deleted gateway. Gateway status: {status}")
        if status == 'deleted':
            click.secho("Gateway is now in DELETED state", fg="red")
        else:
            click.secho(f"Gateway is in {status} state", fg="yellow")
        
        click.secho("\nCompleted full state machine test successfully!", fg="green", bold=True)
        
    except Exception as e:
        click.echo(f"Error during state machine test: {str(e)}")

@cli.command()
@click.argument('gateway_id')
@click.option('--memory', default='64MB', help='Memory usage to report')
@click.option('--cpu', default='5%', help='CPU usage to report')
@click.option('--uptime', default='3600s', help='Uptime to report')
def update_metrics(gateway_id: str, memory: str, cpu: str, uptime: str):
    """Update metrics for a gateway"""
    try:
        resp = requests.post(
            f"http://localhost:8000/api/gateways/{gateway_id}/metrics?uptime={uptime}&memory={memory}&cpu={cpu}"
        )
        if resp.status_code == 200:
            click.echo(f"Successfully updated metrics for gateway {gateway_id}")
            click.echo(f"Gateway status: {resp.json()['gateway']}")
        else:
            click.echo(f"Failed to update metrics: {resp.text}")
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def generate_cert(gateway_id: str):
    """Generate certificate for a gateway"""
    try:
        import subprocess
        from pathlib import Path
        
        # Create certificates directory
        certs_dir = Path("certs") / gateway_id
        certs_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate self-signed certificate
        cert_path = certs_dir / "cert.pem"
        key_path = certs_dir / "key.pem"
        
        click.echo(f"Generating certificate for gateway {gateway_id}...")
        
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
            click.echo(f"Certificate generation failed: {result.stderr}")
            return
        
        # Set permissions
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        click.echo(f"Certificate generated successfully at:")
        click.echo(f"  Certificate: {cert_path}")
        click.echo(f"  Private key: {key_path}")
    except Exception as e:
        click.echo(f"Error generating certificate: {str(e)}")

@cli.command()
@click.argument('gateway_id')
def inject_cert(gateway_id: str):
    """Inject certificate into a gateway container"""
    try:
        import subprocess
        from pathlib import Path
        
        # Define container name and paths based on environment
        container_name = f"gateway-{gateway_id}"
        cert_path = Path("certs") / gateway_id / "cert.pem"
        key_path = Path("certs") / gateway_id / "key.pem"
        
        # Verify files exist
        if not cert_path.exists() or not key_path.exists():
            click.echo(f"Certificate files not found. Please run 'generate_cert {gateway_id}' first.")
            return
        
        # Check if container exists
        container_check = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        
        if container_name not in container_check.stdout:
            click.echo(f"Container {container_name} not found. Make sure the gateway is created.")
            return
        
        click.echo(f"Injecting certificates into container {container_name}...")
        
        # Ensure container is running
        container_state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True, text=True
        ).stdout.strip()
        
        if container_state != "true":
            click.echo(f"Starting container {container_name}...")
            subprocess.run(["docker", "start", container_name], check=True)
            click.echo("Waiting for container to start...")
            import time
            time.sleep(2)
        
        # Copy certificate files into container
        subprocess.run(
            ["docker", "cp", str(cert_path.absolute()), f"{container_name}:/app/certificates/cert.pem"],
            check=True
        )
        
        subprocess.run(
            ["docker", "cp", str(key_path.absolute()), f"{container_name}:/app/certificates/key.pem"],
            check=True
        )
        
        click.echo("Certificates successfully injected, container should connect automatically.")
        click.echo("You can check status with 'list_gateways' command.")
        
        # Detect environment for proper API URLs
        env_type = detect_environment()
        if env_type == EnvironmentType.DOCKER_DESKTOP:
            click.echo("\nNote: Using Docker Desktop environment (host.docker.internal)")
        elif env_type == EnvironmentType.GITHUB_ACTIONS:
            click.echo("\nNote: Using GitHub Actions environment")
        else:
            click.echo("\nNote: Using standard Linux environment")
            
    except Exception as e:
        click.echo(f"Error injecting certificate: {str(e)}")

if __name__ == "__main__":
    cli()