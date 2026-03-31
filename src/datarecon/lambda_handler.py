"""Lambda handler for Data Reconciliation API using Mangum."""
from mangum import Mangum
from datarecon.main import create_app

app = create_app()

handler = Mangum(app, lifespan="off")

lambda_handler = handler
