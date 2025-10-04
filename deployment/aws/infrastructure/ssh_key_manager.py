"""
SSH Key Manager for AMI Instance Access

Creates and manages SSH key pairs for accessing AMI build instances and
production ECS instances launched from custom AMIs.
"""

import logging
import os
import stat
from typing import Dict, Any, Optional
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

class SSHKeyManager:
    """Manage SSH key pairs for AMI instances."""
    
    def __init__(self, region: str = None):
        """Initialize SSH key manager."""
        self.settings = get_settings()
        self.region = region or self.settings.aws_region
        self.ec2 = boto3.client('ec2', region_name=self.region)
        
        # Configuration
        self.app_name = self.settings.app_name or "fastapi-app"
        self.key_name = f"{self.app_name}-vlm-key"
        self.local_key_dir = Path.home() / ".ssh"
        self.private_key_path = self.local_key_dir / f"{self.key_name}.pem"
        
        # Ensure local SSH directory exists
        self.local_key_dir.mkdir(mode=0o700, exist_ok=True)
        
        logger.info(f"SSH Key Manager initialized for key: {self.key_name}")
    
    def key_pair_exists_on_aws(self) -> bool:
        """Check if key pair exists on AWS."""
        try:
            response = self.ec2.describe_key_pairs(KeyNames=[self.key_name])
            return len(response['KeyPairs']) > 0
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
                return False
            raise
    
    def local_key_exists(self) -> bool:
        """Check if local private key file exists."""
        return self.private_key_path.exists()
    
    def create_key_pair_on_aws(self) -> str:
        """Create new key pair on AWS and return private key material."""
        try:
            logger.info(f"Creating key pair on AWS: {self.key_name}")
            
            response = self.ec2.create_key_pair(
                KeyName=self.key_name,
                KeyType='rsa',
                TagSpecifications=[
                    {
                        'ResourceType': 'key-pair',
                        'Tags': [
                            {'Key': 'Name', 'Value': self.key_name},
                            {'Key': 'Purpose', 'Value': 'vlm-ami-access'},
                            {'Key': 'Project', 'Value': self.app_name}
                        ]
                    }
                ]
            )
            
            private_key_material = response['KeyMaterial']
            logger.info(f"Key pair created successfully on AWS: {self.key_name}")
            return private_key_material
            
        except Exception as e:
            logger.error(f"Failed to create key pair on AWS: {e}")
            raise
    
    def save_private_key_locally(self, private_key_material: str) -> None:
        """Save private key to local filesystem with proper permissions."""
        try:
            logger.info(f"Saving private key to: {self.private_key_path}")
            
            # Write private key file
            with open(self.private_key_path, 'w') as f:
                f.write(private_key_material)
            
            # Set proper permissions (read-only for owner)
            os.chmod(self.private_key_path, stat.S_IRUSR)
            
            logger.info("Private key saved successfully with permissions 600")
            
        except Exception as e:
            logger.error(f"Failed to save private key locally: {e}")
            raise
    
    def delete_key_pair_from_aws(self) -> None:
        """Delete key pair from AWS."""
        try:
            logger.info(f"Deleting key pair from AWS: {self.key_name}")
            self.ec2.delete_key_pair(KeyName=self.key_name)
            logger.info("Key pair deleted from AWS")
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
                logger.info("Key pair not found on AWS (already deleted)")
            else:
                logger.error(f"Failed to delete key pair from AWS: {e}")
                raise
    
    def delete_local_key(self) -> None:
        """Delete local private key file."""
        try:
            if self.private_key_path.exists():
                logger.info(f"Deleting local private key: {self.private_key_path}")
                self.private_key_path.unlink()
                logger.info("Local private key deleted")
            else:
                logger.info("Local private key not found (already deleted)")
        except Exception as e:
            logger.error(f"Failed to delete local private key: {e}")
            raise
    
    def ensure_ssh_key(self) -> str:
        """Ensure SSH key pair exists both on AWS and locally."""
        try:
            aws_exists = self.key_pair_exists_on_aws()
            local_exists = self.local_key_exists()
            
            if aws_exists and local_exists:
                logger.info("SSH key pair already exists on AWS and locally")
                return self.key_name
            
            if aws_exists and not local_exists:
                logger.warning("Key exists on AWS but not locally - this is not recoverable")
                logger.warning("Deleting AWS key and creating new pair")
                self.delete_key_pair_from_aws()
                aws_exists = False
            
            if not aws_exists and local_exists:
                logger.warning("Local key exists but not on AWS - removing local key")
                self.delete_local_key()
                local_exists = False
            
            # Create new key pair
            if not aws_exists and not local_exists:
                logger.info("Creating new SSH key pair")
                private_key_material = self.create_key_pair_on_aws()
                self.save_private_key_locally(private_key_material)
                
                logger.info(f"SSH key pair created successfully: {self.key_name}")
                logger.info(f"Private key saved to: {self.private_key_path}")
                return self.key_name
            
        except Exception as e:
            logger.error(f"Failed to ensure SSH key: {e}")
            raise
    
    def get_ssh_connection_command(self, instance_ip: str, username: str = "ec2-user") -> str:
        """Generate SSH connection command for an instance."""
        return f"ssh -i {self.private_key_path} {username}@{instance_ip}"
    
    def get_key_info(self) -> Dict[str, Any]:
        """Get information about the SSH key."""
        return {
            'key_name': self.key_name,
            'private_key_path': str(self.private_key_path),
            'aws_exists': self.key_pair_exists_on_aws(),
            'local_exists': self.local_key_exists(),
            'local_permissions': oct(self.private_key_path.stat().st_mode)[-3:] if self.local_key_exists() else None
        }
    
    def cleanup_keys(self) -> Dict[str, bool]:
        """Clean up both AWS and local keys."""
        results = {'aws_deleted': False, 'local_deleted': False}
        
        try:
            if self.key_pair_exists_on_aws():
                self.delete_key_pair_from_aws()
                results['aws_deleted'] = True
        except Exception as e:
            logger.error(f"Failed to delete AWS key: {e}")
        
        try:
            if self.local_key_exists():
                self.delete_local_key()
                results['local_deleted'] = True
        except Exception as e:
            logger.error(f"Failed to delete local key: {e}")
        
        return results


def create_ssh_key(region: str = None) -> Dict[str, Any]:
    """Convenience function to create SSH key pair."""
    manager = SSHKeyManager(region=region)
    key_name = manager.ensure_ssh_key()
    return {
        'key_name': key_name,
        'key_info': manager.get_key_info()
    }


def get_ssh_key_info(region: str = None) -> Dict[str, Any]:
    """Get information about existing SSH key."""
    manager = SSHKeyManager(region=region)
    return manager.get_key_info()


if __name__ == "__main__":
    # Test SSH key management
    import sys
    import json
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        region = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        command = "create"
        region = None
    
    manager = SSHKeyManager(region=region)
    
    if command == "create":
        key_name = manager.ensure_ssh_key()
        info = manager.get_key_info()
        result = {'status': 'success', 'key_name': key_name, 'info': info}
        print(json.dumps(result, indent=2))
    
    elif command == "info":
        info = manager.get_key_info()
        print(json.dumps(info, indent=2))
    
    elif command == "cleanup":
        results = manager.cleanup_keys()
        print(json.dumps(results, indent=2))
    
    else:
        print("Usage: ssh_key_manager.py [create|info|cleanup] [region]")