#!/usr/bin/env python3
"""
Model downloader for VLM+RAG pipeline.
Downloads required models to a shared cache directory.
Supports both Docker volumes (deploy-aws-local) and EFS mounts (deploy-aws).
"""

import os
import sys
import logging
import argparse
import time
import hashlib
from pathlib import Path
from huggingface_hub import snapshot_download

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Models to download
MODELS = [
    {
        'repo_id': 'HuggingFaceTB/SmolVLM-Instruct',
        'name': 'SmolVLM-Instruct',
        'description': 'Vision Language Model for document understanding'
    },
    {
        'repo_id': 'vidore/colpali',
        'name': 'ColPali',
        'description': 'RAG Multi-Modal Model for document retrieval'
    },
    {
        'repo_id': 'vidore/colpaligemma-3b-mix-448-base',
        'name': 'ColPaliGemma',
        'description': 'Alternative RAG model for document processing'
    }
]

def check_model_exists(repo_id: str, cache_dir: str, mode: str = "standard") -> bool:
    """Check if a model already exists in the cache directory."""
    # Convert repo_id to expected cache directory format
    model_dir = repo_id.replace('/', '--')
    model_path = Path(cache_dir) / f"models--{model_dir}"
    
    # For EFS mode, also check for completion marker
    if mode == "efs-downloader":
        completion_marker = model_path / ".model_ready"
        exists = model_path.exists() and completion_marker.exists()
        logger.info(f"Model {repo_id}: {'✓ Found' if exists else '✗ Missing'} at {model_path} (EFS mode)")
    else:
        exists = model_path.exists() and any(model_path.iterdir())
        logger.info(f"Model {repo_id}: {'✓ Found' if exists else '✗ Missing'} at {model_path}")
    
    return exists

def download_model(repo_id: str, name: str, description: str, cache_dir: str, mode: str = "standard", timeout: int = 3600) -> bool:
    """Download a single model to the cache directory with timeout support."""
    try:
        logger.info(f"📥 Downloading {name} ({repo_id})")
        logger.info(f"    Description: {description}")
        logger.info(f"    Cache dir: {cache_dir}")
        logger.info(f"    Mode: {mode}")
        logger.info(f"    Timeout: {timeout}s")
        
        start_time = time.time()
        
        # Download with timeout handling
        snapshot_download(
            repo_id=repo_id,
            cache_dir=cache_dir,
            local_files_only=False,
            token=None  # Use anonymous access
        )
        
        duration = time.time() - start_time
        logger.info(f"✅ {name} downloaded successfully in {duration:.1f}s")
        
        # For EFS mode, create completion marker
        if mode == "efs-downloader":
            create_completion_marker(repo_id, cache_dir, duration)
        
        return True
        
    except Exception as e:
        duration = time.time() - start_time if 'start_time' in locals() else 0
        logger.error(f"❌ Failed to download {name} after {duration:.1f}s: {str(e)}")
        
        # Check if timeout exceeded
        if duration >= timeout:
            logger.error(f"⏱️ Download timeout exceeded ({timeout}s)")
        
        return False


def create_completion_marker(repo_id: str, cache_dir: str, duration: float) -> None:
    """Create completion marker for EFS mode."""
    try:
        model_dir = repo_id.replace('/', '--')
        model_path = Path(cache_dir) / f"models--{model_dir}"
        completion_marker = model_path / ".model_ready"
        
        # Create marker with metadata
        marker_data = {
            "repo_id": repo_id,
            "downloaded_at": time.time(),
            "duration_seconds": duration,
            "mode": "efs-downloader"
        }
        
        completion_marker.write_text(f"{marker_data}\n")
        logger.info(f"📄 Created completion marker: {completion_marker}")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create completion marker: {e}")


def validate_efs_integrity(cache_dir: str) -> bool:
    """Validate EFS model integrity and accessibility."""
    try:
        cache_path = Path(cache_dir)
        
        # Check if EFS mount is accessible
        if not cache_path.exists():
            logger.error(f"❌ EFS mount not accessible: {cache_path}")
            return False
        
        # Check write permissions
        test_file = cache_path / ".efs_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            logger.info(f"✅ EFS write access confirmed: {cache_path}")
        except Exception as e:
            logger.error(f"❌ EFS write access failed: {e}")
            return False
        
        # Check disk space (basic)
        try:
            stat = os.statvfs(cache_path)
            free_bytes = stat.f_bavail * stat.f_frsize
            free_gb = free_bytes / (1024**3)
            logger.info(f"💾 EFS free space: {free_gb:.1f} GB")
            
            if free_gb < 5:  # Minimum 5GB required
                logger.warning(f"⚠️ Low EFS space: {free_gb:.1f} GB")
                return False
                
        except Exception as e:
            logger.warning(f"⚠️ Could not check EFS disk space: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ EFS validation failed: {e}")
        return False

def main():
    """Main function to download all required models."""
    parser = argparse.ArgumentParser(description="Download VLM models with EFS support")
    parser.add_argument("--mode", choices=["standard", "efs-downloader"], 
                       default="standard", help="Download mode")
    parser.add_argument("--timeout", type=int, default=3600, 
                       help="Download timeout in seconds")
    parser.add_argument("--cache-dir", help="Override cache directory")
    parser.add_argument("--validate-only", action="store_true",
                       help="Only validate EFS without downloading")
    
    args = parser.parse_args()
    
    # Get cache directory from args, environment, or use default
    if args.cache_dir:
        cache_dir = args.cache_dir
    elif args.mode == "efs-downloader":
        cache_dir = os.environ.get('MODEL_CACHE_DIR', '/app/cache')
    else:
        cache_dir = os.environ.get('TRANSFORMERS_CACHE', '/app/cache')
    
    logger.info("🚀 Model Download Service Starting")
    logger.info(f"📁 Cache directory: {cache_dir}")
    logger.info(f"🔧 Mode: {args.mode}")
    logger.info(f"⏱️ Timeout: {args.timeout}s")
    logger.info(f"🌐 HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE', 'not set')}")
    
    # EFS validation for efs-downloader mode
    if args.mode == "efs-downloader":
        logger.info("🔍 Validating EFS integrity...")
        if not validate_efs_integrity(cache_dir):
            logger.error("❌ EFS validation failed!")
            return 1
        logger.info("✅ EFS validation passed")
        
        if args.validate_only:
            logger.info("✅ EFS validation completed (validate-only mode)")
            return 0
    
    # Ensure we can download (not offline)
    if os.environ.get('HF_HUB_OFFLINE', '0') == '1':
        logger.error("❌ HF_HUB_OFFLINE=1 - Cannot download models in offline mode!")
        logger.error("💡 This container should have HF_HUB_OFFLINE=0")
        return 1
    
    # Create cache directory if it doesn't exist
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"📂 Cache directory created/verified: {cache_path}")
    
    # Check which models already exist
    logger.info("🔍 Checking existing models...")
    existing_models = []
    missing_models = []
    
    for model in MODELS:
        if check_model_exists(model['repo_id'], cache_dir, args.mode):
            existing_models.append(model)
        else:
            missing_models.append(model)
    
    # Report status
    if existing_models:
        logger.info(f"✅ Found {len(existing_models)} existing models:")
        for model in existing_models:
            logger.info(f"    - {model['name']}")
    
    if not missing_models:
        logger.info("🎉 All models already present! No downloads needed.")
        logger.info("✅ Model downloader service completed successfully")
        return 0
    
    logger.info(f"📦 Need to download {len(missing_models)} models:")
    for model in missing_models:
        logger.info(f"    - {model['name']} ({model['repo_id']})")
    
    # Download missing models
    download_success = True
    total_start = time.time()
    
    for i, model in enumerate(missing_models, 1):
        logger.info(f"\n📥 [{i}/{len(missing_models)}] Downloading {model['name']}...")
        
        if not download_model(
            repo_id=model['repo_id'],
            name=model['name'],
            description=model['description'],
            cache_dir=cache_dir,
            mode=args.mode,
            timeout=args.timeout
        ):
            download_success = False
            break
    
    total_duration = time.time() - total_start
    
    # Final status
    if download_success:
        logger.info(f"\n🎉 All model downloads completed successfully in {total_duration:.1f}s!")
        logger.info("✅ VLM+RAG worker containers can now start inference")
        
        if args.mode == "efs-downloader":
            logger.info("💾 Models cached in EFS for persistent storage")
            logger.info("🚀 ECS workers can now access pre-downloaded models")
        
        logger.info("🔄 Model downloader service will now exit")
        return 0
    else:
        logger.error(f"\n💥 Model download failed after {total_duration:.1f}s!")
        logger.error("❌ Check network connection and HuggingFace availability")
        
        if args.mode == "efs-downloader":
            logger.error("💾 EFS models may be incomplete - workers will retry")
        
        logger.error("🔄 Model downloader service exiting with error")
        return 1

if __name__ == "__main__":
    sys.exit(main())