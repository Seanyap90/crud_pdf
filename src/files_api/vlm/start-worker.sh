#!/bin/bash
cd /app
# Make sure Python path is set
export PYTHONPATH=/app:$PYTHONPATH
# Run the worker directly with Python
python3 -c "
import asyncio
import sys
sys.path.append('/app')
from files_api.vlm.rag import Worker
from files_api.msg_queue import QueueFactory
print('Starting Worker...')
queue = QueueFactory.get_queue_handler()
worker = Worker(queue)
print('Worker ready to process tasks')
asyncio.run(worker.listen_for_tasks())
"