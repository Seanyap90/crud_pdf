# config.py
import os

class Config:
    """Global Configuration for the application"""

    # General Settings
    APP_NAME = os.getenv("APP_NAME", "FastAPI App")
    
    # S3 Configuration
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
    AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")  # For moto mock server

    # Queue Configuration
    QUEUE_TYPE = os.getenv("EXEC_MODE", "local-dev")  # Uses the mode from CLI
    SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")

config = Config()