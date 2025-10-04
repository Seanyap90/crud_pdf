# src/files_api/settings.py
import os
from typing import Optional, Dict, Any
from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Single source of truth for all application settings.
    
    Configuration precedence:
    1. Environment variables (highest priority)
    2. .env file (if exists)
    3. Default values in this class (lowest priority)
    
    Usage:
        from files_api.settings import get_settings
        settings = get_settings()
        bucket_name = settings.s3_bucket_name
    """
    
    # Application Settings
    app_name: str = Field(
        default="fastapi-app",
        description="Application name"
    )
    
    # Deployment Mode
    deployment_mode: str = Field(
        default="local-dev",
        description="Deployment mode: local-dev, aws-mock, or aws-prod"
    )
    
    # AWS Core Settings
    aws_region: str = Field(
        default="us-east-1",
        alias="AWS_DEFAULT_REGION"
    )
    
    aws_access_key_id: Optional[str] = Field(
        default=None,
        alias="AWS_ACCESS_KEY_ID"
    )
    
    aws_secret_access_key: Optional[str] = Field(
        default=None,
        alias="AWS_SECRET_ACCESS_KEY"
    )
    
    aws_endpoint_url: Optional[str] = Field(
        default=None,
        alias="AWS_ENDPOINT_URL"
    )
    
    aws_account_id: Optional[str] = Field(
        default=None,
        alias="AWS_ACCOUNT_ID",
        description="AWS Account ID (auto-detected if not provided)"
    )
    
    # S3 Configuration
    s3_bucket_name: str = Field(
        default="rag-pdf-storage",
        description="S3 bucket for PDF storage"
    )
    
    # SQS Configuration
    sqs_queue_name: str = Field(
        default="rag-task-queue",
        description="SQS queue name"
    )
    
    sqs_queue_url: Optional[str] = Field(
        default=None,
        alias="SQS_QUEUE_URL",
        description="Full SQS queue URL"
    )
    
    # ECR Configuration
    ecr_repo_name: str = Field(
        default="rag-worker",
        description="ECR repository name"
    )
    
    # IAM Configuration
    iam_role_name: str = Field(
        default="rag-worker-role",
        description="IAM role name"
    )
    
    iam_instance_profile: str = Field(
        default="rag-worker-profile",
        description="IAM instance profile name"
    )
    
    # GPU Instance Configuration
    primary_instance_type: str = Field(
        default="g4dn.xlarge",
        description="Primary GPU instance type for cost optimization"
    )
    
    # Model Configuration
    model_memory_limit: str = Field(
        default="12GiB",
        description="Memory limit for models (optimized for T4 GPU)"
    )
    
    disable_duplicate_loading: bool = Field(
        default=True,
        description="Disable duplicate model loading"
    )
    
    # Storage Configuration
    storage_dir: str = Field(
        default="storage",
        description="Local storage directory"
    )
    
    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )
    
    # Backwards compatibility properties
    @property
    def queue_type(self) -> str:
        """Alias for deployment_mode for backwards compatibility with QUEUE_TYPE."""
        return self.deployment_mode
    
    @property
    def exec_mode(self) -> str:
        """Alias for deployment_mode for backwards compatibility with EXEC_MODE."""
        return self.deployment_mode
    
    @validator('deployment_mode', pre=True)
    def normalize_deployment_mode(cls, v):
        """Normalize deployment mode values for backwards compatibility."""
        if v:
            # Map old values to new ones
            mode_mapping = {
                "local-mock": "local-dev",
                "cloud": "aws-prod",
                "QUEUE_TYPE": "deployment_mode"  # If someone passes the env var name
            }
            return mode_mapping.get(v, v)
        return v
    
    @validator('deployment_mode')
    def validate_deployment_mode(cls, v):
        """Validate deployment mode is one of the allowed values."""
        valid_modes = ["local-dev", "aws-mock", "aws-prod"]
        if v not in valid_modes:
            raise ValueError(f"Invalid deployment_mode: {v}. Must be one of {valid_modes}")
        return v
    
    @validator('aws_endpoint_url', always=True)
    def set_endpoint_url_based_on_mode(cls, v, values):
        """Auto-set endpoint URL based on deployment mode if not explicitly provided."""
        if v is None and 'deployment_mode' in values:
            mode = values['deployment_mode']
            if mode in ["local-dev", "aws-mock"]:
                return "http://localhost:5000"
        return v
    
    @validator('aws_access_key_id', 'aws_secret_access_key', always=True)
    def set_mock_credentials_for_local_modes(cls, v, values):
        """Auto-set mock credentials for local modes if not provided."""
        if v is None and 'deployment_mode' in values:
            mode = values['deployment_mode']
            if mode in ["local-dev", "aws-mock"]:
                return "mock"
            elif mode == "aws-prod":
                # In production, return None to let IAM execution role handle auth
                return None
        return v
    
    @property
    def account_id(self) -> str:
        """Get AWS account ID with auto-detection fallback."""
        if self.aws_account_id:
            return self.aws_account_id
        
        # Auto-detect account ID for production modes
        if self.deployment_mode in ["aws-prod"]:
            try:
                # Try to use centralized client manager for mock/dev modes
                try:
                    from deployment.aws.utils.aws_clients import AWSClientManager
                    sts_client = AWSClientManager().get_client('sts')
                    return sts_client.get_caller_identity()['Account']
                except ImportError:
                    # Fallback for Lambda environment where deployment module isn't available
                    import boto3
                    sts_client = boto3.client('sts')
                    return sts_client.get_caller_identity()['Account']
            except Exception:
                # Fallback for local development
                return "123456789012"
        
        # Mock account ID for development modes
        return "123456789012"
    
    @property
    def ecr_registry(self) -> str:
        """Get ECR registry URL."""
        return f"{self.account_id}.dkr.ecr.{self.aws_region}.amazonaws.com"
    
    @property
    def regional_config(self) -> Dict[str, Any]:
        """GPU optimization configuration per region."""
        return {
            'us-east-1': {
                'instance_types': ['g4dn.xlarge', 'g4dn.2xlarge', 'g4dn.large'],
                'gpu_memory_limit': '12GiB'  # Safe for 16GB T4
            },
            'us-west-2': {
                'instance_types': ['g4dn.xlarge', 'g4dn.2xlarge'],
                'gpu_memory_limit': '12GiB'
            }
        }
    
    @validator('sqs_queue_url', always=True)
    def generate_queue_url_if_needed(cls, v, values):
        """Generate SQS queue URL if not provided."""
        if v is None and all(k in values for k in ['deployment_mode', 'sqs_queue_name']):
            mode = values['deployment_mode']
            queue_name = values['sqs_queue_name']
            
            if mode in ["local-dev", "aws-mock"]:
                endpoint = values.get('aws_endpoint_url', 'http://localhost:5000')
                # For moto, use a simplified format without account number
                return f"{endpoint}/queue/{queue_name}"
            # For aws-prod, the URL will be set after queue creation
        return v

    def export_environment_variables(self) -> None:
        """Export configuration as environment variables.
        
        This replaces the old get_settings_as_env helper function from run.sh
        """
        env_vars = {
            'DEPLOYMENT_MODE': self.deployment_mode,
            'S3_BUCKET_NAME': self.s3_bucket_name,
            'SQS_QUEUE_NAME': self.sqs_queue_name,
            'AWS_DEFAULT_REGION': self.aws_region,
            'SQS_QUEUE_URL': self.sqs_queue_url or '',
            'MODEL_MEMORY_LIMIT': self.model_memory_limit,
            'DISABLE_DUPLICATE_LOADING': str(self.disable_duplicate_loading).lower(),
            'LOG_LEVEL': self.log_level,
        }
        
        # Only set AWS credentials and endpoint for local/mock modes
        if self.deployment_mode in ['local-dev', 'aws-mock']:
            env_vars.update({
                'AWS_ENDPOINT_URL': self.aws_endpoint_url or '',
                'AWS_ACCESS_KEY_ID': self.aws_access_key_id or 'mock',
                'AWS_SECRET_ACCESS_KEY': self.aws_secret_access_key or 'mock',
            })
        # For aws-prod, let Lambda execution role handle credentials automatically
        
        for key, value in env_vars.items():
            if value:  # Only set non-empty values
                os.environ[key] = str(value)

    def get_environment_dict(self) -> dict:
        """Get configuration as a dictionary suitable for docker-compose or subprocess.
        
        Returns:
            Dictionary of environment variables
        """
        env_dict = {
            'DEPLOYMENT_MODE': self.deployment_mode,
            'S3_BUCKET_NAME': self.s3_bucket_name,
            'SQS_QUEUE_NAME': self.sqs_queue_name,
            'AWS_DEFAULT_REGION': self.aws_region,
            'SQS_QUEUE_URL': self.sqs_queue_url or '',
            'MODEL_MEMORY_LIMIT': self.model_memory_limit,
            'DISABLE_DUPLICATE_LOADING': str(self.disable_duplicate_loading).lower(),
            'LOG_LEVEL': self.log_level,
        }
        
        # Only include AWS credentials for local/mock modes
        if self.deployment_mode in ['local-dev', 'aws-mock']:
            env_dict.update({
                'AWS_ENDPOINT_URL': self.aws_endpoint_url or '',
                'AWS_ACCESS_KEY_ID': self.aws_access_key_id or 'mock',
                'AWS_SECRET_ACCESS_KEY': self.aws_secret_access_key or 'mock',
            })
        
        return env_dict

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=(".env", ".env.local-dev", ".env.aws-mock", ".env.aws-prod"),  # Support new env files
        env_file_encoding="utf-8",
        extra="allow",  # Allow extra fields for backwards compatibility
        # Allow reading from environment variables with different names
        env_aliases={
            "deployment_mode": ["DEPLOYMENT_MODE", "QUEUE_TYPE", "EXEC_MODE"],
            "app_name": ["APP_NAME"],
        }
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    This ensures we only create one Settings instance per process.
    """
    return Settings()


# For backwards compatibility - module-level access
settings = get_settings()

# Export commonly used values for convenience
S3_BUCKET_NAME = settings.s3_bucket_name
SQS_QUEUE_NAME = settings.sqs_queue_name
AWS_DEFAULT_REGION = settings.aws_region


def get_settings_with_env_helper(env_file: str = None) -> Settings:
    """
    Get settings instance with env_helper integration.
    
    This function loads the specified .env file using the env_helper
    before creating the Settings instance, ensuring environment
    variables are properly loaded.
    
    Args:
        env_file: Path to .env file (e.g., '.env.aws-prod')
        
    Returns:
        Settings instance with loaded environment
    """
    if env_file:
        try:
            # Import env_helper dynamically to avoid circular imports
            from files_api.env_helper import EnvironmentHelper
            helper = EnvironmentHelper(env_file)
            helper.load_environment()
        except (ImportError, FileNotFoundError) as e:
            print(f"Warning: Could not load env_helper or {env_file}: {e}")
            print("Falling back to default settings loading")
    
    # Clear the settings cache and return fresh instance
    get_settings.cache_clear()
    return get_settings()