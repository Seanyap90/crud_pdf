# AMI Manual Build Guide

## Overview

This guide provides step-by-step instructions for manually creating a custom AMI optimized for Tesla T4 GPU instances with pre-loaded VLM models. This approach reduces cold start times from 15-20 minutes to 2-3 minutes (85% improvement).

## Prerequisites

- AWS CLI configured with appropriate permissions
- Access to create EC2 instances and AMIs
- VPC with public subnet configured (via console)
- Security groups for ECS workers configured

## Architecture Overview

- **Base AMI**: Amazon ECS-optimized GPU AMI
- **Instance Type**: g4dn.xlarge (Tesla T4 GPU)
- **Model Storage**: `/opt/vlm-models` (host path)
- **Container Mount**: Models mounted to `/app/cache` in containers

## Step 1: Launch Base Instance

### 1.1 Find Latest ECS GPU AMI

```bash
# Get the latest ECS GPU-optimized AMI ID
aws ec2 describe-images \
    --owners amazon \
    --filters "Name=name,Values=amzn2-ami-ecs-gpu-hvm-*" "Name=state,Values=available" \
    --query 'Images | sort_by(@, &CreationDate) | [-1].{ImageId:ImageId,Name:Name,CreationDate:CreationDate}' \
    --output table
```

### 1.2 Launch Instance

```bash
# Replace variables with your specific values
VPC_ID="vpc-xxxxxxxxx"
SUBNET_ID="subnet-xxxxxxxxx"
SECURITY_GROUP_ID="sg-xxxxxxxxx"
KEY_PAIR_NAME="your-key-pair"
BASE_AMI_ID="ami-xxxxxxxxx"  # From step 1.1

# Launch instance
aws ec2 run-instances \
    --image-id $BASE_AMI_ID \
    --instance-type g4dn.xlarge \
    --key-name $KEY_PAIR_NAME \
    --security-group-ids $SECURITY_GROUP_ID \
    --subnet-id $SUBNET_ID \
    --associate-public-ip-address \
    --block-device-mappings '[
        {
            "DeviceName": "/dev/xvda",
            "Ebs": {
                "VolumeSize": 100,
                "VolumeType": "gp3",
                "DeleteOnTermination": true
            }
        }
    ]' \
    --tag-specifications 'ResourceType=instance,Tags=[
        {Key=Name,Value=vlm-ami-builder},
        {Key=Purpose,Value=AMI-Building}
    ]'
```

### 1.3 Connect to Instance

```bash
# Get instance public IP
INSTANCE_ID="i-xxxxxxxxx"  # From step 1.2 output
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# SSH to instance
ssh -i ~/.ssh/your-key.pem ec2-user@$PUBLIC_IP
```

## Step 2: Setup System and Install Dependencies

### 2.1 Switch to Root and Update System

```bash
# Switch to root user for easier system management
sudo su

# Update system
yum update -y

# Install unzip for AWS CLI installation
yum install unzip -y
```

**Note**: ECS-optimized AMI already includes Docker and ECS agent pre-installed.

### 2.2 Install AWS CLI

```bash
# Download and install AWS CLI v2
cd /tmp
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install

# Add AWS CLI to PATH
echo 'export PATH=$PATH:/usr/local/bin' >> ~/.bashrc
source ~/.bashrc

# Verify installation
aws --version
```

### 2.3 Verify GPU Access

```bash
# Verify NVIDIA drivers are installed
nvidia-smi

# Test Docker GPU access
docker run --rm --gpus all nvidia/cuda:11.8-base-ubuntu20.04 nvidia-smi
```

## Step 3: Pre-load VLM Models Using Docker

### 3.1 Create Model Directory

```bash
# Create model storage directory (as root)
mkdir -p /opt/vlm-models
```

### 3.2 Pull RAG-Worker Image from ECR

```bash
# Login to ECR (replace region and account ID as needed)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 167349419537.dkr.ecr.us-east-1.amazonaws.com

# Pull the rag-worker image
docker pull 167349419537.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest
```

### 3.3 Download Models Using Container

```bash
# Run model downloader using Docker container
docker run \
    --network host \
    --user root \
    --name check \
    --rm \
    --gpus all \
    -v /opt/vlm-models:/app/cache \
    -e PRELOAD_MODELS=true \
    -e MODEL_CACHE_DIR=/app/cache \
    -e TRANSFORMERS_CACHE=/app/cache \
    -e HF_HOME=/app/cache \
    -e HF_HUB_OFFLINE=0 \
    -e CUDA_VISIBLE_DEVICES=0 \
    167349419537.dkr.ecr.us-east-1.amazonaws.com/rag-worker \
    python3 -m vlm_workers.models.downloader
```

### 3.4 Ensure Models are Written to Disk

```bash
# Force filesystem sync to ensure all data is written to disk
sync

# Verify model download
ls -la /opt/vlm-models/
du -sh /opt/vlm-models/*

# Verify specific models exist
ls -la /opt/vlm-models/models--vidore--colpali/ 2>/dev/null || echo "ColPali model not found"
ls -la /opt/vlm-models/models--HuggingFaceTB--SmolVLM-Instruct/ 2>/dev/null || echo "SmolVLM model not found"
```

## Step 4: Create AMI

### 4.1 Clean Up Instance

```bash
# Clean up temporary files and logs
sudo yum clean all
sudo rm -rf /var/log/*
sudo rm -rf /tmp/*
sudo rm -rf ~/.bash_history
history -c

# Remove download script
rm -f download_models.py

# Stop unnecessary services
sudo systemctl stop docker
```

### 4.2 Create AMI from AWS Console

1. **Navigate to EC2 Console**
   - Go to AWS EC2 Console
   - Select your instance (`vlm-ami-builder`)

2. **Create Image**
   - Right-click instance → "Create Image"
   - **Image name**: `fastapi-app-vlm-gpu-YYYYMMDD-HHMM`
   - **Description**: `Custom AMI with pre-loaded VLM models for Tesla T4 GPU`
   - **No reboot**: Uncheck (allow reboot for consistency)
   - Click "Create Image"

3. **Monitor AMI Creation**
   - Go to "AMIs" in left sidebar
   - Wait for status to change from "pending" to "available"
   - This typically takes 10-15 minutes

### 4.3 Get AMI ID

```bash
# Get the newly created AMI ID
aws ec2 describe-images \
    --owners self \
    --filters "Name=name,Values=fastapi-app-vlm-gpu-*" \
    --query 'Images | sort_by(@, &CreationDate) | [-1].{ImageId:ImageId,Name:Name,CreationDate:CreationDate}' \
    --output table
```

## Step 5: Configure Deployment

### 5.1 Update Environment Configuration

```bash
# In your local project directory, update .env.deploy-aws
# Replace AMI_ID_HERE with your actual AMI ID

echo "CUSTOM_AMI_ID=ami-xxxxxxxxx" >> .env.deploy-aws
```

### 5.2 Verify AMI Configuration

```bash
# Test AMI availability
AMI_ID="ami-xxxxxxxxx"  # Your AMI ID
aws ec2 describe-images --image-ids $AMI_ID --query 'Images[0].{State:State,Name:Name,Description:Description}'
```

## Step 6: Clean Up

### 6.1 Terminate Builder Instance

```bash
# Terminate the instance used for building
aws ec2 terminate-instances --instance-ids $INSTANCE_ID
```

### 6.2 Verify AMI in ECS Deployment

```bash
# Test deployment with new AMI
make deploy-aws
```

## Troubleshooting

### Common Issues

1. **GPU Not Detected**
   ```bash
   # Verify NVIDIA drivers
   nvidia-smi
   # If not working, restart instance
   sudo reboot
   ```

2. **Model Download Fails**
   ```bash
   # Check internet connectivity
   curl -I https://huggingface.co
   # Check disk space
   df -h /opt/vlm-models
   ```

3. **AMI Too Large**
   ```bash
   # Clean up unnecessary files
   sudo docker system prune -a -f
   sudo yum clean all
   ```

### Performance Validation

After deployment, verify Tesla T4 optimization:

```bash
# Check ECS task logs for GPU configuration
aws logs describe-log-groups --log-group-name-prefix "/ecs/fastapi-app-vlm-worker-ami"

# Look for these log entries:
# ✓ GPU configuration initialized for mode: deploy-aws
# - Model memory limit: 14GiB
# - Use quantization: false
```

## Expected Performance

- **Cold Start Time**: 2-3 minutes (vs 15-20 minutes without AMI)
- **GPU Memory Utilization**: 85-90% (14GB/16GB)
- **Performance Improvement**: 3-5x faster inference vs quantized RTX 4060

## Security Notes

- AMI contains pre-loaded public models only
- No sensitive data or keys stored in AMI
- Standard ECS security practices apply
- Models are cached in read-only fashion

## Cost Optimization

- AMI creation is one-time cost (~$5-10)
- Reduces ECS startup time and associated costs
- Enable auto-scaling with faster response times
- Consider regional AMI copying for multi-region deployments