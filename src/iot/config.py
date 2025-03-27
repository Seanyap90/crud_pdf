from enum import Enum
from pydantic_settings import BaseSettings
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class DeploymentMode(str, Enum):
    LOCAL = "local"
    MOCK_AWS = "mock_aws"
    AWS = "aws"

class Settings(BaseSettings):
    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # Default deployment mode
    DEPLOYMENT_MODE: DeploymentMode = DeploymentMode.LOCAL

    # Database Configuration
    DB_PATH: str = "recycling.db"
    
    # AWS Configuration
    AWS_REGION: str = "us-east-1"
    
    # Docker Configuration
    DOCKER_NETWORK: str = "iot-network"
    GATEWAY_IMAGE: str = "iot-gateway-simulator"
    
    # MQTT Configuration
    MQTT_BROKER_HOST: str = "mqtt-broker"
    MQTT_BROKER_PORT: int = 1883
    
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
        
    # Update heartbeat settings
    if "heartbeat_interval" in kwargs:
        settings.HEARTBEAT_INTERVAL_SECONDS = kwargs["heartbeat_interval"]
    if "heartbeat_miss_threshold" in kwargs:
        settings.HEARTBEAT_MISS_THRESHOLD = kwargs["heartbeat_miss_threshold"]
        
    logger.info(f"Settings updated. Mode: {settings.DEPLOYMENT_MODE}, API: {settings.API_HOST}:{settings.API_PORT}")