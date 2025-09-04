"""
ECS Task Lifecycle Manager
Handles ECS task creation, termination, and scale-to-zero lifecycle for VLM workers.
"""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

from deployment.aws.utils.aws_clients import get_ecs_client, get_sqs_client
from src.files_api.settings import get_settings

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
        # Check deployment mode to determine how to get task count
        deployment_mode = os.environ.get('DEPLOYMENT_MODE', 'local-dev')
        
        if deployment_mode == 'aws-mock':
            # In aws-mock mode, get task count from docker-compose
            return self._get_docker_compose_task_count()
        elif deployment_mode == 'aws-prod':
            # In aws-prod mode, use real ECS API
            return self._get_ecs_task_count()
        else:
            # Default for other modes
            return 1
    
    def _get_docker_compose_task_count(self) -> int:
        """Get task count from docker-compose for aws-mock mode."""
        try:
            import subprocess
            
            compose_file = "deployment/docker/compose/aws-mock.yml"
            
            # Check if compose file exists
            if not os.path.exists(compose_file):
                logger.debug(f"Docker compose file not found: {compose_file}")
                return 0
            
            # Get running containers for vlm-worker service
            result = subprocess.run([
                "docker-compose", "-f", compose_file, "ps", "-q", "vlm-worker"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                # Count non-empty lines (each line is a container ID)
                containers = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
                task_count = len(containers)
                logger.debug(f"Docker-compose task count: {task_count}")
                return task_count
            else:
                logger.debug(f"Docker-compose ps failed: {result.stderr}")
                return 0
                
        except subprocess.TimeoutExpired:
            logger.warning("Docker-compose ps command timed out")
            return 0
        except Exception as e:
            logger.debug(f"Error getting docker-compose task count: {e}")
            return 0
    
    def _get_ecs_task_count(self) -> int:
        """Get task count from real ECS for aws-prod mode."""
        if not self.service_name:
            return 1  # Assume 1 if no service specified
        
        try:
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name,
                services=[self.service_name]
            )
            
            if response['services']:
                task_count = response['services'][0]['runningCount']
                logger.debug(f"ECS task count: {task_count}")
                return task_count
            return 0
            
        except Exception as e:
            logger.warning(f"Could not get ECS service task count: {e}")
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
    
    def simulate_scaling(self, desired_count: int) -> bool:
        """
        Simulate ECS auto-scaling for aws-mock mode.
        
        In aws-mock mode, this simulates scaling by using docker-compose scale command.
        In aws-prod mode, this would trigger actual ECS service scaling.
        
        Args:
            desired_count: Target number of worker replicas
            
        Returns:
            True if scaling simulation succeeded, False otherwise
        """
        try:
            deployment_mode = os.environ.get('DEPLOYMENT_MODE', 'local-dev')
            
            if deployment_mode == 'aws-mock':
                return self._simulate_docker_compose_scaling(desired_count)
            elif deployment_mode == 'aws-prod':
                return self._simulate_ecs_service_scaling(desired_count)
            else:
                logger.warning(f"Scaling simulation not supported in {deployment_mode} mode")
                return False
                
        except Exception as e:
            logger.error(f"Scaling simulation failed: {e}")
            return False
    
    def _simulate_docker_compose_scaling(self, desired_count: int) -> bool:
        """Simulate scaling using docker-compose for aws-mock mode."""
        try:
            import subprocess
            
            compose_file = "deployment/docker/compose/aws-mock.yml"
            
            # Check if compose file exists
            if not os.path.exists(compose_file):
                logger.error(f"Docker compose file not found: {compose_file}")
                return False
            
            logger.info(f"Simulating docker-compose scaling to {desired_count} replicas")
            
            # Use docker-compose to scale the vlm-worker service
            cmd = [
                "docker-compose", 
                "-f", compose_file, 
                "up", "--scale", f"vlm-worker={desired_count}", 
                "-d"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                logger.info(f"✅ Successfully scaled vlm-worker to {desired_count} replicas")
                
                # Update internal tracking
                self._update_scaling_metrics(desired_count)
                return True
            else:
                logger.error(f"Docker-compose scaling failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Docker-compose scaling timed out")
            return False
        except Exception as e:
            logger.error(f"Docker-compose scaling error: {e}")
            return False
    
    def _simulate_ecs_service_scaling(self, desired_count: int) -> bool:
        """Simulate ECS service scaling for aws-prod mode."""
        try:
            if not self.service_name:
                logger.error("Service name not configured for ECS scaling")
                return False
            
            logger.info(f"Simulating ECS service scaling to {desired_count} tasks")
            
            # Update ECS service desired count
            response = self.ecs_client.update_service(
                cluster=self.cluster_name,
                service=self.service_name,
                desiredCount=desired_count
            )
            
            if response['service']['desiredCount'] == desired_count:
                logger.info(f"✅ Successfully updated ECS service desired count to {desired_count}")
                
                # Update internal tracking
                self._update_scaling_metrics(desired_count)
                return True
            else:
                logger.error("ECS service scaling failed - desired count not updated")
                return False
                
        except Exception as e:
            logger.error(f"ECS service scaling error: {e}")
            return False
    
    def _update_scaling_metrics(self, desired_count: int) -> None:
        """Update internal scaling metrics after scaling operation."""
        scaling_event = {
            'timestamp': datetime.now(),
            'desired_count': desired_count,
            'cluster': self.cluster_name,
            'service': self.service_name or 'docker-compose'
        }
        
        # Store scaling event for metrics
        if not hasattr(self, 'scaling_history'):
            self.scaling_history = []
        
        self.scaling_history.append(scaling_event)
        
        # Keep only recent history (last 24 hours)
        cutoff_time = datetime.now() - timedelta(hours=24)
        self.scaling_history = [
            event for event in self.scaling_history 
            if event['timestamp'] > cutoff_time
        ]
        
        logger.debug(f"Updated scaling metrics: {scaling_event}")
    
    def get_scaling_history(self) -> List[Dict[str, Any]]:
        """Get recent scaling history."""
        if not hasattr(self, 'scaling_history'):
            return []
        
        return [
            {
                'timestamp': event['timestamp'].isoformat(),
                'desired_count': event['desired_count'],
                'cluster': event['cluster'],
                'service': event['service']
            }
            for event in self.scaling_history
        ]


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


class AutoScalingSimulator:
    """Simulates ECS auto-scaling behavior for aws-mock mode."""
    
    def __init__(self, queue_url: str, min_instances: int = 0, max_instances: int = 3,
                 scale_up_threshold: int = 1, scale_down_threshold: int = 0,
                 cooldown: int = 300, evaluation_interval: int = 15, evaluation_periods: int = 1,
                 simulation_only: bool = True):
        self.queue_url = queue_url
        self.min_instances = min_instances
        self.max_instances = max_instances
        self.scale_up_threshold = scale_up_threshold
        self.scale_down_threshold = scale_down_threshold
        self.cooldown = cooldown
        self.evaluation_interval = evaluation_interval
        self.evaluation_periods = evaluation_periods
        self.simulation_only = simulation_only
        
        self.task_manager = get_task_manager()
        self.last_scaling_action = None
        self.running = False
        self.current_replicas = min_instances  # Start with minimum replicas
        
        logger.info(f"AutoScaling Simulator initialized:")
        logger.info(f"  Queue URL: {queue_url}")
        logger.info(f"  Instance range: {min_instances}-{max_instances}")
        logger.info(f"  Thresholds: scale_up={scale_up_threshold}, scale_down={scale_down_threshold}")
        logger.info(f"  Cooldown: {cooldown}s, Evaluation: every {evaluation_interval}s")
        logger.info(f"  Mode: {'SIMULATION ONLY' if simulation_only else 'REAL SCALING'}")
    
    def start_simulation(self):
        """Start the auto-scaling simulation loop."""
        self.running = True
        logger.info("🚀 Starting auto-scaling simulation...")
        
        try:
            while self.running:
                self._evaluate_and_scale()
                time.sleep(self.evaluation_interval)
        except KeyboardInterrupt:
            logger.info("🛑 Auto-scaling simulation stopped by user")
        except Exception as e:
            logger.error(f"❌ Auto-scaling simulation error: {e}")
        finally:
            self.running = False
    
    def stop_simulation(self):
        """Stop the auto-scaling simulation."""
        self.running = False
        logger.info("🛑 Auto-scaling simulation stopping...")
    
    def _evaluate_and_scale(self):
        """Evaluate queue metrics and make scaling decisions."""
        try:
            # Check queue status
            scale_info = self.task_manager.check_queue_for_scale_decision(self.queue_url)
            total_messages = scale_info.get('total_messages', 0)
            
            # Use simulated replica count if in simulation mode
            if self.simulation_only:
                running_tasks = self.current_replicas
            else:
                running_tasks = scale_info.get('running_tasks', 1)
            
            # Log queue metrics with more detail
            if total_messages > 0:
                logger.info(f"🔥 QUEUE ACTIVITY: {total_messages} messages detected, {running_tasks} running tasks")
            else:
                logger.info(f"📊 Queue metrics: {total_messages} messages, {running_tasks} running tasks")
            
            # Check cooldown period - but allow immediate scale-up from zero
            if self._in_cooldown() and not (running_tasks == 0 and total_messages > 0):
                remaining = self.cooldown - (datetime.now() - self.last_scaling_action).total_seconds()
                logger.info(f"⏳ In cooldown period, skipping scaling evaluation ({remaining:.1f}s remaining)")
                return
            
            # Make scaling decision
            if total_messages >= self.scale_up_threshold and running_tasks < self.max_instances:
                new_count = min(running_tasks + 1, self.max_instances)
                self._scale_to(new_count, f"Scale up: {total_messages} messages >= {self.scale_up_threshold} threshold")
            elif total_messages <= self.scale_down_threshold and running_tasks > self.min_instances:
                new_count = max(running_tasks - 1, self.min_instances)
                self._scale_to(new_count, f"Scale down: {total_messages} messages <= {self.scale_down_threshold} threshold")
            else:
                logger.info(f"📊 No scaling needed: {total_messages} messages, {running_tasks} tasks")
                
        except Exception as e:
            logger.error(f"❌ Scaling evaluation error: {e}")
    
    def _scale_to(self, desired_count: int, reason: str):
        """Execute scaling action."""
        logger.info(f"🔧 Scaling decision: {reason} -> {desired_count} replicas")
        
        if self.simulation_only:
            # Simulation only - just log what would happen
            if desired_count > self.current_replicas:
                logger.info(f"🚀 SIMULATION: Would SCALE UP from {self.current_replicas} to {desired_count} replicas")
            elif desired_count < self.current_replicas:
                logger.info(f"🔽 SIMULATION: Would SCALE DOWN from {self.current_replicas} to {desired_count} replicas")
            else:
                logger.info(f"🎭 SIMULATION: Would maintain {desired_count} replicas")
            
            self.current_replicas = desired_count
            self.last_scaling_action = datetime.now()
            self._update_scaling_metrics(desired_count)
            logger.info(f"✅ Scaling simulation completed: {desired_count} replicas")
        else:
            # Real scaling
            success = self.task_manager.simulate_scaling(desired_count)
            if success:
                self.current_replicas = desired_count
                self.last_scaling_action = datetime.now()
                logger.info(f"✅ Successfully scaled to {desired_count} replicas")
            else:
                logger.error(f"❌ Failed to scale to {desired_count} replicas")
    
    def _in_cooldown(self) -> bool:
        """Check if we're in cooldown period after last scaling action."""
        if not self.last_scaling_action:
            return False
        
        time_since_last_action = (datetime.now() - self.last_scaling_action).total_seconds()
        in_cooldown = time_since_last_action < self.cooldown
        
        if in_cooldown:
            remaining = self.cooldown - time_since_last_action
            logger.debug(f"⏳ In cooldown: {remaining:.1f}s remaining")
        
        return in_cooldown


if __name__ == "__main__":
    import argparse
    import signal
    import sys
    
    def signal_handler(sig, frame):
        print("\n🛑 Received shutdown signal, stopping auto-scaling simulation...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    parser = argparse.ArgumentParser(description="ECS Auto-scaling Simulator")
    parser.add_argument("--queue-url", required=True, help="SQS queue URL to monitor")
    parser.add_argument("--min-instances", type=int, default=0, help="Minimum number of instances")
    parser.add_argument("--max-instances", type=int, default=3, help="Maximum number of instances")
    parser.add_argument("--scale-up-threshold", type=int, default=1, help="Messages threshold to scale up")
    parser.add_argument("--scale-down-threshold", type=int, default=0, help="Messages threshold to scale down")
    parser.add_argument("--cooldown", type=int, default=300, help="Cooldown period in seconds")
    parser.add_argument("--evaluation-interval", type=int, default=15, help="Evaluation interval in seconds")
    parser.add_argument("--evaluation-periods", type=int, default=1, help="Number of evaluation periods")
    parser.add_argument("--test-mode", action="store_true", help="Run in test mode (single evaluation)")
    parser.add_argument("--real-scaling", action="store_true", help="Enable real scaling (default is simulation only)")
    parser.add_argument("--simulation-only", action="store_true", default=True, help="Simulation only mode (default)")
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    if args.test_mode:
        # Test mode - run example usage and testing
        print("🧪 Running auto-scaler in test mode...")
        
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
        
        # Test scaling simulation
        print("\nTesting scaling simulation...")
        result = manager.simulate_scaling(desired_count=2)
        print(f"Scaling simulation result: {result}")
        
        # Test completion
        print("\nTesting task completion...")
        success = manager.signal_task_completion(success=True, reason="Test completed")
        print(f"Task completion signaled: {success}")
        
        # Final statistics
        final_stats = manager.get_task_statistics()
        print(f"Final statistics: {final_stats}")
        
    else:
        # Production mode - run auto-scaling simulator
        # Default to simulation-only unless --real-scaling is specified
        simulation_only = not args.real_scaling
        
        simulator = AutoScalingSimulator(
            queue_url=args.queue_url,
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            scale_up_threshold=args.scale_up_threshold,
            scale_down_threshold=args.scale_down_threshold,
            cooldown=args.cooldown,
            evaluation_interval=args.evaluation_interval,
            evaluation_periods=args.evaluation_periods,
            simulation_only=simulation_only
        )
        
        simulator.start_simulation()