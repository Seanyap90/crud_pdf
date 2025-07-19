"""
VLM model management components.

Handles model loading, downloading, and management for different deployment modes:
- Local development (direct GPU access)
- Container environments (Docker volumes and EFS mounts)
- Unified model loading interface
"""