"""AWS Lambda deployment for Data Reconciliation FastAPI application.

Standalone deployment script following the Files API lean pattern:
- Single Lambda function with Mangum handler
- Reuses the shared FastAPI Lambda layer
- No state machines, extra Lambdas, or IoT infrastructure
"""
import os
import sys
import logging
import json
import time
import argparse
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deployment.aws.utils.aws_clients import AWSClientManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

LAMBDA_RUNTIME = "python3.11"
LAMBDA_TIMEOUT = 120
LAMBDA_MEMORY = 1024
FUNCTION_NAME = "datarecon-api"


class DataReconDeployer:
    """Deploys the datarecon FastAPI app as a single Lambda function."""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.client_manager = AWSClientManager()
        self.lambda_client = self.client_manager.get_client("lambda")
        self.iam_client = self.client_manager.get_client("iam")

    def deploy(self, database_host: str, database_port: int = 8080) -> Dict[str, Any]:
        """Full deployment: package → layer → Lambda → Function URL."""
        logger.info("=== Deploying Data Reconciliation API ===")

        # Step 1: Create deployment ZIP
        zip_path = self._create_package()

        # Step 2: Get or create FastAPI layer
        layer_arn = self._get_or_create_layer()

        # Step 3: Get or create execution role
        role_arn = self._get_or_create_role()

        # Step 4: Deploy Lambda function
        function_info = self._deploy_function(
            zip_path, layer_arn, role_arn, database_host, database_port
        )

        # Step 5: Create Function URL
        function_url = self._create_function_url(FUNCTION_NAME)
        function_info["function_url"] = function_url

        # Cleanup
        os.remove(zip_path)

        logger.info(f"Deployment complete. URL: {function_url}")
        return function_info

    def _create_package(self) -> str:
        """Create deployment ZIP with src/datarecon/ + src/database/."""
        logger.info("Creating deployment package...")
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "package"
            package_dir.mkdir()

            src_dir = PROJECT_ROOT / "src"

            # Copy datarecon source
            shutil.copytree(
                src_dir / "datarecon",
                package_dir / "datarecon",
                ignore=shutil.ignore_patterns("*.pyc", "__pycache__"),
            )

            # Copy shared database module
            shutil.copytree(
                src_dir / "database",
                package_dir / "database",
                ignore=shutil.ignore_patterns("*.pyc", "__pycache__"),
            )

            # Create ZIP
            zip_path = f"/tmp/datarecon-{int(time.time())}.zip"
            file_count = 0
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in package_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(package_dir)
                        zipf.write(file_path, arcname)
                        file_count += 1

            zip_size = os.path.getsize(zip_path)
            logger.info(f"Package created: {zip_path} ({zip_size} bytes, {file_count} files)")
            return zip_path

    def _get_or_create_layer(self) -> str:
        """Reuse existing FastAPI layer or create one."""
        logger.info("Looking for existing FastAPI layer...")
        try:
            response = self.lambda_client.list_layers(CompatibleRuntime=LAMBDA_RUNTIME)
            for layer in response.get("Layers", []):
                if "fastapi" in layer["LayerName"].lower():
                    latest = layer["LatestMatchingVersion"]["LayerVersionArn"]
                    logger.info(f"Reusing layer: {latest}")
                    return latest
        except Exception:
            pass

        # Import layer manager from existing deployment code
        logger.info("No existing layer found, creating new one...")
        from deployment.aws.services.lambda_deploy import LambdaLayerManager

        manager = LambdaLayerManager(self.region)
        layer_info = manager.create_fastapi_layer()
        return layer_info["layer_version_arn"]

    def _get_or_create_role(self) -> str:
        """Get or create Lambda execution role."""
        role_name = "datarecon-lambda-execution-role"
        try:
            response = self.iam_client.get_role(RoleName=role_name)
            arn = response["Role"]["Arn"]
            logger.info(f"Reusing role: {arn}")
            return arn
        except self.iam_client.exceptions.NoSuchEntityException:
            pass

        logger.info(f"Creating role: {role_name}")
        assume_role_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })
        response = self.iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=assume_role_policy,
            Description="Execution role for Data Reconciliation Lambda",
        )
        role_arn = response["Role"]["Arn"]

        # Attach basic execution policy
        self.iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )

        # Wait for role propagation
        logger.info("Waiting for role propagation...")
        time.sleep(10)
        return role_arn

    def _deploy_function(
        self, zip_path: str, layer_arn: str, role_arn: str,
        database_host: str, database_port: int,
    ) -> Dict[str, Any]:
        """Create or update the Lambda function."""
        env_vars = {
            "DEPLOYMENT_MODE": "deploy-aws",
            "DATABASE_HOST": database_host or "",
            "DATABASE_PORT": str(database_port),
        }

        try:
            # Check if function exists
            self.lambda_client.get_function(FunctionName=FUNCTION_NAME)
            logger.info(f"Updating existing function: {FUNCTION_NAME}")

            with open(zip_path, "rb") as f:
                self.lambda_client.update_function_code(
                    FunctionName=FUNCTION_NAME, ZipFile=f.read()
                )

            # Wait for update to finish
            time.sleep(5)

            self.lambda_client.update_function_configuration(
                FunctionName=FUNCTION_NAME,
                Layers=[layer_arn],
                Environment={"Variables": env_vars},
                Timeout=LAMBDA_TIMEOUT,
                MemorySize=LAMBDA_MEMORY,
            )
            response = self.lambda_client.get_function(FunctionName=FUNCTION_NAME)
            return {
                "function_name": FUNCTION_NAME,
                "function_arn": response["Configuration"]["FunctionArn"],
            }

        except self.lambda_client.exceptions.ResourceNotFoundException:
            logger.info(f"Creating new function: {FUNCTION_NAME}")
            with open(zip_path, "rb") as f:
                response = self.lambda_client.create_function(
                    FunctionName=FUNCTION_NAME,
                    Runtime=LAMBDA_RUNTIME,
                    Role=role_arn,
                    Handler="datarecon.lambda_handler.lambda_handler",
                    Code={"ZipFile": f.read()},
                    Description="Data Reconciliation FastAPI application",
                    Timeout=LAMBDA_TIMEOUT,
                    MemorySize=LAMBDA_MEMORY,
                    Layers=[layer_arn],
                    Environment={"Variables": env_vars},
                    Tags={
                        "Project": "crud-pdf",
                        "Component": "DataRecon",
                        "Type": "Lambda",
                    },
                )
            return {
                "function_name": FUNCTION_NAME,
                "function_arn": response["FunctionArn"],
            }

    def _create_function_url(self, function_name: str) -> str:
        """Create or get Lambda Function URL."""
        try:
            response = self.lambda_client.get_function_url_config(
                FunctionName=function_name
            )
            url = response["FunctionUrl"]
            logger.info(f"Function URL exists: {url}")
            return url
        except self.lambda_client.exceptions.ResourceNotFoundException:
            pass

        # Add permission for public access
        try:
            self.lambda_client.add_permission(
                FunctionName=function_name,
                StatementId="FunctionURLAllowPublicAccess",
                Action="lambda:InvokeFunctionUrl",
                Principal="*",
                FunctionUrlAuthType="NONE",
            )
        except self.lambda_client.exceptions.ResourceConflictException:
            pass

        response = self.lambda_client.create_function_url_config(
            FunctionName=function_name,
            AuthType="NONE",
            Cors={
                "AllowOrigins": ["*"],
                "AllowMethods": ["*"],
                "AllowHeaders": ["*"],
            },
        )
        url = response["FunctionUrl"]
        logger.info(f"Created Function URL: {url}")
        return url


def main():
    parser = argparse.ArgumentParser(description="Deploy Data Reconciliation API to AWS Lambda")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--database-host", default=os.getenv("DATABASE_HOST", ""), help="Database host")
    parser.add_argument("--database-port", type=int, default=int(os.getenv("DATABASE_PORT", "8080")), help="Database port")
    args = parser.parse_args()

    deployer = DataReconDeployer(region=args.region)
    result = deployer.deploy(database_host=args.database_host, database_port=args.database_port)

    print(f"\nDeployment Result:")
    print(f"  Function: {result['function_name']}")
    print(f"  ARN: {result['function_arn']}")
    print(f"  URL: {result.get('function_url', 'N/A')}")


if __name__ == "__main__":
    main()
