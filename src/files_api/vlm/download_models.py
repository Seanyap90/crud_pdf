#!/usr/bin/env python3
"""
Model downloader for VLM+RAG pipeline.
Downloads required models to a shared cache directory.
"""

import os
import sys
import logging
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

def check_model_exists(repo_id: str, cache_dir: str) -> bool:
    """Check if a model already exists in the cache directory."""
    # Convert repo_id to expected cache directory format
    model_dir = repo_id.replace('/', '--')
    model_path = Path(cache_dir) / f"models--{model_dir}"
    
    exists = model_path.exists() and any(model_path.iterdir())
    logger.info(f"Model {repo_id}: {'✓ Found' if exists else '✗ Missing'} at {model_path}")
    return exists

def download_model(repo_id: str, name: str, description: str, cache_dir: str) -> bool:
    """Download a single model to the cache directory."""
    try:
        logger.info(f"📥 Downloading {name} ({repo_id})")
        logger.info(f"    Description: {description}")
        logger.info(f"    Cache dir: {cache_dir}")
        
        snapshot_download(
            repo_id=repo_id,
            cache_dir=cache_dir,
            local_files_only=False,
            token=None  # Use anonymous access
        )
        
        logger.info(f"✅ {name} downloaded successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to download {name}: {str(e)}")
        return False

def main():
    """Main function to download all required models."""
    # Get cache directory from environment or use default
    cache_dir = os.environ.get('TRANSFORMERS_CACHE', '/app/cache')
    
    logger.info("🚀 Model Download Service Starting")
    logger.info(f"📁 Cache directory: {cache_dir}")
    logger.info(f"🌐 HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE', 'not set')}")
    
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
        if check_model_exists(model['repo_id'], cache_dir):
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
    for i, model in enumerate(missing_models, 1):
        logger.info(f"\n📥 [{i}/{len(missing_models)}] Downloading {model['name']}...")
        
        if not download_model(
            repo_id=model['repo_id'],
            name=model['name'],
            description=model['description'],
            cache_dir=cache_dir
        ):
            download_success = False
            break
    
    # Final status
    if download_success:
        logger.info("\n🎉 All model downloads completed successfully!")
        logger.info("✅ VLM+RAG worker containers can now start inference")
        logger.info("🔄 Model downloader service will now exit")
        return 0
    else:
        logger.error("\n💥 Model download failed!")
        logger.error("❌ Check network connection and HuggingFace availability")
        logger.error("🔄 Model downloader service exiting with error")
        return 1

if __name__ == "__main__":
    sys.exit(main())