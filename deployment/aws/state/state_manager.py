"""
AWS deployment state management.

Tracks deployment state for idempotent operations and rollback capability.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

from ..utils.aws_clients import get_ecs_client, get_ec2_client, get_efs_client


class StateManager:
    """Manages deployment state for idempotent AWS operations."""
    
    def __init__(self, state_file: str = ".deployment_state.json"):
        self.state_file = state_file
        self.state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """Load deployment state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        
        return {
            "deployment_id": None,
            "created_at": None,
            "last_updated": None,
            "resources": {},
            "configuration": {},
            "status": "not_deployed"
        }
    
    def save_state(self):
        """Save current state to file."""
        self.state["last_updated"] = datetime.utcnow().isoformat()
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save state file: {e}")
    
    def start_deployment(self, deployment_id: str):
        """Start a new deployment."""
        self.state.update({
            "deployment_id": deployment_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "deploying",
            "resources": {},
            "configuration": {}
        })
        self.save_state()
    
    def record_resource(self, resource_type: str, resource_id: str, 
                       resource_data: Dict[str, Any]):
        """Record a created AWS resource."""
        if "resources" not in self.state:
            self.state["resources"] = {}
        
        if resource_type not in self.state["resources"]:
            self.state["resources"][resource_type] = {}
        
        self.state["resources"][resource_type][resource_id] = {
            **resource_data,
            "created_at": datetime.utcnow().isoformat()
        }
        self.save_state()
    
    def get_resource(self, resource_type: str, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a recorded resource."""
        return (self.state.get("resources", {})
                .get(resource_type, {})
                .get(resource_id))
    
    def list_resources(self, resource_type: Optional[str] = None) -> Dict[str, Any]:
        """List all recorded resources, optionally filtered by type."""
        resources = self.state.get("resources", {})
        if resource_type:
            return resources.get(resource_type, {})
        return resources
    
    def check_existing_resources(self) -> Dict[str, List[str]]:
        """Check which recorded resources still exist in AWS."""
        existing = {
            "vpc": [],
            "efs": [],
            "ecs_cluster": [],
            "ecs_service": [],
            "lambda_function": [],
            "ecr_repository": []
        }
        
        try:
            # Check VPCs
            ec2 = get_ec2_client()
            vpcs = self.list_resources("vpc")
            for vpc_id in vpcs.keys():
                try:
                    response = ec2.describe_vpcs(VpcIds=[vpc_id])
                    if response["Vpcs"]:
                        existing["vpc"].append(vpc_id)
                except ClientError:
                    pass
            
            # Check EFS
            efs = get_efs_client()
            efs_systems = self.list_resources("efs")
            for efs_id in efs_systems.keys():
                try:
                    efs.describe_file_systems(FileSystemId=efs_id)
                    existing["efs"].append(efs_id)
                except ClientError:
                    pass
            
            # Check ECS clusters
            ecs = get_ecs_client()
            clusters = self.list_resources("ecs_cluster")
            for cluster_name in clusters.keys():
                try:
                    response = ecs.describe_clusters(clusters=[cluster_name])
                    if response["clusters"] and response["clusters"][0]["status"] == "ACTIVE":
                        existing["ecs_cluster"].append(cluster_name)
                except ClientError:
                    pass
            
            # Check ECS services
            services = self.list_resources("ecs_service")
            for service_name, service_data in services.items():
                cluster_name = service_data.get("cluster")
                if cluster_name:
                    try:
                        response = ecs.describe_services(
                            cluster=cluster_name,
                            services=[service_name]
                        )
                        if (response["services"] and 
                            response["services"][0]["status"] == "ACTIVE"):
                            existing["ecs_service"].append(service_name)
                    except ClientError:
                        pass
            
            # Check Lambda functions
            lambda_client = boto3.client('lambda')
            functions = self.list_resources("lambda_function")
            for function_name in functions.keys():
                try:
                    lambda_client.get_function(FunctionName=function_name)
                    existing["lambda_function"].append(function_name)
                except ClientError:
                    pass
            
            # Check ECR repositories
            ecr = boto3.client('ecr')
            repos = self.list_resources("ecr_repository")
            for repo_name in repos.keys():
                try:
                    ecr.describe_repositories(repositoryNames=[repo_name])
                    existing["ecr_repository"].append(repo_name)
                except ClientError:
                    pass
                    
        except Exception as e:
            print(f"Warning: Error checking existing resources: {e}")
        
        return existing
    
    def set_configuration(self, key: str, value: Any):
        """Set a configuration value."""
        if "configuration" not in self.state:
            self.state["configuration"] = {}
        
        self.state["configuration"][key] = value
        self.save_state()
    
    def get_configuration(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self.state.get("configuration", {}).get(key, default)
    
    def mark_deployment_complete(self):
        """Mark deployment as complete."""
        self.state["status"] = "deployed"
        self.save_state()
    
    def mark_deployment_failed(self, error: str):
        """Mark deployment as failed."""
        self.state["status"] = "failed"
        self.state["error"] = error
        self.save_state()
    
    def clear_state(self):
        """Clear all deployment state."""
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.state = self._load_state()
    
    def export_env_file(self, env_file: str = ".env.aws-prod"):
        """Export configuration to environment file."""
        config = self.state.get("configuration", {})
        resources = self.state.get("resources", {})
        
        env_lines = [
            "# AWS Production Environment Configuration",
            f"# Generated on {datetime.utcnow().isoformat()}",
            f"# Deployment ID: {self.state.get('deployment_id', 'unknown')}",
            "",
            "# Deployment Mode",
            "DEPLOYMENT_MODE=aws-prod",
            ""
        ]
        
        # Add VPC configuration
        vpcs = resources.get("vpc", {})
        if vpcs:
            vpc_id = list(vpcs.keys())[0]
            env_lines.extend([
                "# VPC Configuration",
                f"VPC_ID={vpc_id}",
                ""
            ])
        
        # Add EFS configuration
        efs_systems = resources.get("efs", {})
        if efs_systems:
            efs_id = list(efs_systems.keys())[0]
            env_lines.extend([
                "# EFS Configuration",
                f"EFS_FILE_SYSTEM_ID={efs_id}",
                ""
            ])
        
        # Add ECS configuration
        clusters = resources.get("ecs_cluster", {})
        if clusters:
            cluster_name = list(clusters.keys())[0]
            env_lines.extend([
                "# ECS Configuration",
                f"ECS_CLUSTER_NAME={cluster_name}",
                ""
            ])
        
        # Add Lambda configuration
        functions = resources.get("lambda_function", {})
        if functions:
            function_name = list(functions.keys())[0]
            env_lines.extend([
                "# Lambda Configuration",
                f"LAMBDA_FUNCTION_NAME={function_name}",
                ""
            ])
        
        # Add custom configuration
        if config:
            env_lines.extend([
                "# Custom Configuration",
                *[f"{k}={v}" for k, v in config.items()],
                ""
            ])
        
        try:
            with open(env_file, 'w') as f:
                f.write('\n'.join(env_lines))
            print(f"Configuration exported to {env_file}")
        except IOError as e:
            print(f"Warning: Could not write environment file: {e}")


def main():
    """CLI interface for state management."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python state_manager.py <command> [args]")
        print("Commands:")
        print("  status              - Show deployment status")
        print("  check-existing      - Check which resources still exist")
        print("  export-env [file]   - Export configuration to env file")
        print("  clear               - Clear deployment state")
        return
    
    manager = StateManager()
    command = sys.argv[1]
    
    if command == "status":
        print(f"Deployment Status: {manager.state.get('status', 'unknown')}")
        print(f"Deployment ID: {manager.state.get('deployment_id', 'none')}")
        print(f"Created: {manager.state.get('created_at', 'unknown')}")
        print(f"Last Updated: {manager.state.get('last_updated', 'unknown')}")
        
        resources = manager.list_resources()
        if resources:
            print("\nRecorded Resources:")
            for resource_type, items in resources.items():
                print(f"  {resource_type}: {len(items)} items")
    
    elif command == "check-existing":
        existing = manager.check_existing_resources()
        print("Existing AWS Resources:")
        for resource_type, items in existing.items():
            if items:
                print(f"  {resource_type}: {', '.join(items)}")
    
    elif command == "export-env":
        env_file = sys.argv[2] if len(sys.argv) > 2 else ".env.aws-prod"
        manager.export_env_file(env_file)
    
    elif command == "clear":
        manager.clear_state()
        print("Deployment state cleared")
    
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()