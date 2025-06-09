#!/bin/bash
cd /app

# Make sure Python path is set
export PYTHONPATH=/app:$PYTHONPATH

# Function to check if models are available
check_models_available() {
    local cache_dir="/app/cache"
    
    echo "🔍 Checking for required models in cache directory: $cache_dir"
    
    # Check for ColPali model
    local colpali_path="${cache_dir}/models--vidore--colpali"
    # Check for SmolVLM model  
    local smolvlm_path="${cache_dir}/models--HuggingFaceTB--SmolVLM-Instruct"
    
    if [ -d "$colpali_path" ] && [ -d "$smolvlm_path" ]; then
        echo "✅ All required models found in cache"
        echo "   - ColPali: $colpali_path"
        echo "   - SmolVLM: $smolvlm_path"
        return 0
    else
        echo "❌ Required models missing:"
        [ ! -d "$colpali_path" ] && echo "   - ColPali: NOT FOUND"
        [ ! -d "$smolvlm_path" ] && echo "   - SmolVLM: NOT FOUND"
        return 1
    fi
}

# Function to wait for models to be available (with timeout)
wait_for_models() {
    local max_wait=300  # 5 minutes
    local wait_time=0
    local check_interval=10
    
    echo "⏳ Waiting for models to be downloaded by model-downloader service..."
    
    while [ $wait_time -lt $max_wait ]; do
        if check_models_available; then
            echo "🎉 Models are ready!"
            return 0
        fi
        
        echo "⏱️  Models not ready yet, waiting... (${wait_time}s/${max_wait}s)"
        sleep $check_interval
        wait_time=$((wait_time + check_interval))
    done
    
    echo "💥 Timeout waiting for models after ${max_wait} seconds"
    echo "💡 The model-downloader service may have failed"
    echo "💡 Check logs: docker logs model-downloader"
    return 1
}

# Initialize storage adapter with environment mode
echo "🔧 Initializing storage adapter..."
python3 -c "
import sys
sys.path.append('/app')

from files_api.storage_adapter import init_storage
import os

mode = os.environ.get('QUEUE_TYPE', 'aws-mock')
print(f'Initializing storage in {mode} mode...')
init_storage(mode)
print('✓ Storage adapter initialized')
"

# Wait for models to be available
if ! wait_for_models; then
    echo "🚨 Cannot start worker without required models"
    exit 1
fi

# Set HF_HUB_OFFLINE for inference (models should already be cached)
export HF_HUB_OFFLINE=1
echo "🔒 Set HF_HUB_OFFLINE=1 for offline model usage"

# Import worker and queue
echo "🚀 Starting VLM+RAG Worker..."
python3 -c "
import asyncio
import sys
import os
sys.path.append('/app')

# Import worker and queue
from files_api.vlm.worker import Worker
from files_api.msg_queue import QueueFactory

print('🎯 Initializing worker components...')
queue = QueueFactory.get_queue_handler()
worker = Worker(queue)
print('✅ Worker ready to process PDF inference tasks')
print('🔄 Listening for tasks from SQS queue...')
asyncio.run(worker.listen_for_tasks())
"