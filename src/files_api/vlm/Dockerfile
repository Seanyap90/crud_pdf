# Use NVIDIA CUDA base image with Python support
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
    python3-venv \
    build-essential \
    poppler-utils \
    wget \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a working directory
WORKDIR /app

# Create and activate virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel

# Install PDF processing dependencies not in pyproject.toml
RUN pip install pdf2image

# Install project dependencies from pyproject.toml
COPY pyproject.toml /app/
RUN pip install -e .

# Create cache directories
RUN mkdir -p /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder
ENV TRANSFORMERS_CACHE="/app/cache"
ENV HF_HOME="/app/cache"

# Copy application code
COPY src /app/src
COPY pyproject.toml /app/

# Install the application
RUN pip install -e .

# Create a non-root user
RUN useradd -m -u 1000 worker
RUN chown -R worker:worker /app /opt/venv /app/cache /app/temp_pdfs /app/.byaldi /app/offload_folder

# Switch to non-root user
USER worker

# Create logs directory
RUN mkdir -p /app/logs

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

# Expose the API port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["python", "-m", "files_api.cli"]
CMD ["worker", "--mode", "cloud"]