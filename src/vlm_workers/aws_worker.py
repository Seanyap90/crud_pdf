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
from vlm_workers.scaling.auto_scaler import get_task_manager, AutoScalingManager
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


class ECSTaskObserver(WorkerObserver):
    """ECS-specific observer for task lifecycle management."""
    
    def __init__(self, task_manager: ECSTaskManager):
        """Initialize with ECS task manager."""
        self.task_manager = task_manager
        self.processed_tasks = 0
        
    def on_task_start(self, task: Dict[str, Any]) -> None:
        """Handle ECS task start."""
        # Register current ECS task if not already registered
        if not self.task_manager.get_current_task_arn():
            self.task_manager.register_current_task()
        
        # Log ECS task info
        health = self.task_manager.monitor_task_health()
        if health.get('task_id'):
            logger.info(f"[ECS Task {health['task_id']}] Processing task {self.processed_tasks + 1}")
    
    def on_task_complete(self, task: Dict[str, Any], duration: float, success: bool) -> None:
        """Handle ECS task completion."""
        self.processed_tasks += 1
        
        # Get current health and statistics
        health = self.task_manager.monitor_task_health()
        stats = self.task_manager.get_task_statistics()
        
        logger.info(f"[ECS Task] Processed {self.processed_tasks} tasks total")
        logger.info(f"[ECS Task] Success rate: {stats.get('success_rate', 0):.1f}%")
        
        # Check if we should scale to zero based on queue state
        settings = get_settings()
        if hasattr(settings, 'sqs_queue_url') and settings.sqs_queue_url:
            ready_to_scale = self.task_manager.scale_to_zero(settings.sqs_queue_url)
            if ready_to_scale and self.processed_tasks > 0:  # Only scale to zero after processing at least one task
                logger.info("Queue is empty and task completed - initiating scale to zero")
                self.task_manager.initiate_graceful_shutdown("Queue empty - scaling to zero")
    
    def on_worker_error(self, error: Exception) -> None:
        """Handle worker error in ECS context."""
        # Signal task failure to ECS task manager
        self.task_manager.signal_task_completion(success=False, reason=f"Worker error: {str(error)}")
        logger.error(f"[ECS Task] Worker error reported to task manager: {str(error)}")

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
        
        # Initialize ECS task manager for aws-prod
        self.task_manager = None
        if mode == "aws-prod":
            try:
                self.task_manager = get_task_manager()
                logger.info("ECS task manager initialized")
            except Exception as e:
                logger.warning(f"Could not initialize ECS task manager: {e}")
        
        # Initialize observers based on mode
        if mode == "aws-mock":
            self.observers = observers or [LoggingObserver()]
        else:  # aws-prod
            base_observers = [LoggingObserver(), CloudWatchObserver()]
            if self.task_manager:
                base_observers.append(ECSTaskObserver(self.task_manager))
            self.observers = observers or base_observers
        
        # Log initialization
        logger.info(f"AWS Worker initialized with enhanced monitoring (mode: {mode})")
        if self.task_manager:
            logger.info("ECS task lifecycle management enabled")
        
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
        """Enhanced task listening with ECS scale-to-zero support."""
        if self.enable_scale_to_zero and self.task_manager:
            logger.info("Starting ECS worker with scale-to-zero capability")
        
        # Call parent implementation but with ECS enhancements
        await super().listen_for_tasks()
    
    def _should_check_for_scale_to_zero(self) -> bool:
        """Check if it's time to evaluate scale-to-zero."""
        current_time = time.time()
        if current_time - self.last_idle_check >= self.idle_check_interval:
            self.last_idle_check = current_time
            return True
        return False
    
    async def _check_scale_to_zero_condition(self) -> bool:
        """Check if worker should scale to zero based on queue state."""
        if not self.enable_scale_to_zero or not self.task_manager:
            return False
        
        try:
            settings = get_settings()
            if not hasattr(settings, 'sqs_queue_url') or not settings.sqs_queue_url:
                return False
            
            # Check queue state for scale decision
            scale_info = self.task_manager.check_queue_for_scale_decision(settings.sqs_queue_url)
            
            # Scale to zero if no messages and no in-flight processing
            should_scale = (
                scale_info.get('total_messages', 0) == 0 and
                scale_info.get('in_flight_messages', 0) == 0
            )
            
            if should_scale:
                logger.info("Scale-to-zero condition met: queue is empty")
                return True
                
            return False
            
        except Exception as e:
            logger.warning(f"Error checking scale-to-zero condition: {e}")
            return False
    
    def get_ecs_task_info(self) -> Optional[Dict[str, Any]]:
        """Get current ECS task information."""
        if not self.task_manager:
            return None
        
        health = self.task_manager.monitor_task_health()
        stats = self.task_manager.get_task_statistics()
        
        return {
            'task_health': health,
            'task_statistics': stats,
            'mode': self.mode,
            'scale_to_zero_enabled': self.enable_scale_to_zero
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