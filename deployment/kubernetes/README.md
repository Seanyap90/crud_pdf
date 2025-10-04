# Kubernetes Deployment (Future)

## Overview

This directory contains placeholder configurations for future Kubernetes deployment of the VLM document processing system.

## Architecture Approach

The Kubernetes deployment will mirror the **aws-mock** volume sharing pattern using persistent volumes, rather than the AMI-based approach used in aws-prod.

### Volume Strategy

```yaml
# Similar to aws-mock Docker Compose pattern
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: vlm-models-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 100Gi
  storageClassName: shared-storage
```

### Model Loading Pattern

1. **Model Downloader Job**: One-time job to download models to persistent volume
2. **VLM Worker Deployment**: Multiple replicas sharing the same persistent volume

This follows the same pattern as aws-mock:
- `model-downloader` → Downloads models once
- `vlm-worker` → Uses cached models with `HF_HUB_OFFLINE=1`

## Deployment Modes Comparison

| Mode | Model Storage | Volume Type | Scaling |
|------|--------------|-------------|---------|
| **local-dev** | Local cache | Local directory | Single instance |
| **aws-mock** | Docker volume | Docker volume | Docker Compose |
| **aws-prod** | AMI host path | Host path mount | EventBridge Lambda |
| **kubernetes** | Persistent volume | PVC (ReadWriteMany) | HPA/VPA |

## Future Implementation

When implementing Kubernetes deployment:

1. **Persistent Volume Strategy**
   - Use ReadWriteMany storage class
   - Pre-load models with init containers or jobs
   - Share volume across worker pods

2. **Configuration**
   - ConfigMaps for environment variables
   - Secrets for sensitive data (S3 credentials, etc.)
   - Similar environment variables as aws-mock

3. **Scaling**
   - HorizontalPodAutoscaler based on SQS queue depth
   - VerticalPodAutoscaler for resource optimization
   - GPU node affinity and resource requests

4. **Networking**
   - Service mesh or ingress for external access
   - Internal DNS for service discovery
   - NetworkPolicies for security

## Directory Structure

```
deployment/kubernetes/
├── README.md                    # This file
├── manifests/
│   ├── namespace.yaml           # Future: Kubernetes namespace
│   ├── persistent-volume.yaml   # Future: Shared model storage
│   ├── model-downloader.yaml    # Future: Init job for models
│   ├── vlm-worker.yaml          # Future: Main worker deployment
│   ├── configmap.yaml           # Future: Environment configuration
│   ├── secrets.yaml             # Future: Sensitive configuration
│   └── ingress.yaml             # Future: External access
├── charts/                      # Future: Helm charts
└── kustomization/               # Future: Kustomize overlays
```

## Notes

- This is a **placeholder** directory for future Kubernetes support
- The implementation will reuse Docker images from the aws-mock build process
- Volume sharing strategy mirrors successful aws-mock pattern
- AMI approach is AWS-specific and not applicable to Kubernetes