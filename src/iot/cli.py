import click
import asyncio
import uvicorn
import logging
import requests
from enum import Enum
from typing import Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WorkerMode(str, Enum):
    LOCAL = "local"
    MOCK_AWS = "mock_aws"
    AWS = "aws"

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
def start(mode: str, host: str, port: int, aws_region: str, reload: bool, 
          docker_network: str, mqtt_broker: str, heartbeat_interval: int,
          heartbeat_miss_threshold: int):
    """Start the IoT Gateway Management service with the specified worker mode"""
    # Import here to avoid circular imports
    from iot.worker.aws_worker import AWSWorker
    from iot.worker.local_worker import LocalWorker
    from iot.worker.mock_aws_worker import MockAWSWorker
    from iot.main import create_app
    from iot.config import update_settings
    
    # Update configuration settings
    update_settings(
        mode=mode,
        host=host,
        port=port, 
        aws_region=aws_region,
        docker_network=docker_network,
        mqtt_broker=mqtt_broker,
        heartbeat_interval=heartbeat_interval,
        heartbeat_miss_threshold=heartbeat_miss_threshold
    )
    
    # Get the appropriate worker based on mode
    worker_mode = WorkerMode(mode)
    logger.info(f"Starting service with {worker_mode.value} worker")
    
    if worker_mode == WorkerMode.AWS:
        worker = AWSWorker(aws_region)
    elif worker_mode == WorkerMode.MOCK_AWS:
        worker = MockAWSWorker()
    else:  # default to local
        worker = LocalWorker()
        
        # Check if Docker network exists
        try:
            import docker
            client = docker.from_env()
            networks = client.networks.list()
            network_exists = any(n.name == docker_network for n in networks)
            
            if not network_exists:
                logger.info(f"Creating Docker network: {docker_network}")
                client.networks.create(docker_network, driver="bridge")
                logger.info(f"Created Docker network: {docker_network}")
        except Exception as e:
            logger.error(f"Error with Docker network setup: {str(e)}")
    
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

if __name__ == "__main__":
    cli()