"""
ECS Task Lifecycle Manager
Handles ECS task creation, termination, and scale-to-zero lifecycle for VLM workers.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

from deployment.aws.utils.aws_clients import get_ecs_client, get_sqs_client
from files_api.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TaskState(Enum):
    """ECS task lifecycle states."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass
class TaskInfo:
    """Information about an ECS task."""
    task_arn: str
    task_id: str
    state: TaskState
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    exit_code: Optional[int] = None
    
    @property
    def runtime_seconds(self) -> Optional[float]:
        """Calculate task runtime in seconds."""
        if self.started_at and self.stopped_at:
            return (self.stopped_at - self.started_at).total_seconds()
        elif self.started_at:
            return (datetime.now() - self.started_at).total_seconds()
        return None
    
    @property
    def is_running(self) -> bool:
        """Check if task is in running state."""
        return self.state == TaskState.RUNNING
    
    @property
    def is_stopped(self) -> bool:
        """Check if task is stopped (completed or failed)."""
        return self.state in [TaskState.STOPPED, TaskState.FAILED]


class AutoScalingManager:
    """Manages ECS task lifecycle for VLM workers."""
    
    def __init__(self, cluster_name: str, service_name: str = None):
        """Initialize ECS task manager.
        
        Args:
            cluster_name: Name of ECS cluster
            service_name: Name of ECS service (optional for standalone tasks)
        """
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.ecs_client = get_ecs_client()
        self.sqs_client = get_sqs_client()
        
        # Track task information
        self.active_tasks: Dict[str, TaskInfo] = {}
        self.task_history: List[TaskInfo] = []
        
        # Configuration
        self.max_task_runtime = timedelta(hours=2)  # Max 2 hours per task
        self.task_cleanup_interval = timedelta(minutes=5)  # Check every 5 minutes
        self.last_cleanup = datetime.now()
        
        # Grace period tracking for scale-to-zero (CLAUDE.md benchmark: < 30 seconds)
        self.queue_empty_since: Optional[datetime] = None
        self.last_queue_check: Optional[datetime] = None
        
        logger.info(f"ECS Task Manager initialized for cluster: {cluster_name}")
    
    def get_current_task_arn(self) -> Optional[str]:
        """Get the ARN of the current ECS task (if running in ECS)."""
        try:
            # Check ECS metadata endpoint v4
            import requests
            metadata_uri = os.environ.get('ECS_CONTAINER_METADATA_URI_V4')
            if metadata_uri:
                response = requests.get(f"{metadata_uri}/task", timeout=2)
                if response.status_code == 200:
                    task_metadata = response.json()
                    return task_metadata.get('TaskARN')
        except Exception as e:
            logger.debug(f"Could not retrieve task ARN from metadata: {e}")
        
        # Fallback: check environment variable (set by ECS)
        return os.environ.get('ECS_TASK_ARN')
    
    def register_current_task(self) -> Optional[TaskInfo]:
        """Register the current task if running in ECS."""
        task_arn = self.get_current_task_arn()
        if task_arn:
            task_info = TaskInfo(
                task_arn=task_arn,
                task_id=self._extract_task_id(task_arn),
                state=TaskState.RUNNING,
                created_at=datetime.now(),
                started_at=datetime.now()
            )
            self.active_tasks[task_arn] = task_info
            logger.info(f"Registered current ECS task: {task_info.task_id}")
            return task_info
        return None
    
    def signal_task_completion(self, success: bool = True, reason: str = None) -> bool:
        """Signal that the current task has completed processing."""
        current_task_arn = self.get_current_task_arn()
        if not current_task_arn:
            logger.warning("Cannot signal completion: not running in ECS task")
            return False
        
        task_info = self.active_tasks.get(current_task_arn)
        if task_info:
            task_info.stopped_at = datetime.now()
            task_info.stop_reason = reason or ("Completed successfully" if success else "Failed")
            task_info.state = TaskState.STOPPED if success else TaskState.FAILED
            
            # Move to history
            self.task_history.append(task_info)
            del self.active_tasks[current_task_arn]
            
            logger.info(f"Task {task_info.task_id} completed: {task_info.stop_reason}")
            return True
        
        return False
    
    def check_queue_for_scale_decision(self, queue_url: str) -> Dict[str, Any]:
        """Check SQS queue to determine if task should continue or terminate."""
        try:
            # Get queue attributes
            response = self.sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
            )
            
            attributes = response.get('Attributes', {})
            visible_messages = int(attributes.get('ApproximateNumberOfMessages', 0))
            in_flight_messages = int(attributes.get('ApproximateNumberOfMessagesNotVisible', 0))
            total_messages = visible_messages + in_flight_messages
            
            # Get current service task count
            running_tasks = self._get_service_running_task_count()
            
            # Calculate if this task should continue
            should_continue = total_messages > 0 or in_flight_messages > 0
            scale_recommendation = self._calculate_scale_recommendation(total_messages, running_tasks)
            
            return {
                'should_continue': should_continue,
                'visible_messages': visible_messages,
                'in_flight_messages': in_flight_messages,
                'total_messages': total_messages,
                'running_tasks': running_tasks,
                'scale_recommendation': scale_recommendation,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to check queue for scale decision: {e}")
            return {
                'should_continue': True,  # Default to continue on error
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    def scale_to_zero(self, queue_url: str, grace_period_seconds: int = 30) -> bool:
        """Determine if the service should scale to zero based on queue state.
        
        Implements CLAUDE.md benchmark: < 30 seconds to detect empty queue
        
        Args:
            queue_url: SQS queue URL to check
            grace_period_seconds: Grace period before scaling to zero (default: 30s per CLAUDE.md)
            
        Returns:
            True if queue has been empty for grace period and should scale to zero
        """
        scale_info = self.check_queue_for_scale_decision(queue_url)
        current_time = datetime.now()
        total_messages = scale_info.get('total_messages', 0)
        
        # Update queue state tracking
        self.last_queue_check = current_time
        
        if total_messages > 0:
            # Queue has messages - reset empty tracking
            self.queue_empty_since = None
            return False
        
        # Queue is empty
        if self.queue_empty_since is None:
            # First time we detected empty queue
            self.queue_empty_since = current_time
            logger.info(f"Queue detected as empty, starting {grace_period_seconds}s grace period")
            return False
        
        # Calculate how long queue has been empty
        empty_duration = (current_time - self.queue_empty_since).total_seconds()
        
        if empty_duration >= grace_period_seconds:
            logger.info(f"Queue empty for {empty_duration:.1f}s (>= {grace_period_seconds}s grace period) - ready to scale to zero")
            return True
        
        logger.debug(f"Queue empty for {empty_duration:.1f}s (< {grace_period_seconds}s grace period) - waiting")
        return False
    
    def initiate_graceful_shutdown(self, reason: str = "Scale to zero") -> bool:
        """Initiate graceful shutdown of the current task."""
        current_task_arn = self.get_current_task_arn()
        if not current_task_arn:
            logger.info("Not running in ECS task, cannot initiate graceful shutdown")
            return False
        
        logger.info(f"Initiating graceful shutdown: {reason}")
        
        # Signal completion
        self.signal_task_completion(success=True, reason=reason)
        
        # In a real implementation, this would:
        # 1. Finish processing current message (if any)
        # 2. Stop polling for new messages
        # 3. Clean up resources
        # 4. Exit gracefully
        
        return True
    
    def monitor_task_health(self) -> Dict[str, Any]:
        """Monitor current task health and runtime."""
        current_task_arn = self.get_current_task_arn()
        if not current_task_arn:
            return {'status': 'not_in_ecs', 'message': 'Not running in ECS task'}
        
        task_info = self.active_tasks.get(current_task_arn)
        if not task_info:
            # Try to register the task if not already registered
            task_info = self.register_current_task()
            if not task_info:
                return {'status': 'unknown', 'message': 'Could not determine task status'}
        
        runtime = task_info.runtime_seconds
        health_status = {
            'status': 'healthy',
            'task_id': task_info.task_id,
            'runtime_seconds': runtime,
            'state': task_info.state.value,
            'started_at': task_info.started_at.isoformat() if task_info.started_at else None
        }
        
        # Check for long-running tasks
        if runtime and runtime > self.max_task_runtime.total_seconds():
            health_status['status'] = 'warning'
            health_status['message'] = f'Task running for {runtime/3600:.1f} hours (max: {self.max_task_runtime.total_seconds()/3600:.1f})'
        
        return health_status
    
    def cleanup_completed_tasks(self) -> int:
        """Clean up tracking for old completed tasks."""
        if datetime.now() - self.last_cleanup < self.task_cleanup_interval:
            return 0
        
        # Keep only recent history (last 24 hours)
        cutoff_time = datetime.now() - timedelta(hours=24)
        initial_count = len(self.task_history)
        
        self.task_history = [
            task for task in self.task_history 
            if task.stopped_at and task.stopped_at > cutoff_time
        ]
        
        cleaned_count = initial_count - len(self.task_history)
        self.last_cleanup = datetime.now()
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} old task records")
        
        return cleaned_count
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """Get statistics about task execution."""
        total_tasks = len(self.task_history) + len(self.active_tasks)
        completed_tasks = len([t for t in self.task_history if t.state == TaskState.STOPPED])
        failed_tasks = len([t for t in self.task_history if t.state == TaskState.FAILED])
        
        # Calculate average runtime for completed tasks
        completed_runtimes = [
            t.runtime_seconds for t in self.task_history 
            if t.state == TaskState.STOPPED and t.runtime_seconds
        ]
        avg_runtime = sum(completed_runtimes) / len(completed_runtimes) if completed_runtimes else 0
        
        return {
            'total_tasks': total_tasks,
            'active_tasks': len(self.active_tasks),
            'completed_tasks': completed_tasks,
            'failed_tasks': failed_tasks,
            'success_rate': completed_tasks / max(completed_tasks + failed_tasks, 1) * 100,
            'average_runtime_seconds': avg_runtime,
            'average_runtime_minutes': avg_runtime / 60 if avg_runtime else 0
        }
    
    def _extract_task_id(self, task_arn: str) -> str:
        """Extract short task ID from full ARN."""
        # ARN format: arn:aws:ecs:region:account:task/cluster-name/task-id
        return task_arn.split('/')[-1] if '/' in task_arn else task_arn
    
    def _get_service_running_task_count(self) -> int:
        """Get the number of running tasks for the service."""
        if not self.service_name:
            return 1  # Assume 1 if no service specified
        
        try:
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name,
                services=[self.service_name]
            )
            
            if response['services']:
                return response['services'][0]['runningCount']
            return 0
            
        except Exception as e:
            logger.warning(f"Could not get service task count: {e}")
            return 1  # Default assumption
    
    def _calculate_scale_recommendation(self, total_messages: int, running_tasks: int) -> str:
        """Calculate scaling recommendation based on queue and current tasks."""
        if total_messages == 0:
            return "scale_to_zero"
        elif total_messages > running_tasks:
            return "scale_out"
        elif total_messages < running_tasks and running_tasks > 1:
            return "scale_in"
        else:
            return "no_change"


def create_ecs_task_manager(cluster_name: str = None, service_name: str = None) -> AutoScalingManager:
    """Factory function to create ECS task manager with defaults."""
    cluster_name = cluster_name or f"{settings.app_name}-cluster"
    service_name = service_name or f"{settings.app_name}-vlm-worker"
    
    return AutoScalingManager(cluster_name, service_name)


# Global task manager instance for easy access
_task_manager_instance: Optional[AutoScalingManager] = None


def get_task_manager() -> AutoScalingManager:
    """Get or create global task manager instance."""
    global _task_manager_instance
    if _task_manager_instance is None:
        _task_manager_instance = create_ecs_task_manager()
    return _task_manager_instance


if __name__ == "__main__":
    # Example usage and testing
    import os
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create task manager
    manager = create_ecs_task_manager("test-cluster", "test-service")
    
    # Test registration
    print("Testing task registration...")
    task_info = manager.register_current_task()
    if task_info:
        print(f"Registered task: {task_info.task_id}")
    else:
        print("Not running in ECS - creating mock task")
        mock_arn = "arn:aws:ecs:us-east-1:123456789012:task/test-cluster/abcd1234"
        os.environ['ECS_TASK_ARN'] = mock_arn
        task_info = manager.register_current_task()
        print(f"Mock task registered: {task_info.task_id}")
    
    # Test health monitoring
    print("\nTesting health monitoring...")
    health = manager.monitor_task_health()
    print(f"Task health: {health}")
    
    # Test statistics
    print("\nTesting statistics...")
    stats = manager.get_task_statistics()
    print(f"Task statistics: {stats}")
    
    # Test completion
    print("\nTesting task completion...")
    success = manager.signal_task_completion(success=True, reason="Test completed")
    print(f"Task completion signaled: {success}")
    
    # Final statistics
    final_stats = manager.get_task_statistics()
    print(f"Final statistics: {final_stats}")