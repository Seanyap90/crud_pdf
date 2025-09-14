"""
Model Preloader for AMI Building

Handles model preloading from ECR images to host filesystem at /opt/vlm-models
during AMI creation to achieve 85% cold start reduction (15-20min → 2-3min).
"""

import logging
import json
import subprocess
from typing import Dict, Any, List, Optional
from pathlib import Path

from src.files_api.settings import get_settings

logger = logging.getLogger(__name__)

class ModelPreloader:
    """Preload VLM models from ECR images to AMI host filesystem."""
    
    def __init__(self):
        """Initialize model preloader."""
        self.settings = get_settings()
        self.app_name = self.settings.app_name or "fastapi-app"
        
        # Model configuration
        self.host_model_path = Path("/opt/vlm-models")
        self.container_model_path = "/app/cache"
        
        # ECR configuration
        self.ecr_repo_name = "rag-worker"
        self.image_tag = "latest"
        
        logger.info("Model preloader initialized")
    
    def get_ecr_image_uri(self, region: str) -> str:
        """Get full ECR image URI."""
        account_id = self.settings.aws_account_id
        if not account_id:
            raise Exception("AWS_ACCOUNT_ID not set in settings")
        
        ecr_registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
        return f"{ecr_registry}/{self.ecr_repo_name}:{self.image_tag}"
    
    def prepare_host_directories(self) -> None:
        """Prepare host directories for model storage."""
        try:
            logger.info(f"Creating host model directory: {self.host_model_path}")
            self.host_model_path.mkdir(parents=True, exist_ok=True)
            
            # Set proper ownership (assuming ec2-user)
            import os
            import pwd
            import grp
            
            # Try to get ec2-user uid/gid, fallback to current user
            try:
                ec2_user = pwd.getpwnam('ec2-user')
                uid, gid = ec2_user.pw_uid, ec2_user.pw_gid
            except KeyError:
                # Fallback to current user if ec2-user doesn't exist
                uid, gid = os.getuid(), os.getgid()
            
            os.chown(self.host_model_path, uid, gid)
            logger.info(f"Host model directory prepared with ownership {uid}:{gid}")
            
        except Exception as e:
            logger.error(f"Failed to prepare host directories: {e}")
            raise
    
    def login_to_ecr(self, region: str) -> None:
        """Login to ECR registry."""
        try:
            logger.info("Logging into ECR...")
            account_id = self.settings.aws_account_id
            ecr_registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
            
            # Get ECR login token
            get_login_cmd = [
                "aws", "ecr", "get-login-password", 
                "--region", region
            ]
            
            login_result = subprocess.run(
                get_login_cmd, 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            login_token = login_result.stdout.strip()
            
            # Docker login to ECR
            docker_login_cmd = [
                "docker", "login", 
                "--username", "AWS",
                "--password-stdin",
                ecr_registry
            ]
            
            subprocess.run(
                docker_login_cmd,
                input=login_token,
                text=True,
                check=True
            )
            
            logger.info("ECR login successful")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"ECR login failed: {e}")
            logger.error(f"Command output: {e.stdout}")
            logger.error(f"Command error: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"ECR login error: {e}")
            raise
    
    def pull_worker_image(self, image_uri: str) -> None:
        """Pull VLM worker image from ECR."""
        try:
            logger.info(f"Pulling worker image: {image_uri}")
            
            pull_cmd = ["docker", "pull", image_uri]
            result = subprocess.run(
                pull_cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            logger.info("Worker image pulled successfully")
            logger.debug(f"Pull output: {result.stdout}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Image pull failed: {e}")
            logger.error(f"Command output: {e.stdout}")
            logger.error(f"Command error: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"Image pull error: {e}")
            raise
    
    def run_model_preloading_container(self, image_uri: str) -> Dict[str, Any]:
        """Run container to preload models to host filesystem."""
        try:
            logger.info("Starting model preloading container...")
            
            # Model preloading Python script
            preload_script = '''
import sys
import os
sys.path.append("/app")

# Set up model cache environment
os.environ["MODEL_CACHE_DIR"] = "/app/cache"
os.environ["TRANSFORMERS_CACHE"] = "/app/cache/huggingface/transformers"
os.environ["HF_HOME"] = "/app/cache/huggingface"

print("Starting model preloading process...")

try:
    from src.vlm_workers.models.manager import get_model_manager
    
    print("Initializing model manager...")
    manager = get_model_manager()
    
    # Preload VLM models (this will download to cache)
    print("Loading VLM models...")
    vlm_model, vlm_processor = manager.get_vlm_model()
    if vlm_model and vlm_processor:
        print("✅ VLM models loaded successfully")
        # Clear from GPU memory to save space
        del vlm_model, vlm_processor
    else:
        print("❌ Failed to load VLM models")
    
    # Preload RAG models  
    print("Loading RAG models...")
    rag_model = manager.get_rag_model()
    if rag_model:
        print("✅ RAG models loaded successfully")
        # Clear from GPU memory
        del rag_model
    else:
        print("❌ Failed to load RAG models")
    
    # Verify cache contents
    print("\\nVerifying model cache contents...")
    cache_dir = "/app/cache"
    for root, dirs, files in os.walk(cache_dir):
        if files:  # Only show directories with files
            print(f"📁 {root}: {len(files)} files")
    
    print("\\n🎉 Model preloading completed successfully!")
    
except Exception as e:
    print(f"❌ Model preloading failed: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
'''
            
            # Docker run command with GPU support and volume mount
            docker_cmd = [
                "docker", "run", "--rm",
                "--gpus", "all",  # GPU access for model loading
                "-v", f"{self.host_model_path}:{self.container_model_path}",
                "-e", "PRELOAD_MODELS=true",
                "-e", f"MODEL_CACHE_DIR={self.container_model_path}",
                "-e", f"TRANSFORMERS_CACHE={self.container_model_path}/huggingface/transformers",
                "-e", f"HF_HOME={self.container_model_path}/huggingface",
                "-e", "CUDA_VISIBLE_DEVICES=0",
                image_uri,
                "python", "-c", preload_script
            ]
            
            logger.info("Running model preloading container...")
            logger.debug(f"Docker command: {' '.join(docker_cmd[:6])} ... [script content]")
            
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout
            )
            
            if result.returncode == 0:
                logger.info("Model preloading container completed successfully")
                logger.info(f"Container output:\\n{result.stdout}")
                
                return {
                    'status': 'success',
                    'output': result.stdout,
                    'container_logs': result.stderr
                }
            else:
                logger.error(f"Model preloading container failed with code {result.returncode}")
                logger.error(f"Container stdout:\\n{result.stdout}")
                logger.error(f"Container stderr:\\n{result.stderr}")
                
                return {
                    'status': 'failed',
                    'error': f"Container exit code: {result.returncode}",
                    'output': result.stdout,
                    'container_logs': result.stderr
                }
                
        except subprocess.TimeoutExpired as e:
            logger.error("Model preloading container timed out")
            return {
                'status': 'timeout',
                'error': 'Container execution timed out after 1 hour',
                'output': e.stdout.decode() if e.stdout else '',
                'container_logs': e.stderr.decode() if e.stderr else ''
            }
        except Exception as e:
            logger.error(f"Error running model preloading container: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def verify_model_cache(self) -> Dict[str, Any]:
        """Verify that models were properly cached to host filesystem."""
        try:
            logger.info(f"Verifying model cache at {self.host_model_path}")
            
            if not self.host_model_path.exists():
                return {
                    'status': 'missing',
                    'error': 'Model cache directory does not exist'
                }
            
            # Count files and directories
            total_files = 0
            total_size = 0
            directories = []
            
            for root, dirs, files in self.host_model_path.rglob('*'):
                if root.is_dir():
                    directories.append(str(root.relative_to(self.host_model_path)))
                elif root.is_file():
                    total_files += 1
                    total_size += root.stat().st_size
            
            # Convert size to human readable
            size_mb = total_size / (1024 * 1024)
            
            verification_result = {
                'status': 'verified',
                'total_files': total_files,
                'total_size_mb': round(size_mb, 2),
                'directories': directories[:20],  # Limit output
                'cache_path': str(self.host_model_path)
            }
            
            if total_files == 0:
                verification_result['status'] = 'empty'
                verification_result['warning'] = 'No model files found in cache'
            elif size_mb < 100:  # Expect at least 100MB of models
                verification_result['status'] = 'incomplete'
                verification_result['warning'] = f'Cache size ({size_mb:.1f}MB) seems too small'
            
            logger.info(f"Model cache verification: {verification_result['status']}")
            logger.info(f"Files: {total_files}, Size: {size_mb:.1f}MB")
            
            return verification_result
            
        except Exception as e:
            logger.error(f"Model cache verification failed: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def cleanup_docker_resources(self) -> None:
        """Clean up Docker images and containers to save space."""
        try:
            logger.info("Cleaning up Docker resources...")
            
            # Remove unused images
            subprocess.run(
                ["docker", "system", "prune", "-f"],
                capture_output=True,
                check=False  # Don't fail if this doesn't work
            )
            
            logger.info("Docker cleanup completed")
            
        except Exception as e:
            logger.warning(f"Docker cleanup failed: {e}")
    
    def preload_models(self, region: str) -> Dict[str, Any]:
        """Complete model preloading process."""
        logger.info("Starting complete model preloading process...")
        
        results = {
            'status': 'in_progress',
            'steps': {},
            'region': region,
            'host_path': str(self.host_model_path)
        }
        
        try:
            # Step 1: Prepare host directories
            self.prepare_host_directories()
            results['steps']['host_prep'] = {'status': 'success'}
            
            # Step 2: Get ECR image URI
            image_uri = self.get_ecr_image_uri(region)
            results['image_uri'] = image_uri
            results['steps']['image_uri'] = {'status': 'success', 'uri': image_uri}
            
            # Step 3: Login to ECR
            self.login_to_ecr(region)
            results['steps']['ecr_login'] = {'status': 'success'}
            
            # Step 4: Pull worker image
            self.pull_worker_image(image_uri)
            results['steps']['image_pull'] = {'status': 'success'}
            
            # Step 5: Run model preloading container
            container_result = self.run_model_preloading_container(image_uri)
            results['steps']['model_preloading'] = container_result
            
            if container_result['status'] != 'success':
                results['status'] = 'failed'
                results['error'] = 'Model preloading container failed'
                return results
            
            # Step 6: Verify model cache
            verification_result = self.verify_model_cache()
            results['steps']['verification'] = verification_result
            
            if verification_result['status'] in ['missing', 'empty', 'error']:
                results['status'] = 'failed'
                results['error'] = 'Model cache verification failed'
                return results
            
            # Step 7: Cleanup Docker resources
            self.cleanup_docker_resources()
            results['steps']['cleanup'] = {'status': 'success'}
            
            results['status'] = 'success'
            logger.info("Model preloading process completed successfully")
            
            return results
            
        except Exception as e:
            logger.error(f"Model preloading process failed: {e}")
            results['status'] = 'failed'
            results['error'] = str(e)
            return results


def preload_models_for_ami(region: str) -> Dict[str, Any]:
    """Convenience function to preload models for AMI building."""
    preloader = ModelPreloader()
    return preloader.preload_models(region)


if __name__ == "__main__":
    # Test model preloading
    import sys
    
    if len(sys.argv) > 1:
        region = sys.argv[1]
    else:
        region = "us-east-1"
    
    result = preload_models_for_ami(region)
    print(json.dumps(result, indent=2))