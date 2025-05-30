# Stage 1: Base image with system dependencies
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Set environment variables for build
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3-pip \
    python3-dev \
    build-essential \
    poppler-utils \
    wget \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Stage 2: Python dependencies
FROM base AS python-deps

WORKDIR /deps

# Install Python package tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy only requirements file for better caching
COPY src/files_api/vlm/requirements.txt .

# Install dependencies into a virtual environment
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: Model files download
FROM python-deps as model-download

WORKDIR /models

# Create cache directory
RUN mkdir -p /models/cache

# Download model files
RUN echo "Downloading model files..." && \
    python -c "from huggingface_hub import snapshot_download; \
    print('Downloading SmolVLM-Instruct files...'); \
    snapshot_download(repo_id='HuggingFaceTB/SmolVLM-Instruct', cache_dir='/models/cache', local_files_only=False); \
    print('SmolVLM-Instruct files downloaded successfully!')" && \
    python -c "from huggingface_hub import snapshot_download; \
    print('Downloading Colpali RAG model files...'); \
    snapshot_download(repo_id='vidore/colpali', cache_dir='/models/cache', local_files_only=False); \
    print('Colpali model files downloaded successfully!')" && \
    python -c "from huggingface_hub import snapshot_download; \
    print('Downloading Colpaligemma model files...'); \
    snapshot_download(repo_id='vidore/colpaligemma-3b-mix-448-base', cache_dir='/models/cache', local_files_only=False); \
    print('Colpaligemma model files downloaded successfully!')" && \
    echo "All model files downloaded successfully!"

# Final stage: Runtime environment
FROM base AS runtime

# Set CUDA and performance environment variables
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6+PTX" \
    OMP_NUM_THREADS=4 \
    LOW_MEMORY=1 \
    USE_FLASH_ATTENTION=0 \
    TRANSFORMERS_CACHE="/app/cache" \
    HF_HOME="/app/cache" \
    PYTHONPATH="/app:${PYTHONPATH}" \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    CACHE_IMPLEMENTATION=offloaded \
    MODEL_MEMORY_LIMIT=7GiB \
    OFFLOAD_TO_CPU=true \
    CPU_OFFLOAD_FOLDER=/app/offload_folder \
    PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128,garbage_collection_threshold:0.8 \
    MKL_NUM_THREADS=4 \
    HF_HUB_OFFLINE=1

# Set up working directory
WORKDIR /app

# Copy Python dependencies from dependencies stage
COPY --from=python-deps /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy model files from model-download stage
COPY --from=model-download /models/cache /app/cache

# Create necessary directories
RUN mkdir -p /app/temp_pdfs /app/.byaldi /app/offload_folder /app/logs \
    && mkdir -p /app/files_api/vlm

# Create package structure
RUN touch /app/files_api/__init__.py \
    && touch /app/files_api/vlm/__init__.py

# Copy application modules - copy these layers last as they change most frequently
COPY src/files_api/config.py /app/files_api/
COPY src/files_api/msg_queue.py /app/files_api/
COPY src/files_api/storage_adapter.py /app/files_api/
COPY src/files_api/vlm/load_models_eb.py /app/files_api/vlm/
COPY src/files_api/vlm/worker.py /app/files_api/vlm/

# Copy startup script
COPY src/files_api/vlm/start-worker.sh /app/start-worker.sh
RUN chmod +x /app/start-worker.sh

# Create a non-root user and set ownership
RUN useradd -m -u 1000 worker \
    && chown -R worker:worker /app /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder /app/logs

# Switch to non-root user
USER worker

# Start command
CMD ["/app/start-worker.sh"]