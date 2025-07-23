"""
Utility functions for API Gateway integration.
"""
import boto3
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

class APIGatewayManager:
    """Manage API Gateway URL retrieval and validation."""

    def __init__(self, region: str = 'us-east-1'):
        self.region = region
        self.apigateway_client = boto3.client('apigateway', region_name=region)

    def get_api_gateway_url(self, api_id: str) -> Optional[str]:
        """
        Get API Gateway URL from API ID.
        
        Args:
            api_id: The API Gateway ID (user will provide this)
            
        Returns:
            Full API Gateway URL or None if not found
        """
        try:
            # Get API Gateway details
            response = self.apigateway_client.get_rest_api(restApiId=api_id)
            api_name = response.get('name', 'unknown')

            # Construct URL: https://{api-id}.execute-api.{region}.amazonaws.com
            api_url = f"https://{api_id}.execute-api.{self.region}.amazonaws.com"

            logger.info(f"Retrieved API Gateway URL: {api_url} (name: {api_name})")
            return api_url

        except Exception as e:
            logger.error(f"Failed to retrieve API Gateway URL for {api_id}: {str(e)}")
            return None

    def validate_api_gateway(self, api_id: str) -> bool:
        """
        Validate that API Gateway exists and is accessible.
        
        Args:
            api_id: The API Gateway ID to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            response = self.apigateway_client.get_rest_api(restApiId=api_id)
            logger.info(f"API Gateway {api_id} validated successfully: {response.get('name')}")
            return True
        except Exception as e:
            logger.error(f"API Gateway validation failed for {api_id}: {str(e)}")
            return False

def get_api_gateway_url_from_env_or_cli() -> Optional[str]:
    """
    Get API Gateway URL from environment variable or CLI detection.
    
    Priority:
    1. API_GATEWAY_URL environment variable
    2. API_GATEWAY_ID environment variable (construct URL)
    3. None (will use default/fallback)
    """
    # Method 1: Direct URL provided
    direct_url = os.environ.get('API_GATEWAY_URL')
    if direct_url:
        logger.info(f"Using direct API Gateway URL from environment: {direct_url}")
        return direct_url

    # Method 2: API ID provided, construct URL
    api_id = os.environ.get('API_GATEWAY_ID')
    if api_id:
        region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        manager = APIGatewayManager(region)
        return manager.get_api_gateway_url(api_id)

    logger.warning("No API Gateway URL or ID found in environment")
    return None