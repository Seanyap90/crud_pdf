"""Lambda handler for IoT Backend using Mangum."""
import os
import logging
import boto3
from mangum import Mangum
from iot.main import create_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables for AWS deployment
DATABASE_HOST = os.environ.get('DATABASE_HOST', '44.201.200.44')
DATABASE_PORT = os.environ.get('DATABASE_PORT', '8080')
DEPLOYMENT_MODE = os.environ.get('DEPLOYMENT_MODE', 'deploy-aws')
GATEWAY_SM_ARN = os.environ.get('GATEWAY_SM_ARN', '')
CONFIG_SM_ARN = os.environ.get('CONFIG_SM_ARN', '')

logger.info(f"IoT Lambda init: {DEPLOYMENT_MODE}, DB: {DATABASE_HOST}:{DATABASE_PORT}")

# Step Functions client — shared across invocations
sfn_client = boto3.client('stepfunctions')

# Create FastAPI app without LocalWorker (stateless AWS mode)
app = create_app(worker_instance=None)

# Wrap with Mangum for Lambda compatibility
handler = Mangum(app, lifespan="off")

# Export for Lambda runtime
lambda_handler = handler
