import asyncio
import boto3
import logging
import os
import time
from pathlib import Path
import json
# Change: Use settings instead of config
from files_api.settings import get_settings

logger = logging.getLogger(__name__)

class BaseQueue:
    """Base class for queue handling (to be extended by specific implementations)"""
    async def add_task(self, task):
        raise NotImplementedError

    async def get_task(self):
        raise NotImplementedError
    
    async def send_message(self, task):
        """Alias for add_task to maintain compatibility with router code"""
        result = await self.add_task(task)
        # Return a task ID for compatibility
        import time
        return str(int(time.time() * 1000)) if result else None

class LocalQueue(BaseQueue):
    """Handles local queue using file system for IPC"""
    def __init__(self):
        settings = get_settings()
        # Use storage_dir from settings
        self.queue_dir = Path(settings.storage_dir) / "queue_data"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        logger.info("LocalQueue initialized at: %s", self.queue_dir)

    async def add_task(self, task):
        """Add task to queue"""
        try:
            # Create unique filename based on timestamp
            filename = f"{int(time.time() * 1000)}_{os.getpid()}.json"
            filepath = self.queue_dir / filename
            
            # Write task to file
            with open(filepath, 'w') as f:
                json.dump(task, f)
            
            logger.info("Added task to queue: %s", task)
            return True
        except Exception as e:
            logger.error("Error adding task to queue: %s", str(e))
            return False

    async def get_task(self):
        """Get next task from queue"""
        try:
            # Get all task files sorted by name (timestamp)
            files = sorted(self.queue_dir.glob("*.json"))
            
            if not files:
                await asyncio.sleep(0.1)  # Prevent busy waiting
                return None
                
            # Get oldest task file
            task_file = files[0]
            
            try:
                # Read task
                with open(task_file, 'r') as f:
                    task = json.load(f)
                
                # Remove file after reading
                task_file.unlink()
                
                logger.info("Retrieved task from queue: %s", task)
                return task
            except Exception as e:
                logger.error("Error reading task file %s: %s", task_file, str(e))
                # Move problematic file to error directory
                error_dir = self.queue_dir / "errors"
                error_dir.mkdir(exist_ok=True)
                task_file.rename(error_dir / task_file.name)
                return None
                
        except Exception as e:
            logger.error("Error getting task from queue: %s", str(e))
            return None

    def __del__(self):
        """Cleanup method"""
        try:
            # Optionally clean up old files
            for file in self.queue_dir.glob("*.json"):
                if time.time() - file.stat().st_mtime > 3600:  # Clean files older than 1 hour
                    file.unlink()
        except Exception as e:
            logger.error("Error during cleanup: %s", str(e))

class SQSQueue(BaseQueue):
    """Handles AWS SQS queue"""
    def __init__(self):
        # Get settings
        settings = get_settings()
        
        # Create SQS client with settings
        self.sqs = boto3.client(
            "sqs",
            endpoint_url=settings.aws_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region
        )
        
        self.queue_url = settings.sqs_queue_url
        
        logger.info(f"SQSQueue initialized")
        logger.info(f"  Endpoint: {settings.aws_endpoint_url}")
        logger.info(f"  Queue URL: {self.queue_url}")
        logger.info(f"  Region: {settings.aws_region}")

    async def add_task(self, task):
        """Add a task to the SQS queue."""
        try:
            response = self.sqs.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(task),  # Convert dict to JSON string
            )
            logger.info(f"Task added to SQS queue with ID: {response.get('MessageId')}")
            return True
        except Exception as e:
            logger.error(f"Error adding task to SQS queue: {str(e)}")
            return False

    async def get_task(self):
        messages = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )
        if "Messages" in messages:
            message = messages["Messages"][0]
            self.sqs.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )
            # Parse the JSON message
            task = json.loads(message["Body"])
            logger.info(f"Retrieved task from SQS queue: {task}")
            return task
        return None

class QueueFactory:
    """Factory to initialize the correct queue handler based on deployment mode"""

    @staticmethod
    def get_queue_handler():
        settings = get_settings()
        
        queue_classes = {
            "local-dev": LocalQueue,
            "aws-mock": SQSQueue,
            "aws-prod": SQSQueue,
        }
        
        deployment_mode = settings.deployment_mode
        if deployment_mode not in queue_classes:
            raise ValueError(
                f"Invalid deployment_mode: {deployment_mode}. "
                f"Choose from {list(queue_classes.keys())}"
            )
        
        logger.info(f"Creating queue handler for mode: {deployment_mode}")
        return queue_classes[deployment_mode]()

# Initialize queue handler (used throughout the app)
queue_handler = QueueFactory.get_queue_handler()