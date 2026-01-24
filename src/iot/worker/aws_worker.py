"""AWS Worker - Stateless worker for Lambda deployment using HTTP adapter"""
import logging
from typing import Dict, Any, Optional
from .base import BaseWorker
from database.sqlite_http_adapter import SQLiteHTTPAdapter

logger = logging.getLogger(__name__)


class AWSWorker(BaseWorker):
    """Stateless worker for AWS Lambda deployment

    Uses SQLiteHTTPAdapter to connect to EC2 database via HTTP.
    No Docker, MQTT, or filesystem dependencies.
    """

    def __init__(self, db_host: str, db_port: int = 8080):
        self.db_host = db_host
        self.db_port = db_port
        self.db_path = f"http://{db_host}:{db_port}"  # HTTP URL for adapter detection
        self.adapter = None
        self.running = False
        logger.info(f"AWSWorker initialized for {self.db_path}")

    async def start(self):
        """Initialize HTTP adapter connection"""
        if not self.running:
            self.adapter = SQLiteHTTPAdapter(self.db_host, self.db_port)
            self.running = True
            logger.info("AWSWorker started with HTTP adapter")

    async def stop(self):
        """Cleanup HTTP adapter"""
        if self.running and self.adapter:
            self.adapter.close()
            self.running = False
            logger.info("AWSWorker stopped")

    async def process_task(self, task_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process task using HTTP adapter (stateless)"""
        logger.info(f"Processing task: {task_data.get('task_type')}")
        # Basic pass-through - routes handle business logic
        return {"status": "processed"}
