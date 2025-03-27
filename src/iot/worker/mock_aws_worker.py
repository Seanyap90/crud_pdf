from .base import BaseWorker
from typing import Dict, Any

class MockAWSWorker(BaseWorker):
    """Worker that mimics AWS services behavior locally"""
    
    async def start(self):
        # Initialize mock AWS services
        pass
        
    async def stop(self):
        # Cleanup mock resources
        pass
        
    async def process_task(self, task_data: Dict[str, Any]):
        # Process task using mock AWS services
        pass
