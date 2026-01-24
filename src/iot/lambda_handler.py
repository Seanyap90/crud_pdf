"""Lambda handler for IoT Backend using Mangum."""
import os
import logging
from mangum import Mangum
from iot.main import create_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables for AWS deployment
DATABASE_HOST = os.environ.get('DATABASE_HOST', '13.221.108.179')
DATABASE_PORT = os.environ.get('DATABASE_PORT', '8080')
DEPLOYMENT_MODE = os.environ.get('DEPLOYMENT_MODE', 'deploy-aws')

logger.info(f"IoT Lambda init: {DEPLOYMENT_MODE}, DB: {DATABASE_HOST}:{DATABASE_PORT}")

# Create FastAPI app without LocalWorker (stateless AWS mode)
app = create_app(worker_instance=None)

# Wrap with Mangum for Lambda compatibility
handler = Mangum(app, lifespan="off")

# Export for Lambda runtime
lambda_handler = handler
