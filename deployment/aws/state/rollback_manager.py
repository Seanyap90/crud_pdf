"""
AWS deployment rollback management.

Provides rollback capabilities for failed deployments.
"""

import json
from typing import Dict, Any, List, Optional
import boto3
from botocore.exceptions import ClientError

from .state_manager import StateManager
from ..utils.aws_clients import get_ecs_client, get_ec2_client, get_efs_client


class RollbackManager:
    """Manages rollback operations for AWS deployments."""
    
    def __init__(self, state_manager: Optional[StateManager] = None):
        self.state_manager = state_manager or StateManager()
    
    def can_rollback(self) -> bool:
        """Check if rollback is possible."""
        state = self.state_manager.state
        return (state.get("status") in ["failed", "deployed"] and
                state.get("resources"))
    
    def create_rollback_plan(self) -> Dict[str, List[Dict[str, Any]]]:
        """Create a rollback plan based on current state."""
        if not self.can_rollback():
            return {"error": "No rollback possible"}
        
        resources = self.state_manager.list_resources()
        plan = {
            "ecs_services": [],
            "ecs_clusters": [],
            "lambda_functions": [],
            "efs_systems": [],
            "ecr_repositories": [],
            "vpcs": []
        }
        
        # ECS Services (stop first)
        for service_name, service_data in resources.get("ecs_service", {}).items():
            plan["ecs_services"].append({
                "action": "delete_service",
                "service_name": service_name,
                "cluster": service_data.get("cluster"),
                "priority": 1
            })
        
        # ECS Clusters
        for cluster_name, cluster_data in resources.get("ecs_cluster", {}).items():
            plan["ecs_clusters"].append({
                "action": "delete_cluster",
                "cluster_name": cluster_name,
                "priority": 2
            })
        
        # Lambda Functions
        for function_name, function_data in resources.get("lambda_function", {}).items():
            plan["lambda_functions"].append({
                "action": "delete_function",
                "function_name": function_name,
                "priority": 3
            })
        
        # EFS Systems
        for efs_id, efs_data in resources.get("efs", {}).items():
            plan["efs_systems"].append({
                "action": "delete_efs",
                "efs_id": efs_id,
                "priority": 4
            })
        
        # ECR Repositories
        for repo_name, repo_data in resources.get("ecr_repository", {}).items():
            plan["ecr_repositories"].append({
                "action": "delete_repository",
                "repository_name": repo_name,
                "priority": 5
            })
        
        # VPCs (last)
        for vpc_id, vpc_data in resources.get("vpc", {}).items():
            plan["vpcs"].append({
                "action": "delete_vpc",
                "vpc_id": vpc_id,
                "priority": 6
            })
        
        return plan
    
    def execute_rollback(self, dry_run: bool = True) -> Dict[str, Any]:
        """Execute rollback plan."""
        plan = self.create_rollback_plan()
        
        if "error" in plan:
            return plan
        
        results = {
            "dry_run": dry_run,
            "success": [],
            "failed": [],
            "skipped": []
        }
        
        # Sort all actions by priority
        all_actions = []
        for resource_type, actions in plan.items():
            for action in actions:
                action["resource_type"] = resource_type
                all_actions.append(action)
        
        all_actions.sort(key=lambda x: x["priority"])
        
        for action in all_actions:
            try:
                if dry_run:
                    results["success"].append({
                        "action": action["action"],
                        "resource": action.get("service_name") or action.get("cluster_name") or 
                                  action.get("function_name") or action.get("efs_id") or
                                  action.get("repository_name") or action.get("vpc_id"),
                        "status": "would_delete"
                    })
                else:
                    success = self._execute_action(action)
                    if success:
                        results["success"].append({
                            "action": action["action"],
                            "resource": action.get("service_name") or action.get("cluster_name") or 
                                      action.get("function_name") or action.get("efs_id") or
                                      action.get("repository_name") or action.get("vpc_id"),
                            "status": "deleted"
                        })
                    else:
                        results["failed"].append({
                            "action": action["action"],
                            "resource": action.get("service_name") or action.get("cluster_name") or 
                                      action.get("function_name") or action.get("efs_id") or
                                      action.get("repository_name") or action.get("vpc_id"),
                            "status": "delete_failed"
                        })
            
            except Exception as e:
                results["failed"].append({
                    "action": action["action"],
                    "resource": action.get("service_name") or action.get("cluster_name") or 
                              action.get("function_name") or action.get("efs_id") or
                              action.get("repository_name") or action.get("vpc_id"),
                    "error": str(e)
                })
        
        return results
    
    def _execute_action(self, action: Dict[str, Any]) -> bool:
        """Execute a single rollback action."""
        try:
            if action["action"] == "delete_service":
                return self._delete_ecs_service(action["service_name"], action["cluster"])
            
            elif action["action"] == "delete_cluster":
                return self._delete_ecs_cluster(action["cluster_name"])
            
            elif action["action"] == "delete_function":
                return self._delete_lambda_function(action["function_name"])
            
            elif action["action"] == "delete_efs":
                return self._delete_efs_system(action["efs_id"])
            
            elif action["action"] == "delete_repository":
                return self._delete_ecr_repository(action["repository_name"])
            
            elif action["action"] == "delete_vpc":
                return self._delete_vpc(action["vpc_id"])
            
            return False
        
        except Exception:
            return False
    
    def _delete_ecs_service(self, service_name: str, cluster: str) -> bool:
        """Delete ECS service."""
        try:
            ecs = get_ecs_client()
            
            # Scale to 0 first
            ecs.update_service(
                cluster=cluster,
                service=service_name,
                desiredCount=0
            )
            
            # Wait for tasks to stop (simplified)
            import time
            time.sleep(30)
            
            # Delete service
            ecs.delete_service(
                cluster=cluster,
                service=service_name
            )
            
            return True
        
        except ClientError:
            return False
    
    def _delete_ecs_cluster(self, cluster_name: str) -> bool:
        """Delete ECS cluster."""
        try:
            ecs = get_ecs_client()
            ecs.delete_cluster(cluster=cluster_name)
            return True
        
        except ClientError:
            return False
    
    def _delete_lambda_function(self, function_name: str) -> bool:
        """Delete Lambda function."""
        try:
            lambda_client = boto3.client('lambda')
            lambda_client.delete_function(FunctionName=function_name)
            return True
        
        except ClientError:
            return False
    
    def _delete_efs_system(self, efs_id: str) -> bool:
        """Delete EFS file system."""
        try:
            efs = get_efs_client()
            
            # Delete mount targets first
            mount_targets = efs.describe_mount_targets(FileSystemId=efs_id)
            for mt in mount_targets["MountTargets"]:
                efs.delete_mount_target(MountTargetId=mt["MountTargetId"])
            
            # Wait for mount targets to be deleted (simplified)
            import time
            time.sleep(60)
            
            # Delete file system
            efs.delete_file_system(FileSystemId=efs_id)
            return True
        
        except ClientError:
            return False
    
    def _delete_ecr_repository(self, repository_name: str) -> bool:
        """Delete ECR repository."""
        try:
            ecr = boto3.client('ecr')
            ecr.delete_repository(
                repositoryName=repository_name,
                force=True  # Delete even if contains images
            )
            return True
        
        except ClientError:
            return False
    
    def _delete_vpc(self, vpc_id: str) -> bool:
        """Delete VPC and associated resources."""
        try:
            ec2 = get_ec2_client()
            
            # This is simplified - in practice, you'd need to delete
            # all associated resources (subnets, route tables, etc.)
            # before deleting the VPC
            
            # Delete VPC
            ec2.delete_vpc(VpcId=vpc_id)
            return True
        
        except ClientError:
            return False


def main():
    """CLI interface for rollback management."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python rollback_manager.py <command> [args]")
        print("Commands:")
        print("  plan        - Show rollback plan")
        print("  execute     - Execute rollback (dry run)")
        print("  execute-real - Execute actual rollback")
        return
    
    manager = RollbackManager()
    command = sys.argv[1]
    
    if command == "plan":
        plan = manager.create_rollback_plan()
        if "error" in plan:
            print(f"Error: {plan['error']}")
            return
        
        print("Rollback Plan:")
        for resource_type, actions in plan.items():
            if actions:
                print(f"\n{resource_type.upper()}:")
                for action in actions:
                    resource_name = (action.get("service_name") or action.get("cluster_name") or 
                                   action.get("function_name") or action.get("efs_id") or
                                   action.get("repository_name") or action.get("vpc_id"))
                    print(f"  Priority {action['priority']}: {action['action']} - {resource_name}")
    
    elif command == "execute":
        results = manager.execute_rollback(dry_run=True)
        print("Rollback Dry Run Results:")
        print(f"Would delete {len(results['success'])} resources")
        for item in results["success"]:
            print(f"  {item['action']}: {item['resource']}")
    
    elif command == "execute-real":
        print("WARNING: This will delete AWS resources!")
        confirm = input("Type 'DELETE' to confirm: ")
        if confirm == "DELETE":
            results = manager.execute_rollback(dry_run=False)
            print("Rollback Results:")
            print(f"Deleted: {len(results['success'])}")
            print(f"Failed: {len(results['failed'])}")
            
            if results["failed"]:
                print("\nFailed deletions:")
                for item in results["failed"]:
                    print(f"  {item['action']}: {item['resource']} - {item.get('error', 'unknown error')}")
        else:
            print("Rollback cancelled")
    
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()