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
COPY src/vlm_workers/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Final stage: Runtime environment (worker-focused, no models)
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

# Create necessary directories (cache will be mounted as volume)
RUN mkdir -p /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder /app/logs \
    && mkdir -p /app/src/files_api /app/src/vlm_workers

# Create package structure
RUN touch /app/src/__init__.py \
    && touch /app/src/files_api/__init__.py \
    && touch /app/src/vlm_workers/__init__.py

# Copy application modules - copy these layers last as they change most frequently
COPY src/files_api/config/ /app/src/files_api/config/
COPY src/files_api/settings.py /app/src/files_api/
COPY src/files_api/adapters/ /app/src/files_api/adapters/
COPY src/files_api/storage_adapter.py /app/src/files_api/
COPY src/vlm_workers/ /app/src/vlm_workers/

# Copy startup script
COPY deployment/docker/vlm-worker/start-worker.sh /app/start-worker.sh
RUN chmod +x /app/start-worker.sh

# Create a non-root user and set ownership
RUN useradd -m -u 1000 worker \
    && chown -R worker:worker /app /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder /app/logs

# Switch to non-root user
USER worker

# Start command
CMD ["/app/start-worker.sh"]