#!/bin/bash
cd /app

# Make sure Python path is set
export PYTHONPATH=/app/src:$PYTHONPATH

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

# Initialize GPU configuration and storage adapter
echo "🔧 Initializing GPU configuration and storage adapter..."
python3 -c "
import sys
sys.path.append('/app/src')

from files_api.adapters.storage import init_storage
from vlm_workers.gpu.gpu_config import GPUConfigManager
import os

# Initialize GPU configuration first
print('🎯 Initializing GPU configuration...')
gpu_config = GPUConfigManager()
deployment_mode = gpu_config.validate_deployment_mode()
print(f'✓ GPU configuration initialized for mode: {deployment_mode}')

# Apply GPU-specific environment overrides
gpu_config.apply_environment_overrides()
print('✓ GPU environment overrides applied')

# Log GPU configuration for debugging
print(f'GPU Config:')
print(f'  - Model memory limit: {gpu_config.get_memory_config()}')
print(f'  - CUDA allocator: {gpu_config.get_cuda_allocator_config()}')
print(f'  - Cache implementation: {gpu_config.get_cache_config()}')
print(f'  - CPU offloading: {gpu_config.should_offload_to_cpu()}')
print(f'  - Use quantization: {gpu_config.config[\"use_quantization\"]}')

# Initialize storage adapter
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
sys.path.append('/app/src')

# Import worker and queue  
from vlm_workers.worker import Worker
from files_api.msg_queue import QueueFactory

print('🎯 Initializing worker components...')
queue = QueueFactory.get_queue_handler()
worker = Worker(queue)
print('✅ Worker ready to process PDF inference tasks')
print('🔄 Listening for tasks from SQS queue...')
asyncio.run(worker.listen_for_tasks())
"