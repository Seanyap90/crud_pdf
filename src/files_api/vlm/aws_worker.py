# aws_worker.py
"""
Enhanced AWS Worker implementation for VLM+RAG processing
Adds AWS-specific features like CloudWatch metrics,
error handling, and integration with AWS services
"""
import boto3
import logging
import json
import time
import os
import gc
import torch
import asyncio
from datetime import datetime
from files_api.vlm.rag import Worker
from files_api.config import config

# Configure logging
logger = logging.getLogger(__name__)

class AWSWorker(Worker):
    """Worker implementation optimized for AWS deployment"""
    
    def __init__(self, queue, aws_region=None):
        """Initialize worker with AWS-specific configuration"""
        super().__init__(queue)
        
        # Get AWS region from environment or use default
        self.aws_region = aws_region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        
        # Get AWS endpoint URL for local moto testing
        self.endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
        
        # Initialize AWS clients
        self._init_aws_clients()
        
        # Track task metrics
        self.task_start_time = None
        self.current_task_id = None
        
        # Health check data
        self.processed_tasks = 0
        self.failed_tasks = 0
        self.last_task_duration = 0
        
        logger.info(f"AWS Worker initialized in region {self.aws_region}")
    
    def _init_aws_clients(self):
        """Initialize AWS service clients"""
        try:
            # Common client args
            client_kwargs = {
                'region_name': self.aws_region
            }
            
            # Add endpoint URL for local testing if specified
            if self.endpoint_url:
                client_kwargs['endpoint_url'] = self.endpoint_url
            
            # Initialize CloudWatch client for metrics
            self.cloudwatch = boto3.client('cloudwatch', **client_kwargs)
            
            # Initialize S3 client for any direct operations
            self.s3 = boto3.client('s3', **client_kwargs)
            
            # Initialize CloudWatch Logs client
            self.logs = boto3.client('logs', **client_kwargs)
            
            # Initialize SQS client for any queue operations outside the queue handler
            self.sqs = boto3.client('sqs', **client_kwargs)
            
            # Create log group and stream if using CloudWatch Logs
            if not self.endpoint_url:  # Skip in local mock mode
                self._setup_cloudwatch_logs()
                
            logger.info("AWS clients initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing AWS clients: {str(e)}")
            # Continue without AWS monitoring if clients can't be initialized
            self.cloudwatch = None
            self.logs = None
    
    def _setup_cloudwatch_logs(self):
        """Set up CloudWatch Logs group and stream"""
        try:
            # Log group name
            log_group_name = "/aws/rag-worker"
            
            # Create log group if it doesn't exist
            try:
                self.logs.create_log_group(logGroupName=log_group_name)
                logger.info(f"Created CloudWatch Logs group: {log_group_name}")
            except self.logs.exceptions.ResourceAlreadyExistsException:
                logger.info(f"CloudWatch Logs group already exists: {log_group_name}")
            
            # Create log stream with timestamp and instance ID
            instance_id = os.environ.get('EC2_INSTANCE_ID', 'local')
            timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            log_stream_name = f"{instance_id}-{timestamp}"
            
            try:
                self.logs.create_log_stream(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name
                )
                logger.info(f"Created CloudWatch Logs stream: {log_stream_name}")
            except Exception as e:
                logger.error(f"Error creating CloudWatch Logs stream: {str(e)}")
            
            # Store log group and stream names
            self.log_group_name = log_group_name
            self.log_stream_name = log_stream_name
        except Exception as e:
            logger.error(f"Error setting up CloudWatch Logs: {str(e)}")
            self.log_group_name = None
            self.log_stream_name = None
    
    def _log_to_cloudwatch(self, message, level="INFO"):
        """Send log message to CloudWatch Logs"""
        if not hasattr(self, 'log_group_name') or not self.log_group_name:
            return
            
        if not self.logs:
            return
            
        try:
            timestamp = int(datetime.now().timestamp() * 1000)
            
            self.logs.put_log_events(
                logGroupName=self.log_group_name,
                logStreamName=self.log_stream_name,
                logEvents=[
                    {
                        'timestamp': timestamp,
                        'message': f"[{level}] {message}"
                    }
                ]
            )
        except Exception as e:
            logger.warning(f"Error sending logs to CloudWatch: {str(e)}")
    
    def _put_cloudwatch_metric(self, metric_name, value, unit="Count", dimensions=None):
        """Send custom metric to CloudWatch"""
        if not self.cloudwatch:
            return
            
        try:
            # Default dimensions
            if dimensions is None:
                dimensions = [
                    {
                        'Name': 'Environment',
                        'Value': os.environ.get('DEPLOYMENT_ENV', 'development')
                    },
                    {
                        'Name': 'InstanceId',
                        'Value': os.environ.get('EC2_INSTANCE_ID', 'local')
                    }
                ]
            
            # Put metric data
            self.cloudwatch.put_metric_data(
                Namespace='RAGWorker',
                MetricData=[
                    {
                        'MetricName': metric_name,
                        'Value': value,
                        'Unit': unit,
                        'Dimensions': dimensions
                    }
                ]
            )
        except Exception as e:
            logger.warning(f"Error putting CloudWatch metric: {str(e)}")
    
    async def process_task(self, task):
        """Process a PDF task with AWS monitoring and metrics"""
        # Set task metadata for tracking
        self.current_task_id = task.get('file_info', {}).get('invoice_id', 'unknown')
        self.task_start_time = time.time()
        
        # Log task start to CloudWatch
        task_info = f"Processing task {self.current_task_id}: {task.get('task_type')}"
        logger.info(task_info)
        self._log_to_cloudwatch(f"Started {task_info}")
        
        try:
            # Pre-task GPU memory metric
            if torch.cuda.is_available():
                pre_mem = torch.cuda.memory_allocated() / (1024 ** 3)  # GB
                self._put_cloudwatch_metric(
                    metric_name="GPUMemoryPreTask",
                    value=pre_mem,
                    unit="Gigabytes"
                )
            
            # Process the task with the parent method
            result = await super().process_task(task)
            
            # Calculate task duration
            self.last_task_duration = time.time() - self.task_start_time
            
            # Post-task GPU memory metric
            if torch.cuda.is_available():
                post_mem = torch.cuda.memory_allocated() / (1024 ** 3)  # GB
                self._put_cloudwatch_metric(
                    metric_name="GPUMemoryPostTask", 
                    value=post_mem,
                    unit="Gigabytes"
                )
                
                # Memory difference
                mem_diff = post_mem - pre_mem
                self._put_cloudwatch_metric(
                    metric_name="GPUMemoryDelta",
                    value=mem_diff,
                    unit="Gigabytes"
                )
            
            # Track successful completion
            self.processed_tasks += 1
            
            # Send metrics to CloudWatch
            self._put_cloudwatch_metric("TasksProcessed", 1)
            self._put_cloudwatch_metric(
                "TaskDuration", 
                self.last_task_duration,
                unit="Seconds"
            )
            
            # Log task completion
            completion_msg = f"Completed task {self.current_task_id} in {self.last_task_duration:.2f} seconds"
            logger.info(completion_msg)
            self._log_to_cloudwatch(completion_msg)
            
            # Handle GPU memory proactively
            if torch.cuda.is_available():
                # If memory usage is high, force cleanup
                if post_mem > 20:  # Threshold in GB
                    logger.info("High GPU memory usage detected, performing cleanup")
                    self._log_to_cloudwatch("Performing proactive GPU memory cleanup")
                    
                    # Force CUDA cache empty and garbage collection
                    torch.cuda.empty_cache()
                    gc.collect()
                    
                    # Measure memory after cleanup
                    clean_mem = torch.cuda.memory_allocated() / (1024 ** 3)
                    self._put_cloudwatch_metric(
                        metric_name="GPUMemoryAfterCleanup",
                        value=clean_mem,
                        unit="Gigabytes"
                    )
                    
                    # Log memory freed
                    mem_freed = post_mem - clean_mem
                    if mem_freed > 0:
                        self._log_to_cloudwatch(f"Freed {mem_freed:.2f} GB of GPU memory")
            
            return result
        except Exception as e:
            # Track task failure
            self.failed_tasks += 1
            self.last_task_duration = time.time() - self.task_start_time
            
            # Send failure metrics
            self._put_cloudwatch_metric("TasksFailed", 1)
            self._put_cloudwatch_metric(
                "FailedTaskDuration", 
                self.last_task_duration,
                unit="Seconds"
            )
            
            # Log detailed error
            error_msg = f"Error processing task {self.current_task_id}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._log_to_cloudwatch(error_msg, level="ERROR")
            
            # Include error in result if we can extract invoice ID
            filepath = task.get('file_info', {}).get('filepath', 'unknown')
            invoice_id = task.get('file_info', {}).get('invoice_id')
            
            if invoice_id:
                try:
                    # Update database with failure status
                    from database.local import update_invoice_with_extracted_data
                    update_invoice_with_extracted_data(
                        invoice_id=invoice_id,
                        total_amount=None,
                        reported_weight_kg=None,
                        status='failed',
                        completion_date=datetime.utcnow().isoformat(),
                        error_message=str(e)[:500]  # Truncate long error messages
                    )
                except Exception as update_error:
                    logger.error(f"Error updating invoice status: {str(update_error)}")
            
            # Re-raise the exception for the worker loop to handle
            raise
        finally:
            # Reset task tracking variables
            self.current_task_id = None
            self.task_start_time = None
            
            # Force memory cleanup
            torch.cuda.empty_cache()
            gc.collect()
    
    def get_health_status(self):
        """Return health status info for monitoring"""
        return {
            "status": "healthy" if self.failed_tasks < 5 else "degraded",
            "processed_tasks": self.processed_tasks,
            "failed_tasks": self.failed_tasks,
            "last_task_duration": f"{self.last_task_duration:.2f}s",
            "current_task": self.current_task_id,
            "gpu_memory": f"{torch.cuda.memory_allocated() / (1024 ** 3):.2f} GB" if torch.cuda.is_available() else "N/A",
            "uptime": time.time() - self.start_time if hasattr(self, 'start_time') else 0
        }
    
    async def listen_for_tasks(self):
        """Override listen_for_tasks to add AWS-specific monitoring"""
        logger.info("AWS Worker started listening for tasks")
        self._log_to_cloudwatch("Worker started listening for tasks")
        
        # Store startup time
        self.start_time = time.time()
        
        # Set initial health status
        self._put_cloudwatch_metric("WorkerStarted", 1)
        
        # Report worker presence for auto-scaling metrics
        self._put_cloudwatch_metric("ActiveWorkers", 1)
        
        # Track consecutive errors for backoff
        consecutive_errors = 0
        last_status_report = time.time()
        status_report_interval = 300  # 5 minutes
        
        while self.running:
            try:
                # Report periodic health status
                current_time = time.time()
                if current_time - last_status_report > status_report_interval:
                    health = self.get_health_status()
                    logger.info(f"Worker health: {json.dumps(health)}")
                    self._log_to_cloudwatch(f"Health status: {json.dumps(health)}")
                    
                    # Report worker is still active
                    self._put_cloudwatch_metric("ActiveWorkers", 1)
                    
                    # Report GPU memory
                    if torch.cuda.is_available():
                        gpu_mem = torch.cuda.memory_allocated() / (1024 ** 3)
                        self._put_cloudwatch_metric(
                            "GPUMemoryUsage",
                            gpu_mem,
                            unit="Gigabytes"
                        )
                    
                    last_status_report = current_time
                
                # Check for a new task
                task = await self.queue.get_task()
                
                if task:
                    logger.info(f"Received task: {task}")
                    self._log_to_cloudwatch(f"Received task: {task}")
                    
                    result = await self.process_task(task)
                    logger.info(result)
                    
                    # Reset error counter on success
                    consecutive_errors = 0
                else:
                    # Report queue is empty
                    self._put_cloudwatch_metric("EmptyQueuePolls", 1)
                
                # Small pause to prevent busy-waiting
                await asyncio.sleep(0.1)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in task processing loop: {str(e)}", exc_info=True)
                self._log_to_cloudwatch(f"Error in task loop: {str(e)}", level="ERROR")
                
                # Report error metric
                self._put_cloudwatch_metric("WorkerErrors", 1)
                
                # Implement exponential backoff with cap
                backoff_time = min(30, 2 ** consecutive_errors)
                logger.warning(f"Backing off for {backoff_time} seconds after error...")
                self._log_to_cloudwatch(f"Backing off for {backoff_time} seconds", level="WARN")
                
                await asyncio.sleep(backoff_time)
                
                # Reset processor if we have multiple errors
                if consecutive_errors >= 3:
                    try:
                        logger.warning("Multiple consecutive errors - reinitializing PDF processor")
                        self._log_to_cloudwatch("Reinitializing PDF processor", level="WARN")
                        
                        # Track recovery attempt
                        self._put_cloudwatch_metric("ProcessorResets", 1)
                        
                        # Recreate the PDF processor
                        self.pdf_processor = None
                        gc.collect()
                        torch.cuda.empty_cache()
                        
                        # Import here to avoid circular imports
                        from files_api.vlm.rag import PDFProcessor
                        self.pdf_processor = PDFProcessor()
                    except Exception as reset_error:
                        logger.error(f"Error reinitializing PDF processor: {str(reset_error)}")
                        self._log_to_cloudwatch(f"Failed to reinitialize processor: {str(reset_error)}", level="ERROR")
    
    def stop(self):
        """Stop the worker gracefully with AWS cleanup"""
        logger.info("Stopping AWS worker...")
        self._log_to_cloudwatch("Worker shutting down")
        
        # Report worker stopping
        self._put_cloudwatch_metric("WorkerStopped", 1)
        
        # Stop the base worker
        super().stop()
        
        # Additional AWS-specific cleanup
        if hasattr(self, 'pdf_processor') and self.pdf_processor:
            try:
                self.pdf_processor.cleanup()
                self.pdf_processor = None
                logger.info("PDF processor cleaned up")
            except Exception as e:
                logger.warning(f"Error cleaning up processor during stop: {str(e)}")
        
        # Log final shutdown
        self._log_to_cloudwatch("Worker shutdown complete")
        logger.info("AWS worker shutdown complete")