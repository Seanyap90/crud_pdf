#!/bin/bash
cd /app

# Make sure Python path is set
export PYTHONPATH=/app/src:$PYTHONPATH

# Function to check if models are available and have proper structure
check_models_available() {
    local cache_dir="/app/cache"

    echo "🔍 Checking for required models in cache directory: $cache_dir"

    # Check for ColPali model (standard HuggingFace cache structure)
    local colpali_path="${cache_dir}/models--vidore--colpali"
    # Check for SmolVLM model
    local smolvlm_path="${cache_dir}/models--HuggingFaceTB--SmolVLM-Instruct"

    local models_valid=true

    # ColPali validation
    if [ -d "$colpali_path" ] && [ -d "$colpali_path/snapshots" ] && [ "$(ls -A $colpali_path/snapshots 2>/dev/null)" ]; then
        echo "✅ ColPali model found and validated: $colpali_path"
    else
        echo "❌ ColPali model missing or corrupted: $colpali_path"
        models_valid=false
    fi

    # SmolVLM validation
    if [ -d "$smolvlm_path" ] && [ -d "$smolvlm_path/snapshots" ] && [ "$(ls -A $smolvlm_path/snapshots 2>/dev/null)" ]; then
        echo "✅ SmolVLM model found and validated: $smolvlm_path"
    else
        echo "❌ SmolVLM model missing or corrupted: $smolvlm_path"
        models_valid=false
    fi

    if [ "$models_valid" = true ]; then
        echo "✅ All required models validated successfully"
        return 0
    else
        echo "❌ One or more models failed validation"
        return 1
    fi
}

# Function to refresh corrupted models using huggingface-cli
refresh_models_with_cli() {
    local cache_dir="/app/cache"
    echo "🔧 Attempting to refresh models using huggingface-cli..."

    # Temporarily disable offline mode for downloads
    export HF_HUB_OFFLINE=0

    # Models to refresh
    local models=(
        "HuggingFaceTB/SmolVLM-Instruct"
        "vidore/colpali"
        "vidore/colpaligemma-3b-mix-448-base"
    )

    for model in "${models[@]}"; do
        echo "📥 Refreshing $model using huggingface-cli..."
        if command -v huggingface-cli >/dev/null 2>&1; then
            huggingface-cli download "$model" \
                --cache-dir "$cache_dir" \
                --local-dir-use-symlinks False \
                --resume-download
        else
            echo "⚠️  huggingface-cli not found, using Python downloader as fallback..."
            python3 -m vlm_workers.models.downloader --cache-dir "$cache_dir"
        fi

        if [ $? -eq 0 ]; then
            echo "✅ Successfully refreshed $model"
        else
            echo "❌ Failed to refresh $model"
        fi
    done

    echo "🎉 Model refresh completed!"
}

# Function to wait for models to be available (with corruption detection)
wait_for_models() {
    local max_wait=300  # 5 minutes
    local wait_time=0
    local check_interval=10

    echo "⏳ Checking for pre-loaded models..."

    # First check if models are already available
    if check_models_available; then
        echo "🎉 Pre-loaded models are ready!"
        return 0
    fi

    echo "❌ Pre-loaded models are missing or corrupted"
    echo "🔧 Attempting to refresh models directly..."

    # Try to refresh models using huggingface-cli
    refresh_models_with_cli

    # Check again after refresh attempt
    if check_models_available; then
        echo "🎉 Models are now ready after refresh!"
        return 0
    else
        echo "💥 Failed to refresh models"
        echo "💡 Check network connectivity and HuggingFace Hub access"
        return 1
    fi
}

# Initialize storage adapter with environment mode
echo "🔧 Initializing storage adapter..."
python3 -c "
import sys
sys.path.append('/app/src')

from files_api.adapters.storage import init_storage
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