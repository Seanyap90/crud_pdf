import platform
import subprocess
import os
import socket
from enum import Enum
from pydantic_settings import BaseSettings
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class DeploymentMode(str, Enum):
    LOCAL = "local"
    MOCK_AWS = "mock_aws"
    AWS = "aws"

class EnvironmentType(str, Enum):
    DOCKER_DESKTOP = "docker_desktop"  # WSL or Docker Desktop on Windows/Mac
    GITHUB_ACTIONS = "github_actions"  # GitHub Actions environment
    STANDARD_LINUX = "standard_linux"  # Standard Linux with Docker

def detect_environment() -> EnvironmentType:
    """Detect the environment type before class initialization"""
    # Default to standard Linux
    detected_env = EnvironmentType.STANDARD_LINUX
    
    # Check for GitHub Actions
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return EnvironmentType.GITHUB_ACTIONS
        
    # Check for WSL/Docker Desktop
    is_wsl = False
    if platform.system() == "Linux" and os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        is_wsl = True
    
    # Check if host.docker.internal is resolvable
    try:
        socket.gethostbyname("host.docker.internal")
        return EnvironmentType.DOCKER_DESKTOP
    except socket.gaierror:
        # Only if we're in WSL but host.docker.internal isn't resolvable, still consider it Docker Desktop
        if is_wsl:
            return EnvironmentType.DOCKER_DESKTOP
    
    return detected_env

# Detect environment once at module level
DETECTED_ENVIRONMENT = detect_environment()
logger.info(f"Environment detected as: {DETECTED_ENVIRONMENT}")

# Calculate the container settings based on detected environment
def get_container_api_url(port: int = 8000) -> str:
    if DETECTED_ENVIRONMENT == EnvironmentType.DOCKER_DESKTOP:
        return f"http://host.docker.internal:{port}"
    elif DETECTED_ENVIRONMENT == EnvironmentType.GITHUB_ACTIONS:
        return f"http://172.17.0.1:{port}"
    else:
        try:
            hostname = socket.gethostname()
            return f"http://{hostname}:{port}"
        except:
            return f"http://localhost:{port}"

def get_container_mqtt_address(port: int = 1883) -> str:
    if DETECTED_ENVIRONMENT == EnvironmentType.DOCKER_DESKTOP:
        return f"host.docker.internal:{port}"
    else:
        return f"mqtt-broker:{port}"

class Settings(BaseSettings):
    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # Default deployment mode
    DEPLOYMENT_MODE: DeploymentMode = DeploymentMode.LOCAL
    
    # Environment detection - use the pre-detected value
    ENVIRONMENT_TYPE: EnvironmentType = DETECTED_ENVIRONMENT
    
    # Database Configuration
    DB_PATH: str = "recycling.db"
    
    # AWS Configuration
    AWS_REGION: str = "us-west-2"
    
    # Docker Configuration
    DOCKER_NETWORK: str = "iot-network"
    GATEWAY_IMAGE: str = "iot-gateway-simulator"
    
    # MQTT Configuration
    MQTT_BROKER_HOST: str = "mqtt-broker"
    MQTT_BROKER_PORT: int = 1883
    
    # Container Configuration with pre-calculated default values
    CONTAINER_API_URL: str = get_container_api_url(8000)
    CONTAINER_MQTT_ADDRESS: str = get_container_mqtt_address(1883)
    
    # State Machine Configuration
    CONNECTION_TIMEOUT_MINUTES: int = 5
    RESPONSE_TIMEOUT_MINUTES: int = 1
    
    # Heartbeat Configuration
    HEARTBEAT_INTERVAL_SECONDS: int = 30
    HEARTBEAT_MISS_THRESHOLD: int = 3  # Number of missed heartbeats before considering offline
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

# Create a settings instance
settings = Settings()

# Configure logging based on settings
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def update_settings(**kwargs):
    """Update settings at runtime"""
    global settings
    
    logger.info(f"Updating settings: {kwargs}")
    
    # Update deployment mode if provided
    if "mode" in kwargs:
        settings.DEPLOYMENT_MODE = DeploymentMode(kwargs["mode"])
    
    # Update environment type if provided
    if "environment_type" in kwargs:
        settings.ENVIRONMENT_TYPE = kwargs["environment_type"]
        # Recalculate container URLs
        settings.CONTAINER_API_URL = get_container_api_url(settings.API_PORT)
        settings.CONTAINER_MQTT_ADDRESS = get_container_mqtt_address(settings.MQTT_BROKER_PORT)
    
    # Update state machine settings
    if "connection_timeout" in kwargs:
        settings.CONNECTION_TIMEOUT_MINUTES = kwargs["connection_timeout"]
    if "response_timeout" in kwargs:
        settings.RESPONSE_TIMEOUT_MINUTES = kwargs["response_timeout"]
    
    # Update API settings
    if "host" in kwargs:
        settings.API_HOST = kwargs["host"]
    if "port" in kwargs:
        settings.API_PORT = kwargs["port"]
        # Recalculate container API URL since port changed
        settings.CONTAINER_API_URL = get_container_api_url(settings.API_PORT)
    
    # Update database settings
    if "db_path" in kwargs:
        settings.DB_PATH = kwargs["db_path"]
        
    # Update AWS settings
    if "aws_region" in kwargs:
        settings.AWS_REGION = kwargs["aws_region"]
        
    # Update Docker settings
    if "docker_network" in kwargs:
        settings.DOCKER_NETWORK = kwargs["docker_network"]
        
    # Update MQTT settings
    if "mqtt_broker" in kwargs:
        settings.MQTT_BROKER_HOST = kwargs["mqtt_broker"]
    if "mqtt_port" in kwargs:
        settings.MQTT_BROKER_PORT = kwargs["mqtt_port"]
        # Recalculate container MQTT address
        settings.CONTAINER_MQTT_ADDRESS = get_container_mqtt_address(settings.MQTT_BROKER_PORT)
        
    # Update heartbeat settings
    if "heartbeat_interval" in kwargs:
        settings.HEARTBEAT_INTERVAL_SECONDS = kwargs["heartbeat_interval"]
    if "heartbeat_miss_threshold" in kwargs:
        settings.HEARTBEAT_MISS_THRESHOLD = kwargs["heartbeat_miss_threshold"]
        
    logger.info(f"Settings updated. Mode: {settings.DEPLOYMENT_MODE}, Environment: {settings.ENVIRONMENT_TYPE}")
    logger.info(f"API: {settings.API_HOST}:{settings.API_PORT}")
    logger.info(f"Container API URL: {settings.CONTAINER_API_URL}")
    logger.info(f"Container MQTT Address: {settings.CONTAINER_MQTT_ADDRESS}")