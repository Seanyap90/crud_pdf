"""
Environment Helper for AWS Deployment

This module provides a centralized way to manage environment variables across 
different deployment modes (local-dev, aws-mock, aws-prod). It loads .env files
and provides validation and export capabilities.

Key features:
- Loads environment variables from .env files
- Validates required variables for different deployment modes
- Exports variables for shell usage
- Provides deployment mode detection
"""

import os
import sys
import argparse
from typing import Dict, List, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("Error: python-dotenv is required but not installed")
    print("Please install it with: pip install python-dotenv")
    sys.exit(1)


class EnvironmentHelper:
    """Helper class for managing environment variables across deployment modes."""
    
    def __init__(self, env_file: str = None):
        """
        Initialize EnvironmentHelper.
        
        Args:
            env_file: Optional path to specific .env file to load
        """
        self.project_root = Path(__file__).parent.parent.parent
        self.env_file = env_file
        self.loaded_vars = {}
        
    def load_environment(self) -> Dict[str, str]:
        """
        Load environment variables from .env file.
        
        Returns:
            Dictionary of loaded environment variables
        """
        if self.env_file:
            env_path = self.project_root / self.env_file
            if env_path.exists():
                load_dotenv(env_path)
                print(f"✅ Loaded environment from {self.env_file}")
            else:
                raise FileNotFoundError(f"Environment file not found: {env_path}")
        
        # Capture all environment variables that might have been loaded
        self.loaded_vars = dict(os.environ)
        return self.loaded_vars
    
    def get_deployment_mode(self) -> str:
        """
        Get the current deployment mode.
        
        Returns:
            Deployment mode string (local-dev, aws-mock, aws-prod)
        """
        return os.getenv('DEPLOYMENT_MODE', 'local-dev')
    
    def validate_required_vars(self, required_vars: List[str]) -> bool:
        """
        Validate that all required environment variables are set.
        
        Args:
            required_vars: List of required variable names
            
        Returns:
            True if all variables are set, False otherwise
        """
        missing_vars = []
        
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
            return False
        
        print(f"✅ All required environment variables are set")
        return True
    
    def export_to_shell(self) -> str:
        """
        Export environment variables as shell commands.
        
        Returns:
            String of export commands for shell sourcing
        """
        exports = []
        
        # Get current environment variables
        for key, value in os.environ.items():
            # Only export variables that are likely deployment-related
            if any(prefix in key.upper() for prefix in [
                'DEPLOYMENT', 'AWS', 'S3', 'SQS', 'DATABASE', 'VPC', 'EFS', 'ECR', 
                'MODEL', 'CUDA', 'TRANSFORMERS', 'HF_', 'LAMBDA_FUNCTION'
            ]):
                # Escape quotes and special characters
                escaped_value = value.replace('"', '\\"').replace('$', '\\$')
                exports.append(f'export {key}="{escaped_value}"')
        
        return '\n'.join(exports)
    
    def get_infrastructure_vars(self) -> Dict[str, str]:
        """
        Get infrastructure-specific environment variables.
        
        Returns:
            Dictionary of infrastructure variables
        """
        infra_vars = {}
        
        for key, value in os.environ.items():
            if any(prefix in key.upper() for prefix in [
                'VPC_ID', 'SUBNET_ID', 'SECURITY_GROUP', 'EFS_', 'DATABASE_HOST',
                'LAMBDA_FUNCTION_URL', 'ECR_REGISTRY'
            ]):
                infra_vars[key] = value
        
        return infra_vars

    def get_gpu_deployment_config(self) -> Dict[str, str]:
        """
        Get GPU-specific deployment configuration variables.

        Returns:
            Dictionary of GPU-specific variables
        """
        gpu_vars = {}

        for key, value in os.environ.items():
            if any(prefix in key.upper() for prefix in [
                'MODEL_MEMORY_LIMIT', 'PYTORCH_CUDA_ALLOC_CONF', 'OFFLOAD_TO_CPU',
                'CACHE_IMPLEMENTATION', 'USE_QUANTIZATION', 'CUDA_VISIBLE_DEVICES'
            ]):
                gpu_vars[key] = value

        return gpu_vars

    def export_gpu_variables(self) -> str:
        """
        Export GPU-specific environment variables as shell commands.

        Returns:
            String of export commands for GPU variables
        """
        gpu_vars = self.get_gpu_deployment_config()
        exports = []

        for key, value in gpu_vars.items():
            # Escape quotes and special characters
            escaped_value = value.replace('"', '\\"').replace('$', '\\$')
            exports.append(f'export {key}="{escaped_value}"')

        return '\n'.join(exports)

    @classmethod
    def detect_env_file_from_mode(cls, mode: str = None) -> str:
        """
        Detect the appropriate .env file based on deployment mode.
        
        Args:
            mode: Deployment mode (auto-detected if not provided)
            
        Returns:
            Path to appropriate .env file
        """
        if not mode:
            mode = os.getenv('DEPLOYMENT_MODE', 'local-dev')
        
        env_file_map = {
            'local-dev': '.env.local-dev',
            'aws-mock': '.env.aws-mock', 
            'aws-prod': '.env.aws-prod'
        }
        
        return env_file_map.get(mode, '.env.local-dev')


def main():
    """Command-line interface for environment helper."""
    parser = argparse.ArgumentParser(description='Environment Helper for AWS Deployment')
    
    parser.add_argument('--env-file', help='Path to .env file to load')
    parser.add_argument('--export', action='store_true', help='Export variables for shell sourcing')
    parser.add_argument('--export-gpu', action='store_true', help='Export GPU-specific variables for shell sourcing')
    parser.add_argument('--validate-required-vars', help='Comma-separated list of required variables to validate')
    parser.add_argument('--deployment-mode', help='Set deployment mode and load appropriate .env file')
    
    args = parser.parse_args()
    
    # Determine env file to use
    env_file = args.env_file
    if args.deployment_mode:
        env_file = EnvironmentHelper.detect_env_file_from_mode(args.deployment_mode)
    
    # Initialize helper
    helper = EnvironmentHelper(env_file)
    
    # Load environment
    if env_file:
        try:
            helper.load_environment()
        except FileNotFoundError as e:
            print(f"❌ {e}")
            sys.exit(1)
    
    # Validate required variables
    if args.validate_required_vars:
        required_vars = args.validate_required_vars.split(',')
        if not helper.validate_required_vars(required_vars):
            sys.exit(1)
    
    # Export variables
    if args.export:
        print(helper.export_to_shell())
    elif args.export_gpu:
        print(helper.export_gpu_variables())

    # Show deployment mode
    if not args.export and not args.export_gpu:
        print(f"Deployment Mode: {helper.get_deployment_mode()}")
        gpu_vars = helper.get_gpu_deployment_config()
        if gpu_vars:
            print(f"GPU Variables: {list(gpu_vars.keys())}")


if __name__ == '__main__':
    main()