"""
Lambda Layer Management
Creates and manages lightweight Lambda layers for FastAPI applications.
Supports both Files API and IoT Backend with shared lightweight dependencies.
"""
import os
import json
import logging
import tempfile
import zipfile
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from deployment.aws.utils.aws_clients import get_lambda_client
from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class LayerConfig:
    """Configuration for Lambda layer creation."""
    layer_name: str
    description: str
    dependencies: List[str]
    python_version: str = "python3.11"
    architecture: str = "x86_64"


class LayerManager:
    """Manages Lambda layers for lightweight FastAPI applications."""
    
    def __init__(self):
        self.lambda_client = get_lambda_client()
        self.region = settings.aws_region
        
    def create_requirements_file(self, dependencies: List[str], temp_dir: Path) -> Path:
        """Create requirements.txt file with specified dependencies."""
        requirements_file = temp_dir / "requirements.txt"
        
        with open(requirements_file, 'w') as f:
            for dep in dependencies:
                f.write(f"{dep}\n")
        
        logger.info(f"Created requirements.txt with {len(dependencies)} dependencies")
        return requirements_file
    
    def install_dependencies(self, requirements_file: Path, target_dir: Path) -> bool:
        """Install dependencies to target directory using pip."""
        try:
            # Create python/lib/python3.11/site-packages structure for Lambda layer
            python_dir = target_dir / "python" / "lib" / "python3.11" / "site-packages"
            python_dir.mkdir(parents=True, exist_ok=True)
            
            # Install dependencies
            cmd = [
                "pip", "install",
                "-r", str(requirements_file),
                "--target", str(python_dir),
                "--no-deps",  # Skip dependencies to avoid bloat
                "--upgrade"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            logger.info(f"Successfully installed dependencies: {result.stdout}")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install dependencies: {e.stderr}")
            return False
    
    def create_layer_zip(self, source_dir: Path, zip_path: Path) -> bool:
        """Create ZIP file for Lambda layer."""
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in source_dir.rglob('*'):
                    if file_path.is_file():
                        # Calculate archive name relative to source_dir
                        archive_name = file_path.relative_to(source_dir)
                        zipf.write(file_path, archive_name)
                        
            logger.info(f"Created layer ZIP: {zip_path} ({zip_path.stat().st_size} bytes)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create layer ZIP: {e}")
            return False
    
    def publish_layer(self, layer_config: LayerConfig, zip_path: Path) -> Optional[Dict[str, Any]]:
        """Publish Lambda layer to AWS."""
        try:
            with open(zip_path, 'rb') as zip_file:
                response = self.lambda_client.publish_layer_version(
                    LayerName=layer_config.layer_name,
                    Description=layer_config.description,
                    Content={'ZipFile': zip_file.read()},
                    CompatibleRuntimes=[layer_config.python_version],
                    CompatibleArchitectures=[layer_config.architecture]
                )
            
            layer_version_arn = response['LayerVersionArn']
            version = response['Version']
            
            # Extract layer ARN from layer version ARN (remove :version at the end)
            layer_arn = ':'.join(layer_version_arn.split(':')[:-1])
            
            logger.info(f"Published layer: {layer_config.layer_name}")
            logger.info(f"Layer ARN: {layer_arn}")
            logger.info(f"Layer Version ARN: {layer_version_arn}")
            logger.info(f"Version: {version}")
            
            return {
                'layer_arn': layer_arn,
                'version': version,
                'layer_version_arn': layer_version_arn,
                'size': response['Content']['CodeSize']
            }
            
        except Exception as e:
            logger.error(f"Failed to publish layer {layer_config.layer_name}: {e}")
            return None
    
    def delete_layer_version(self, layer_name: str, version: int) -> bool:
        """Delete a specific version of a layer."""
        try:
            self.lambda_client.delete_layer_version(
                LayerName=layer_name,
                VersionNumber=version
            )
            logger.info(f"Deleted layer version: {layer_name}:{version}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete layer version {layer_name}:{version}: {e}")
            return False
    
    def list_layer_versions(self, layer_name: str) -> List[Dict[str, Any]]:
        """List all versions of a layer."""
        try:
            response = self.lambda_client.list_layer_versions(LayerName=layer_name)
            return response.get('LayerVersions', [])
            
        except Exception as e:
            logger.error(f"Failed to list layer versions for {layer_name}: {e}")
            return []
    
    def create_layer(self, layer_config: LayerConfig) -> Optional[Dict[str, Any]]:
        """Create a complete Lambda layer from dependencies."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create requirements file
            requirements_file = self.create_requirements_file(
                layer_config.dependencies, 
                temp_path
            )
            
            # Install dependencies
            install_dir = temp_path / "layer"
            if not self.install_dependencies(requirements_file, install_dir):
                return None
            
            # Create ZIP file
            zip_path = temp_path / f"{layer_config.layer_name}.zip"
            if not self.create_layer_zip(install_dir, zip_path):
                return None
            
            # Publish layer
            return self.publish_layer(layer_config, zip_path)


# Layer configurations for different applications
FASTAPI_LAYER_CONFIG = LayerConfig(
    layer_name=f"{settings.app_name}-fastapi-layer",
    description="Lightweight FastAPI layer for Lambda deployment",
    dependencies=[
        "fastapi==0.104.1",
        "pydantic==2.5.0", 
        "uvicorn==0.24.0",
        "mangum==0.17.0",
        "python-multipart==0.0.6",  # For file uploads
        "starlette==0.27.0"
    ]
)

FILES_API_LAYER_CONFIG = LayerConfig(
    layer_name=f"{settings.app_name}-files-api-layer",
    description="Files API specific dependencies for Lambda",
    dependencies=[
        "fastapi==0.104.1",
        "pydantic==2.5.0",
        "uvicorn==0.24.0", 
        "mangum==0.17.0",
        "python-multipart==0.0.6",
        "boto3==1.34.0",
        "botocore==1.34.0",
        "python-dotenv==1.0.0"
    ]
)

IOT_BACKEND_LAYER_CONFIG = LayerConfig(
    layer_name=f"{settings.app_name}-iot-backend-layer", 
    description="IoT Backend specific dependencies for Lambda",
    dependencies=[
        "fastapi==0.104.1",
        "pydantic==2.5.0",
        "uvicorn==0.24.0",
        "mangum==0.17.0",
        "python-multipart==0.0.6",
        "boto3==1.34.0",
        "botocore==1.34.0",
        "paho-mqtt==1.6.1",  # For MQTT communication
        "python-dotenv==1.0.0"
    ]
)


def create_fastapi_layer() -> Optional[Dict[str, Any]]:
    """Create lightweight FastAPI layer."""
    manager = LayerManager()
    return manager.create_layer(FASTAPI_LAYER_CONFIG)


def create_files_api_layer() -> Optional[Dict[str, Any]]:
    """Create Files API specific layer."""
    manager = LayerManager()
    return manager.create_layer(FILES_API_LAYER_CONFIG)


def create_iot_backend_layer() -> Optional[Dict[str, Any]]:
    """Create IoT Backend specific layer."""
    manager = LayerManager()
    return manager.create_layer(IOT_BACKEND_LAYER_CONFIG)


def create_all_layers() -> Dict[str, Optional[Dict[str, Any]]]:
    """Create all Lambda layers."""
    logger.info("Creating all Lambda layers...")
    
    results = {}
    
    # Create Files API layer
    logger.info("Creating Files API layer...")
    results['files_api'] = create_files_api_layer()
    
    # Create IoT Backend layer  
    logger.info("Creating IoT Backend layer...")
    results['iot_backend'] = create_iot_backend_layer()
    
    # Summary
    successful = sum(1 for result in results.values() if result is not None)
    logger.info(f"Layer creation completed: {successful}/{len(results)} successful")
    
    return results


def cleanup_old_layer_versions(layer_name: str, keep_versions: int = 3) -> int:
    """Clean up old layer versions, keeping only the most recent ones."""
    manager = LayerManager()
    versions = manager.list_layer_versions(layer_name)
    
    if len(versions) <= keep_versions:
        logger.info(f"No cleanup needed for {layer_name}: {len(versions)} versions")
        return 0
    
    # Sort by version number (descending) and delete old ones
    versions.sort(key=lambda v: v['Version'], reverse=True)
    versions_to_delete = versions[keep_versions:]
    
    deleted_count = 0
    for version_info in versions_to_delete:
        version_num = version_info['Version']
        if manager.delete_layer_version(layer_name, version_num):
            deleted_count += 1
    
    logger.info(f"Cleaned up {deleted_count} old versions of {layer_name}")
    return deleted_count


def cleanup_all_layers(keep_versions: int = 3) -> Dict[str, int]:
    """Clean up old versions of all layers."""
    layer_names = [
        FILES_API_LAYER_CONFIG.layer_name,
        IOT_BACKEND_LAYER_CONFIG.layer_name
    ]
    
    results = {}
    for layer_name in layer_names:
        results[layer_name] = cleanup_old_layer_versions(layer_name, keep_versions)
    
    return results


if __name__ == "__main__":
    # Command line interface for testing
    import argparse
    
    parser = argparse.ArgumentParser(description="Lambda Layer Manager")
    parser.add_argument("--create", choices=["files-api", "iot-backend", "all"], 
                       help="Create specific layer or all layers")
    parser.add_argument("--cleanup", action="store_true", 
                       help="Clean up old layer versions")
    parser.add_argument("--list", type=str, metavar="LAYER_NAME",
                       help="List versions of specific layer")
    
    args = parser.parse_args()
    
    if args.create:
        if args.create == "files-api":
            result = create_files_api_layer()
            print(json.dumps(result, indent=2))
        elif args.create == "iot-backend":
            result = create_iot_backend_layer()
            print(json.dumps(result, indent=2))
        elif args.create == "all":
            results = create_all_layers()
            print(json.dumps(results, indent=2))
    
    elif args.cleanup:
        results = cleanup_all_layers()
        print(f"Cleanup results: {results}")
    
    elif args.list:
        manager = LayerManager()
        versions = manager.list_layer_versions(args.list)
        print(json.dumps(versions, indent=2))
    
    else:
        parser.print_help()