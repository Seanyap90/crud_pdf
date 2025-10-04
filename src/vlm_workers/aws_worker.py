"""AWS-specific worker implementation for the VLM+RAG pipeline."""
import logging
import time
import os
import gc
import torch
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from vlm_workers.worker import Worker
from deployment.aws.utils.aws_clients import get_cloudwatch_client
# Auto-scaler removed in AMI integration - using EventBridge Lambda scaling instead
from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

# Observer pattern implementation for monitoring
class WorkerObserver:
    """Base observer interface for worker event notifications."""
    
    def on_task_start(self, task: Dict[str, Any]) -> None:
        """Called when a task starts processing."""
        pass
    
    def on_task_complete(self, task: Dict[str, Any], duration: float, success: bool) -> None:
        """Called when a task completes or fails."""
        pass
    
    def on_worker_error(self, error: Exception) -> None:
        """Called when a worker error occurs."""
        pass

class CloudWatchObserver(WorkerObserver):
    """CloudWatch implementation of WorkerObserver for metrics."""
    
    def __init__(self, namespace: str = "FilesAPI/Worker"):
        """Initialize with CloudWatch namespace."""
        self.namespace = namespace
        self.cloudwatch = get_cloudwatch_client()
    
    def on_task_start(self, task: Dict[str, Any]) -> None:
        """Record task start metric."""
        task_type = task.get('task_type', 'unknown')
        try:
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[
                    {
                        'MetricName': 'TaskStarted',
                        'Dimensions': [
                            {'Name': 'TaskType', 'Value': task_type}
                        ],
                        'Value': 1,
                        'Unit': 'Count'
                    }
                ]
            )
        except Exception as e:
            logger.warning(f"Failed to publish task start metric: {str(e)}")
    
    def on_task_complete(self, task: Dict[str, Any], duration: float, success: bool) -> None:
        """Record task completion metrics."""
        task_type = task.get('task_type', 'unknown')
        
        metrics = [
            {
                'MetricName': 'TaskDuration',
                'Dimensions': [
                    {'Name': 'TaskType', 'Value': task_type}
                ],
                'Value': duration,
                'Unit': 'Seconds'
            },
            {
                'MetricName': 'TaskSuccess' if success else 'TaskFailure',
                'Dimensions': [
                    {'Name': 'TaskType', 'Value': task_type}
                ],
                'Value': 1,
                'Unit': 'Count'
            }
        ]
        
        try:
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=metrics
            )
        except Exception as e:
            logger.warning(f"Failed to publish task completion metrics: {str(e)}")
    
    def on_worker_error(self, error: Exception) -> None:
        """Record worker error metric."""
        try:
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[
                    {
                        'MetricName': 'WorkerError',
                        'Value': 1,
                        'Unit': 'Count'
                    }
                ]
            )
        except Exception as e:
            logger.warning(f"Failed to publish worker error metric: {str(e)}")

class LoggingObserver(WorkerObserver):
    """Simple logging implementation of WorkerObserver."""
    
    def on_task_start(self, task: Dict[str, Any]) -> None:
        """Log task start."""
        task_type = task.get('task_type', 'unknown')
        task_id = self._get_task_id(task)
        logger.info(f"[Task {task_id}] Started processing {task_type} task")
    
    def on_task_complete(self, task: Dict[str, Any], duration: float, success: bool) -> None:
        """Log task completion."""
        task_type = task.get('task_type', 'unknown')
        task_id = self._get_task_id(task)
        status = "successfully" if success else "with failure"
        logger.info(f"[Task {task_id}] Completed {task_type} task {status} in {duration:.2f}s")
    
    def on_worker_error(self, error: Exception) -> None:
        """Log worker error."""
        logger.error(f"Worker error: {str(error)}")
    
    def _get_task_id(self, task: Dict[str, Any]) -> str:
        """Extract a task identifier for logging."""
        if task.get('task_type') == 'process_invoice' and 'file_info' in task:
            return f"{task['file_info'].get('invoice_id', 'unknown')}"
        return str(id(task))


# ECSTaskObserver removed - AMI-based scaling uses EventBridge Lambda functions instead of task managers

class AWSWorker(Worker):
    """AWS-specific worker implementation with enhanced monitoring and ECS integration."""
    
    def __init__(self, queue, observers: Optional[List[WorkerObserver]] = None, mode: str = "aws-prod"):
        """Initialize AWS worker with queue and observers.
        
        Args:
            queue: Queue handler for receiving tasks
            observers: Optional list of observers for monitoring
            mode: Deployment mode (aws-mock, aws-prod)
        """
        super().__init__(queue)
        self.mode = mode
        
        # Initialize observers based on mode (simplified - no task manager in AMI approach)
        if mode == "aws-mock":
            self.observers = observers or [LoggingObserver()]
        else:  # aws-prod
            # Only CloudWatch and Logging observers for AMI-based deployment
            base_observers = [LoggingObserver(), CloudWatchObserver()]
            self.observers = observers or base_observers
        
        # Log initialization
        logger.info(f"AWS Worker initialized with enhanced monitoring (mode: {mode})")
        if mode == "aws-prod":
            logger.info("AMI-based deployment - scaling handled by EventBridge Lambda functions")
        
        # Track continuous error count for health monitoring
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5  # Threshold for health status
        
        # Schedule periodic health check reporting
        self.last_health_report = 0
        self.health_report_interval = 300  # 5 minutes
        
        # ECS-specific configuration
        self.enable_scale_to_zero = (mode == "aws-prod")
        self.idle_check_interval = 30  # Check for scale-to-zero every 30 seconds
        self.last_idle_check = 0
    
    async def process_task(self, task: Dict[str, Any]) -> str:
        """Process a task with metrics and enhanced error handling.
        
        Args:
            task: Task dictionary with type and parameters
            
        Returns:
            Result message
        """
        # Notify observers of task start
        for observer in self.observers:
            observer.on_task_start(task)
        
        start_time = time.time()
        success = False
        self._check_memory_limits()
        
        try:
            # Process task with parent implementation
            result = await super().process_task(task)
            
            # Reset error counter on success
            self.consecutive_errors = 0
            success = True
            
            return result
        except Exception as e:
            # Increment error counter
            self.consecutive_errors += 1
            
            # Notify observers of worker error
            for observer in self.observers:
                observer.on_worker_error(e)
            
            # If we've hit too many errors, report unhealthy status
            if self.consecutive_errors >= self.max_consecutive_errors:
                self._report_unhealthy("Too many consecutive errors")
            
            # Re-raise for standard error handling
            raise
        finally:
            # Calculate task duration
            duration = time.time() - start_time
            
            # Notify observers of task completion
            for observer in self.observers:
                observer.on_task_complete(task, duration, success)
            
            # Check if it's time for a health report
            current_time = time.time()
            if current_time - self.last_health_report >= self.health_report_interval:
                self._report_health()
                self.last_health_report = current_time
            
            self._check_memory_limits()
    
    async def listen_for_tasks(self) -> None:
        """AMI-based task listening - scaling handled externally by Lambda functions."""
        logger.info("Starting AMI-based ECS worker")
        logger.info("Scaling managed by EventBridge Lambda functions (external)")
        
        # Call parent implementation
        await super().listen_for_tasks()
    
    def get_worker_info(self) -> Dict[str, Any]:
        """Get current worker information for AMI-based deployment."""
        import os
        
        return {
            'deployment_type': 'ami-based',
            'mode': self.mode,
            'scaling_method': 'eventbridge-lambda',
            'ami_deployment': os.getenv('AMI_BASED_DEPLOYMENT', 'false') == 'true',
            'custom_ami': os.getenv('CUSTOM_AMI_ID', 'not-set'),
            'observers': [type(obs).__name__ for obs in self.observers],
            'consecutive_errors': self.consecutive_errors,
            'max_consecutive_errors': self.max_consecutive_errors
        }
    
    def _report_health(self) -> None:
        """Report worker health metrics to CloudWatch."""
        try:
            # Determine health status
            healthy = self.consecutive_errors < self.max_consecutive_errors
            
            # Get CloudWatch client
            cloudwatch = get_cloudwatch_client()
            
            # Report health status
            cloudwatch.put_metric_data(
                Namespace="FilesAPI/Worker",
                MetricData=[
                    {
                        'MetricName': 'WorkerHealthy',
                        'Value': 1 if healthy else 0,
                        'Unit': 'None',
                        'Timestamp': datetime.now(timezone.utc)
                    },
                    {
                        'MetricName': 'ConsecutiveErrors',
                        'Value': self.consecutive_errors,
                        'Unit': 'Count',
                        'Timestamp': datetime.now(timezone.utc)
                    }
                ]
            )
            
            logger.info(f"Reported worker health: {'healthy' if healthy else 'unhealthy'}")
        except Exception as e:
            logger.warning(f"Failed to report worker health: {str(e)}")
    
    def _report_unhealthy(self, reason: str) -> None:
        """Report unhealthy status with reason."""
        try:
            cloudwatch = get_cloudwatch_client()
            
            cloudwatch.put_metric_data(
                Namespace="FilesAPI/Worker",
                MetricData=[
                    {
                        'MetricName': 'WorkerHealthy',
                        'Value': 0,
                        'Unit': 'None'
                    }
                ]
            )
            
            logger.warning(f"Worker reported as unhealthy: {reason}")
        except Exception as e:
            logger.warning(f"Failed to report unhealthy status: {str(e)}")
    
    def _check_memory_limits(self):
        """Check memory limits from cgroups and handle potential OOM."""
        try:
            # Check cgroups v2 first
            if os.path.exists("/sys/fs/cgroup/memory.current"):
                with open("/sys/fs/cgroup/memory.current", "r") as f:
                    current = int(f.read().strip())
                with open("/sys/fs/cgroup/memory.max", "r") as f:
                    max_str = f.read().strip()
                    maximum = int(max_str) if max_str != "max" else float('inf')
                
                if maximum != float('inf'):
                    mem_percent = (current / maximum) * 100
                    if mem_percent > 85:
                        logger.warning(f"High memory usage: {mem_percent:.1f}% ({current/(1024*1024):.1f}MB/{maximum/(1024*1024):.1f}MB)")
                        
                        if mem_percent > 95:
                            logger.error("Critical memory usage - forcing garbage collection!")
                            # Force aggressive memory cleanup
                            gc.collect(generation=2)
                            torch.cuda.empty_cache()
            
            # Check cgroups v1 if v2 not found
            elif os.path.exists("/sys/fs/cgroup/memory/memory.usage_in_bytes"):
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes", "r") as f:
                    current = int(f.read().strip())
                with open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r") as f:
                    maximum = int(f.read().strip())
                
                if maximum < 9223372036854775807:  # Not max int64
                    mem_percent = (current / maximum) * 100
                    if mem_percent > 85:
                        logger.warning(f"High memory usage: {mem_percent:.1f}% ({current/(1024*1024):.1f}MB/{maximum/(1024*1024):.1f}MB)")
                        
                        if mem_percent > 95:
                            logger.error("Critical memory usage - forcing garbage collection!")
                            # Force aggressive memory cleanup
                            gc.collect(generation=2)
                            torch.cuda.empty_cache()
        
        except Exception as e:
            logger.warning(f"Error checking memory limits: {str(e)}")