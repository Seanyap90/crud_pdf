FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Set environment variables
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
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

# Set up working directory
WORKDIR /app

# Install Python packages
RUN pip install --upgrade pip setuptools wheel

# Install PDF processing dependencies
RUN pip install pdf2image

# Install ML/RAG requirements (minimal set)
RUN pip install \
    torch>=2.0.0 \
    transformers>=4.36.0 \
    "git+https://github.com/sergiopaniego/byaldi.git@colsmolvlm-support" \
    datasets>=2.15.0 \
    Pillow>=10.0.0 \
    bitsandbytes>=0.41.0 \
    huggingface_hub[cli] \
    boto3

# Create cache and working directories
RUN mkdir -p /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder
ENV TRANSFORMERS_CACHE="/app/cache"
ENV HF_HOME="/app/cache"

# Create module structure
RUN mkdir -p /app/files_api/vlm
COPY src/files_api/vlm/load_models.py /app/files_api/vlm/
COPY src/files_api/vlm/rag.py /app/files_api/vlm/
COPY src/files_api/vlm/start-worker.sh /app/start-worker.sh
RUN touch /app/files_api/__init__.py
RUN touch /app/files_api/vlm/__init__.py
RUN chmod +x /app/start-worker.sh

# Set Python path to find modules
ENV PYTHONPATH="/app:${PYTHONPATH}"

# Create a non-root user
RUN useradd -m -u 1000 worker
RUN chown -R worker:worker /app /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder

# Switch to non-root user
USER worker

# Create logs directory
RUN mkdir -p /app/logs

# Keep container running with models preloaded for better performance
CMD ["/app/start-worker.sh"]