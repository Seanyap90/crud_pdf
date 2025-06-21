"""Lambda handler for Files API using Mangum."""
from mangum import Mangum
from files_api.main import create_app

# Create FastAPI app
app = create_app()

# Wrap with Mangum for Lambda compatibility
handler = Mangum(app, lifespan="off")

# Export handler for Lambda runtime
lambda_handler = handler