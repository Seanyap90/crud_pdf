import asyncio
import boto3
from files_api.config import config
import logging
import os
import time
from pathlib import Path
import json

logger = logging.getLogger(__name__)

class BaseQueue:
    """Base class for queue handling (to be extended by specific implementations)"""
    async def add_task(self, task):
        raise NotImplementedError

    async def get_task(self):
        raise NotImplementedError

class LocalQueue(BaseQueue):
    """Handles local queue using file system for IPC"""
    def __init__(self):
        self.queue_dir = Path("queue_data")
        self.queue_dir.mkdir(exist_ok=True)
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
        self.sqs = boto3.client(
            "sqs",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        )
        self.queue_url = config.SQS_QUEUE_URL

    async def add_task(self, task):
        self.sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=str(task),
        )

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
            return message["Body"]
        return None

class QueueFactory:
    """Factory to initialize the correct queue handler based on deployment mode"""

    queue_classes = {
        "local-mock": LocalQueue,
        "local": LocalQueue,
        "hybrid": SQSQueue,
        "cloud": SQSQueue,
    }

    @staticmethod
    def get_queue_handler():
        queue_type = config.QUEUE_TYPE
        if queue_type not in QueueFactory.queue_classes:
            raise ValueError(f"Invalid QUEUE_TYPE: {queue_type}. Choose from {list(QueueFactory.queue_classes.keys())}")
        return QueueFactory.queue_classes[queue_type]()  # Dynamically instantiate the correct class

# Initialize queue handler (used throughout the app)
queue_handler = QueueFactory.get_queue_handler()