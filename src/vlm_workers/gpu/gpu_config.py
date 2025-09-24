"""
GPU Configuration Management for VLM Workers

This module provides GPU-aware configuration for VLM workers based on deployment mode:
- aws-mock: RTX 4060 8GB optimization (quantization enabled)
- aws-prod: Tesla T4 16GB optimization (quantization disabled)
"""

import os
import logging
from typing import Dict, Any, Optional
import torch

try:
    from transformers import BitsAndBytesConfig
except ImportError:
    BitsAndBytesConfig = None

logger = logging.getLogger(__name__)


def get_gpu_optimized_config(deployment_mode: str) -> Dict[str, Any]:
    """
    Get GPU-optimized configuration based on deployment mode.

    Args:
        deployment_mode: 'aws-mock' for RTX 4060 or 'aws-prod' for Tesla T4

    Returns:
        Dictionary with GPU optimization settings
    """
    configs = {
        'aws-mock': {
            # RTX 4060 8GB - Memory constrained, use quantization
            'use_quantization': True,
            'model_memory_limit': '7GiB',
            'device_map': 'auto',  # Auto CPU/GPU split for memory management
            'cache_implementation': 'offloaded',  # Save VRAM with disk cache
            'offload_to_cpu': True,  # Enable CPU offloading when needed
            'cuda_alloc_conf': 'max_split_size_mb:128,garbage_collection_threshold:0.8',
            'torch_dtype': torch.float16,
            'max_memory': {0: '7GiB'}
        },
        'aws-prod': {
            # Tesla T4 16GB - Memory abundant, disable quantization
            'use_quantization': False,
            'model_memory_limit': '14GiB',
            'device_map': 'cuda:0',  # Force single GPU, no auto-split
            'cache_implementation': 'standard',  # Use VRAM for cache
            'offload_to_cpu': False,  # Keep everything on GPU
            'cuda_alloc_conf': 'max_split_size_mb:1024,garbage_collection_threshold:0.6',
            'torch_dtype': torch.float16,
            'max_memory': {0: '14GiB'}
        }
    }

    # Default to aws-mock configuration if mode not recognized
    config = configs.get(deployment_mode, configs['aws-mock'])

    logger.info(f"Using GPU configuration for {deployment_mode}: {config}")
    return config


class GPUConfigManager:
    """Manager for GPU-specific configuration based on deployment mode."""

    def __init__(self, deployment_mode: str = None):
        """
        Initialize GPU configuration manager.

        Args:
            deployment_mode: Deployment mode, defaults to DEPLOYMENT_MODE env var
        """
        self.mode = deployment_mode or os.environ.get('DEPLOYMENT_MODE', 'aws-mock')
        self.config = get_gpu_optimized_config(self.mode)

        logger.info(f"Initialized GPUConfigManager for mode: {self.mode}")

    def get_quantization_config(self) -> Optional[BitsAndBytesConfig]:
        """
        Return quantization config for aws-mock, None for aws-prod.

        Returns:
            BitsAndBytesConfig for quantization or None
        """
        if not self.config['use_quantization']:
            logger.info("Quantization disabled for deployment mode: %s", self.mode)
            return None

        if BitsAndBytesConfig is None:
            logger.warning("BitsAndBytesConfig not available, skipping quantization")
            return None

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16
        )

        logger.info("Created quantization config for mode: %s", self.mode)
        return quantization_config

    def get_model_loading_params(self) -> Dict[str, Any]:
        """
        Return model loading parameters (device_map, max_memory, etc.).

        Returns:
            Dictionary with model loading parameters
        """
        params = {
            'device_map': self.config['device_map'],
            'torch_dtype': self.config['torch_dtype'],
            'max_memory': self.config['max_memory'],
            'local_files_only': True
        }

        logger.info("Model loading params for %s: %s", self.mode, params)
        return params

    def get_cuda_allocator_config(self) -> str:
        """
        Return PYTORCH_CUDA_ALLOC_CONF string.

        Returns:
            CUDA allocator configuration string
        """
        cuda_config = self.config['cuda_alloc_conf']
        logger.info("CUDA allocator config for %s: %s", self.mode, cuda_config)
        return cuda_config

    def get_cache_config(self) -> str:
        """
        Return cache implementation configuration.

        Returns:
            Cache implementation type ('standard' or 'offloaded')
        """
        cache_config = self.config['cache_implementation']
        logger.info("Cache config for %s: %s", self.mode, cache_config)
        return cache_config

    def get_memory_config(self) -> str:
        """
        Return memory limit configuration.

        Returns:
            Memory limit string (e.g., '7GiB', '14GiB')
        """
        memory_config = self.config['model_memory_limit']
        logger.info("Memory config for %s: %s", self.mode, memory_config)
        return memory_config

    def should_offload_to_cpu(self) -> bool:
        """
        Return whether CPU offloading should be enabled.

        Returns:
            True if CPU offloading should be enabled
        """
        offload = self.config['offload_to_cpu']
        logger.info("CPU offloading for %s: %s", self.mode, offload)
        return offload

    def apply_environment_overrides(self) -> None:
        """
        Apply environment variable overrides for current mode.

        This method sets environment variables that will be used by the model
        loading process to optimize for the current GPU configuration.
        """
        overrides = {
            'MODEL_MEMORY_LIMIT': self.get_memory_config(),
            'PYTORCH_CUDA_ALLOC_CONF': self.get_cuda_allocator_config(),
            'CACHE_IMPLEMENTATION': self.get_cache_config(),
            'OFFLOAD_TO_CPU': 'true' if self.should_offload_to_cpu() else 'false'
        }

        for key, value in overrides.items():
            os.environ[key] = value
            logger.info("Set environment override %s=%s for mode %s", key, value, self.mode)

    def get_generation_config_params(self) -> Dict[str, Any]:
        """
        Return generation configuration parameters.

        Returns:
            Dictionary with generation config parameters
        """
        params = {
            'use_cache': True,
            'do_sample': False,
            'num_beams': 1
        }

        # Only set cache_implementation if not using standard (default)
        if self.config['cache_implementation'] != 'standard':
            params['cache_implementation'] = self.config['cache_implementation']

        logger.info("Generation config params for %s: %s", self.mode, params)
        return params

    def validate_deployment_mode(self) -> str:
        """
        Validate and return the deployment mode with fallback.

        Returns:
            Validated deployment mode
        """
        valid_modes = ['aws-mock', 'aws-prod']

        if self.mode not in valid_modes:
            logger.warning(
                "Invalid deployment mode '%s', falling back to 'aws-mock'. "
                "Valid modes: %s", self.mode, valid_modes
            )
            self.mode = 'aws-mock'
            self.config = get_gpu_optimized_config(self.mode)

        return self.mode