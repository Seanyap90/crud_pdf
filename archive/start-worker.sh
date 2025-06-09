#!/bin/bash
cd /app
# Make sure Python path is set
export PYTHONPATH=/app:$PYTHONPATH

# Verify GPU is available
echo "Checking GPU availability..."
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'Device count: {torch.cuda.device_count()}'); print(f'Device name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None\"}')"

# Inside start-worker.sh
echo "Environment variables:"
echo "AWS_ENDPOINT_URL: $AWS_ENDPOINT_URL"
echo "SQS_QUEUE_URL: $SQS_QUEUE_URL"
echo "QUEUE_TYPE: $QUEUE_TYPE"

# Run the worker directly with Python
python3 -c "
import asyncio
import sys
import os
sys.path.append('/app')

# Initialize storage adapter with environment mode
from files_api.storage_adapter import init_storage
init_storage(os.environ.get('QUEUE_TYPE', 'aws-mock'))

# Import worker and queue
from files_api.vlm.worker import Worker
from files_api.msg_queue import QueueFactory

print('Starting Worker with storage adapter...')
queue = QueueFactory.get_queue_handler()
worker = Worker(queue)
print('Worker ready to process tasks')
asyncio.run(worker.listen_for_tasks())
"