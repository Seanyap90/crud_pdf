"""GPU configuration management for VLM workers."""
from .gpu_config import GPUConfigManager, get_gpu_optimized_config

__all__ = ['GPUConfigManager', 'get_gpu_optimized_config']
