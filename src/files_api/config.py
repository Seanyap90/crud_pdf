# config.py
from files_api.settings import get_settings

# Get settings instance once
_settings = get_settings()


class Config:
    """
    Global Configuration for the application.
    
    This class provides backwards compatibility for code expecting
    the Config class interface. All values come from settings.py.
    """
    
    def __init__(self):
        # Dynamically set attributes from settings
        self._refresh_from_settings()
    
    def _refresh_from_settings(self):
        """Refresh configuration values from settings."""
        # General Settings
        self.APP_NAME = _settings.app_name
        
        # S3 Configuration
        self.AWS_ACCESS_KEY_ID = _settings.aws_access_key_id
        self.AWS_SECRET_ACCESS_KEY = _settings.aws_secret_access_key
        self.S3_BUCKET_NAME = _settings.s3_bucket_name
        self.AWS_ENDPOINT_URL = _settings.aws_endpoint_url
        
        # Queue Configuration
        self.QUEUE_TYPE = _settings.deployment_mode  # Maps to deployment_mode
        self.SQS_QUEUE_URL = _settings.sqs_queue_url
        self.SQS_QUEUE_NAME = _settings.sqs_queue_name
        
        # AWS Region
        self.AWS_DEFAULT_REGION = _settings.aws_region
        
        # Model Configuration
        self.MODEL_MEMORY_LIMIT = _settings.model_memory_limit
        self.DISABLE_DUPLICATE_LOADING = _settings.disable_duplicate_loading
        
        # Storage
        self.STORAGE_DIR = _settings.storage_dir
        
        # ECR/IAM
        self.ECR_REPO_NAME = _settings.ecr_repo_name
        self.IAM_ROLE_NAME = _settings.iam_role_name
        self.IAM_INSTANCE_PROFILE = _settings.iam_instance_profile
    
    # For code that expects EXEC_MODE attribute
    @property
    def EXEC_MODE(self):
        """Legacy alias for QUEUE_TYPE/deployment_mode."""
        return _settings.deployment_mode
    
    def reload(self):
        """
        Reload configuration from settings.
        Useful if environment variables change at runtime.
        """
        global _settings
        from files_api.settings import get_settings
        get_settings.cache_clear()  # Clear the cache
        _settings = get_settings()
        self._refresh_from_settings()


# Create global instance for backwards compatibility
config = Config()

# For direct imports like `from files_api.config import AWS_ENDPOINT_URL`
# Create module-level variables that reference the config instance
AWS_ACCESS_KEY_ID = config.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = config.AWS_SECRET_ACCESS_KEY
S3_BUCKET_NAME = config.S3_BUCKET_NAME
AWS_ENDPOINT_URL = config.AWS_ENDPOINT_URL
QUEUE_TYPE = config.QUEUE_TYPE
SQS_QUEUE_URL = config.SQS_QUEUE_URL
SQS_QUEUE_NAME = config.SQS_QUEUE_NAME
AWS_DEFAULT_REGION = config.AWS_DEFAULT_REGION
MODEL_MEMORY_LIMIT = config.MODEL_MEMORY_LIMIT
DISABLE_DUPLICATE_LOADING = config.DISABLE_DUPLICATE_LOADING
STORAGE_DIR = config.STORAGE_DIR
ECR_REPO_NAME = config.ECR_REPO_NAME
IAM_ROLE_NAME = config.IAM_ROLE_NAME
IAM_INSTANCE_PROFILE = config.IAM_INSTANCE_PROFILE
APP_NAME = config.APP_NAME

# Function to get current config (for dynamic access)
def get_config():
    """Get the current configuration object."""
    return config