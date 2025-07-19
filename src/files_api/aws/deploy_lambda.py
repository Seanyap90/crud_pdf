"""AWS Lambda deployment for Files API and IoT Backend FastAPI applications."""
import os
import logging
import json
import time
import subprocess
import argparse
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List

# Import settings
from files_api.settings import get_settings

# Import AWS utilities
from files_api.aws.utils import (
    get_iam_client,
    AWSClientManager
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get settings instance
settings = get_settings()

# Constants
DEFAULT_REGION = settings.aws_region
LAMBDA_RUNTIME = "python3.11"
LAMBDA_TIMEOUT = 30
LAMBDA_MEMORY = 512


def log_operation(description: str):
    """Decorator for timing and logging Lambda deployment operations."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger.info(f"Starting: {description}")
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"Completed: {description} in {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Failed: {description} after {duration:.2f}s - {str(e)}")
                raise
        return wrapper
    return decorator


class LambdaLayerManager:
    """Manager for Lambda layers with lightweight dependencies."""
    
    def __init__(self, region: str = None):
        self.region = region or DEFAULT_REGION
        self.lambda_client = AWSClientManager().get_client('lambda')
        self.layers = {}
        
    @log_operation("Creating FastAPI lightweight layer")
    def create_fastapi_layer(self) -> Dict[str, Any]:
        """Create lightweight FastAPI layer for both Files API and IoT."""
        layer_name = f"{settings.app_name}-fastapi-layer"
        
        try:
            # Check for existing layer
            existing_layer = self._find_existing_layer(layer_name)
            if existing_layer:
                logger.info(f"Using existing FastAPI layer: {layer_name}")
                self.layers['fastapi'] = existing_layer
                return existing_layer
            
            logger.info("No existing layer found, creating new layer...")
            
            # Create lightweight layer with minimal dependencies
            layer_zip_path = self._create_fastapi_layer_zip()
            logger.info(f"Created layer ZIP: {layer_zip_path}")
            
            # Upload layer
            with open(layer_zip_path, 'rb') as f:
                logger.info(f"Publishing layer {layer_name}...")
                layer_response = self.lambda_client.publish_layer_version(
                    LayerName=layer_name,
                    Description="Lightweight FastAPI layer for Files API and IoT Backend",
                    Content={'ZipFile': f.read()},
                    CompatibleRuntimes=[LAMBDA_RUNTIME],
                    CompatibleArchitectures=['x86_64']
                )
            
            logger.info(f"Layer response keys: {list(layer_response.keys())}")
            
            # Extract layer ARN from layer version ARN (remove :version at the end)
            layer_version_arn = layer_response['LayerVersionArn']
            layer_arn = ':'.join(layer_version_arn.split(':')[:-1])
            
            layer_info = {
                'layer_name': layer_name,
                'layer_arn': layer_arn,
                'layer_version_arn': layer_version_arn,
                'version': layer_response['Version']
            }
            
            self.layers['fastapi'] = layer_info
            logger.info(f"Created FastAPI layer: {layer_name} v{layer_response['Version']}")
            
            # Cleanup temporary files
            os.remove(layer_zip_path)
            
            return layer_info
            
        except Exception as e:
            logger.error(f"Failed to create FastAPI layer: {e}")
            raise
    
    def _create_fastapi_layer_zip(self) -> str:
        """Create ZIP file with lightweight FastAPI dependencies."""
        # Create temporary directory for layer
        with tempfile.TemporaryDirectory() as temp_dir:
            python_dir = Path(temp_dir) / "python"
            python_dir.mkdir()
            
            # Install lightweight dependencies
            requirements = [
                "fastapi==0.104.1",
                "uvicorn==0.24.0",
                "pydantic==2.4.2",
                "mangum==0.17.0",
                "pymongo>=4.0.0",
                "python-multipart==0.0.6"  # For form uploads
            ]
            
            # Install packages to target directory only
            subprocess.run([
                "pip", "install", "--target", str(python_dir), "--no-cache-dir", "--upgrade"
            ] + requirements, check=True)
            
            # Create layer ZIP
            layer_zip_path = Path(temp_dir) / "fastapi-layer.zip"
            with zipfile.ZipFile(layer_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in python_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(temp_dir)
                        zipf.write(file_path, arcname)
            
            # Copy to permanent location
            final_zip_path = f"/tmp/fastapi-layer-{int(time.time())}.zip"
            shutil.copy2(layer_zip_path, final_zip_path)
            
            return final_zip_path
    
    def _find_existing_layer(self, layer_name: str) -> Dict[str, Any]:
        """Find existing Lambda layer."""
        try:
            response = self.lambda_client.list_layer_versions(LayerName=layer_name)
            if response['LayerVersions']:
                latest_version = response['LayerVersions'][0]
                # Extract layer ARN from layer version ARN if LayerArn not available
                layer_version_arn = latest_version['LayerVersionArn']
                layer_arn = latest_version.get('LayerArn', ':'.join(layer_version_arn.split(':')[:-1]))
                
                return {
                    'layer_name': layer_name,
                    'layer_arn': layer_arn,
                    'layer_version_arn': layer_version_arn,
                    'version': latest_version['Version']
                }
            return None
        except self.lambda_client.exceptions.ResourceNotFoundException:
            return None
    
    def get_layer_arns(self) -> List[str]:
        """Get list of layer ARNs for Lambda functions."""
        return [layer['layer_version_arn'] for layer in self.layers.values()]
    
    def cleanup_layers(self) -> None:
        """Clean up Lambda layers."""
        for layer_name, layer_info in self.layers.items():
            try:
                # Delete all versions of the layer
                response = self.lambda_client.list_layer_versions(LayerName=layer_info['layer_name'])
                for version in response['LayerVersions']:
                    self.lambda_client.delete_layer_version(
                        LayerName=layer_info['layer_name'],
                        VersionNumber=version['Version']
                    )
                    logger.info(f"Deleted layer version: {layer_info['layer_name']} v{version['Version']}")
            except Exception as e:
                logger.warning(f"Failed to delete layer {layer_name}: {e}")


class LambdaDeployer:
    """Deployer for Lambda functions."""
    
    def __init__(self, region: str = None):
        self.region = region or DEFAULT_REGION
        self.lambda_client = AWSClientManager().get_client('lambda')
        self.iam_client = get_iam_client()
        self.layer_manager = LambdaLayerManager(region)
        self.functions = {}
        
    @log_operation("Deploying Files API Lambda")
    def deploy_files_api_lambda(self) -> Dict[str, Any]:
        """Deploy Files API as Lambda function."""
        function_name = f"{settings.app_name}-files-api"
        
        try:
            # Create FastAPI layer
            layer_info = self.layer_manager.create_fastapi_layer()
            
            # Create deployment package
            deployment_zip = self._create_files_api_package()
            
            # Get or create execution role
            execution_role_arn = self._get_lambda_execution_role()
            
            # Check for existing function
            existing_function = self._find_existing_function(function_name)
            
            if existing_function:
                # Update existing function
                lambda_response = self._update_lambda_function(
                    function_name, deployment_zip, [layer_info['layer_version_arn']]
                )
                logger.info(f"Updated Files API Lambda: {function_name}")
            else:
                # Create new function
                lambda_response = self.lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime=LAMBDA_RUNTIME,
                    Role=execution_role_arn,
                    Handler="files_api.lambda_handler.lambda_handler",
                    Code={'ZipFile': deployment_zip},
                    Description="Files API FastAPI application",
                    Timeout=LAMBDA_TIMEOUT,
                    MemorySize=LAMBDA_MEMORY,
                    Layers=[layer_info['layer_version_arn']],
                    Environment={
                        'Variables': {
                            'DEPLOYMENT_MODE': 'aws-prod',
                            'S3_BUCKET_NAME': settings.s3_bucket_name,
                            'SQS_QUEUE_URL': settings.sqs_queue_url or '',
                            'MONGODB_URI': f"mongodb://{settings.app_name}-mongodb.{settings.app_name}.local:27017/crud_pdf"
                        }
                    },
                    Tags={
                        'Project': settings.app_name,
                        'Component': 'FilesAPI',
                        'Type': 'Lambda'
                    }
                )
                logger.info(f"Created Files API Lambda: {function_name}")
            
            function_info = {
                'function_name': function_name,
                'function_arn': lambda_response['FunctionArn'],
                'version': lambda_response.get('Version', '$LATEST'),
                'handler': lambda_response['Handler'],
                'layers': [layer_info['layer_version_arn']]
            }
            
            self.functions['files_api'] = function_info
            
            # Cleanup
            os.remove(deployment_zip)
            
            return function_info
            
        except Exception as e:
            logger.error(f"Failed to deploy Files API Lambda: {e}")
            raise
    
    @log_operation("Deploying IoT Backend Lambda")
    def deploy_iot_lambda(self) -> Dict[str, Any]:
        """Deploy IoT Backend as Lambda function."""
        function_name = f"{settings.app_name}-iot-backend"
        
        try:
            # Use same FastAPI layer
            if 'fastapi' not in self.layer_manager.layers:
                layer_info = self.layer_manager.create_fastapi_layer()
            else:
                layer_info = self.layer_manager.layers['fastapi']
            
            # Create deployment package
            deployment_zip = self._create_iot_package()
            
            # Get execution role
            execution_role_arn = self._get_lambda_execution_role()
            
            # Check for existing function
            existing_function = self._find_existing_function(function_name)
            
            if existing_function:
                # Update existing function
                lambda_response = self._update_lambda_function(
                    function_name, deployment_zip, [layer_info['layer_version_arn']]
                )
                logger.info(f"Updated IoT Backend Lambda: {function_name}")
            else:
                # Create new function
                lambda_response = self.lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime=LAMBDA_RUNTIME,
                    Role=execution_role_arn,
                    Handler="iot.lambda_handler.lambda_handler",
                    Code={'ZipFile': deployment_zip},
                    Description="IoT Backend FastAPI application",
                    Timeout=LAMBDA_TIMEOUT,
                    MemorySize=LAMBDA_MEMORY,
                    Layers=[layer_info['layer_version_arn']],
                    Environment={
                        'Variables': {
                            'DEPLOYMENT_MODE': 'aws-prod',
                            'DATABASE_PATH': '/tmp/recycling.db'  # SQLite for IoT
                        }
                    },
                    Tags={
                        'Project': settings.app_name,
                        'Component': 'IoTBackend',
                        'Type': 'Lambda'
                    }
                )
                logger.info(f"Created IoT Backend Lambda: {function_name}")
            
            function_info = {
                'function_name': function_name,
                'function_arn': lambda_response['FunctionArn'],
                'version': lambda_response.get('Version', '$LATEST'),
                'handler': lambda_response['Handler'],
                'layers': [layer_info['layer_version_arn']]
            }
            
            self.functions['iot_backend'] = function_info
            
            # Cleanup
            os.remove(deployment_zip)
            
            return function_info
            
        except Exception as e:
            logger.error(f"Failed to deploy IoT Backend Lambda: {e}")
            raise
    
    def _create_files_api_package(self) -> str:
        """Create deployment package for Files API."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "package"
            package_dir.mkdir()
            
            # Copy Files API source code (excluding heavy dependencies)
            # Use absolute path to handle different working directories
            project_root = Path(__file__).parent.parent.parent.parent
            src_files_api = project_root / "src" / "files_api"
            dest_files_api = package_dir / "files_api"
            
            shutil.copytree(src_files_api, dest_files_api, ignore=shutil.ignore_patterns(
                'vlm', '*.pyc', '__pycache__', 'docker-compose*.yml', 'offload_folder'
            ))
            
            # Copy database module (for NoSQL adapter)
            src_database = project_root / "src" / "database"
            dest_database = package_dir / "database"
            shutil.copytree(src_database, dest_database, ignore=shutil.ignore_patterns(
                '*.pyc', '__pycache__'
            ))
            
            # Create Lambda handler if it doesn't exist
            lambda_handler_path = dest_files_api / "lambda_handler.py"
            if not lambda_handler_path.exists():
                with open(lambda_handler_path, 'w') as f:
                    f.write("""\"\"\"Lambda handler for Files API using Mangum.\"\"\"
from mangum import Mangum
from files_api.main import create_app

# Create FastAPI app
app = create_app()

# Wrap with Mangum for Lambda compatibility
handler = Mangum(app, lifespan="off")

# Export handler for Lambda runtime
lambda_handler = handler
""")
            
            # Create ZIP package
            zip_path = f"/tmp/files-api-{int(time.time())}.zip"
            file_count = 0
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in package_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(package_dir)
                        zipf.write(file_path, arcname)
                        file_count += 1
            
            # Validate ZIP file
            zip_size = os.path.getsize(zip_path)
            logger.info(f"Created ZIP package: {zip_path} ({zip_size} bytes, {file_count} files)")
            
            # Verify ZIP file integrity
            try:
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    zipf.testzip()
                    logger.info("ZIP file integrity verified")
            except Exception as e:
                logger.error(f"ZIP file integrity check failed: {e}")
                raise
            
            return zip_path
    
    def _create_iot_package(self) -> str:
        """Create deployment package for IoT Backend."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "package"
            package_dir.mkdir()
            
            # Copy IoT source code
            src_iot = Path("src/iot")
            dest_iot = package_dir / "iot"
            shutil.copytree(src_iot, dest_iot, ignore=shutil.ignore_patterns(
                'gateway', 'rules_engine', 'mosquitto', '*.pyc', '__pycache__', 'docker-compose*.yml'
            ))
            
            # Copy database module
            src_database = Path("src/database")
            dest_database = package_dir / "database"
            shutil.copytree(src_database, dest_database, ignore=shutil.ignore_patterns(
                '*.pyc', '__pycache__'
            ))
            
            # Create Lambda handler for IoT
            lambda_handler_path = dest_iot / "lambda_handler.py"
            with open(lambda_handler_path, 'w') as f:
                f.write("""\"\"\"Lambda handler for IoT Backend using Mangum.\"\"\"
from mangum import Mangum
from iot.main import create_app

# Create FastAPI app
app = create_app()

# Wrap with Mangum for Lambda compatibility
handler = Mangum(app, lifespan="off")

# Export handler for Lambda runtime
lambda_handler = handler
""")
            
            # Create ZIP package
            zip_path = f"/tmp/iot-backend-{int(time.time())}.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in package_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(package_dir)
                        zipf.write(file_path, arcname)
            
            return zip_path
    
    def _get_lambda_execution_role(self) -> str:
        """Get or create Lambda execution role."""
        role_name = f"{settings.app_name}-lambda-execution-role"
        
        try:
            # Check for existing role
            try:
                role_response = self.iam_client.get_role(RoleName=role_name)
                return role_response['Role']['Arn']
            except self.iam_client.exceptions.NoSuchEntityException:
                pass
            
            # Create execution role
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            
            role_response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=f"Lambda execution role for {settings.app_name}",
                Tags=[
                    {'Key': 'Name', 'Value': role_name},
                    {'Key': 'Project', 'Value': settings.app_name}
                ]
            )
            
            # Attach basic execution policy
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
            )
            
            # Attach VPC execution policy (for ECS networking)
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole'
            )
            
            logger.info(f"Created Lambda execution role: {role_name}")
            return role_response['Role']['Arn']
            
        except Exception as e:
            logger.error(f"Failed to create Lambda execution role: {e}")
            raise
    
    def _find_existing_function(self, function_name: str) -> Dict[str, Any]:
        """Find existing Lambda function."""
        try:
            response = self.lambda_client.get_function(FunctionName=function_name)
            return response
        except self.lambda_client.exceptions.ResourceNotFoundException:
            return None
    
    def _update_lambda_function(self, function_name: str, zip_content: str, layers: List[str]) -> Dict[str, Any]:
        """Update existing Lambda function."""
        with open(zip_content, 'rb') as f:
            # Update function code
            self.lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=f.read()
            )
            
            # Update function configuration
            response = self.lambda_client.update_function_configuration(
                FunctionName=function_name,
                Layers=layers,
                Timeout=LAMBDA_TIMEOUT,
                MemorySize=LAMBDA_MEMORY
            )
            
            return response
    
    def get_deployment_summary(self) -> Dict[str, Any]:
        """Get deployment summary."""
        return {
            'functions': self.functions,
            'layers': self.layer_manager.layers,
            'region': self.region,
            'runtime': LAMBDA_RUNTIME
        }
    
    def cleanup_functions(self) -> None:
        """Clean up Lambda functions."""
        for function_name, function_info in self.functions.items():
            try:
                self.lambda_client.delete_function(FunctionName=function_info['function_name'])
                logger.info(f"Deleted Lambda function: {function_info['function_name']}")
            except Exception as e:
                logger.warning(f"Failed to delete function {function_name}: {e}")
        
        # Clean up layers
        self.layer_manager.cleanup_layers()


def deploy_all_lambdas(region: str = None) -> Dict[str, Any]:
    """Deploy both Files API and IoT Backend Lambdas."""
    deployer = LambdaDeployer(region)
    
    try:
        # Deploy Files API only (IoT deployment removed)
        files_api_info = deployer.deploy_files_api_lambda()
        
        deployment_summary = deployer.get_deployment_summary()
        deployment_summary['status'] = 'success'
        deployment_summary['deployment_time'] = time.time()
        
        logger.info("All Lambda functions deployed successfully")
        return deployment_summary
        
    except Exception as e:
        logger.error(f"Lambda deployment failed: {e}")
        raise


def cleanup_all_lambdas(region: str = None) -> None:
    """Clean up all Lambda functions and layers."""
    deployer = LambdaDeployer(region)
    deployer.cleanup_functions()


def main():
    """Main deployment entry point."""
    parser = argparse.ArgumentParser(description="Deploy Lambda functions for Files API and IoT Backend")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument("--cleanup", action="store_true", help="Clean up Lambda functions instead of deploying")
    parser.add_argument("--files-api-only", action="store_true", help="Deploy only Files API Lambda")
    parser.add_argument("--iot-only", action="store_true", help="Deploy only IoT Backend Lambda")
    
    args = parser.parse_args()
    
    try:
        if args.cleanup:
            cleanup_all_lambdas(args.region)
            print("Lambda functions cleaned up successfully")
            return 0
        
        if args.files_api_only:
            deployer = LambdaDeployer(args.region)
            result = deployer.deploy_files_api_lambda()
            print(json.dumps(result, indent=2, default=str))
        elif args.iot_only:
            deployer = LambdaDeployer(args.region)
            result = deployer.deploy_iot_lambda()
            print(json.dumps(result, indent=2, default=str))
        else:
            # Deploy all
            result = deploy_all_lambdas(args.region)
            print(json.dumps(result, indent=2, default=str))
        
        return 0
        
    except Exception as e:
        logger.error(f"Lambda deployment error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())