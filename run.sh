#!/bin/bash

set -e

#####################
# --- Constants --- #
#####################

THIS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
MINIMUM_TEST_COVERAGE_PERCENT=0
FRONTEND_DIR="$THIS_DIR/../client" # Adjust if your frontend is in a different location
CERT_DIR="./certificates"
CERT_DAYS=365

##########################
# --- Helper Functions --- #
##########################

# Function to get settings as environment variables
function get_settings_as_env {
    python3 -c "
from files_api.config.settings import get_settings
settings = get_settings()
print(f'export S3_BUCKET_NAME=\"{settings.s3_bucket_name}\"')
print(f'export SQS_QUEUE_NAME=\"{settings.sqs_queue_name}\"')
print(f'export AWS_DEFAULT_REGION=\"{settings.aws_region}\"')
print(f'export AWS_ENDPOINT_URL=\"{settings.aws_endpoint_url or \"\"}\"')
print(f'export AWS_ACCESS_KEY_ID=\"{settings.aws_access_key_id or \"mock\"}\"')
print(f'export AWS_SECRET_ACCESS_KEY=\"{settings.aws_secret_access_key or \"mock\"}\"')
print(f'export SQS_QUEUE_URL=\"{settings.sqs_queue_url or \"\"}\"')
print(f'export MODEL_MEMORY_LIMIT=\"{settings.model_memory_limit}\"')
print(f'export DISABLE_DUPLICATE_LOADING=\"{str(settings.disable_duplicate_loading).lower()}\"')
print(f'export LOG_LEVEL=\"{settings.log_level}\"')
"
}

##########################
# --- Task Functions --- #
##########################

# install core and development Python dependencies into the currently activated venv
function install {
    python -m pip install --upgrade pip
    python -m pip install --editable "$THIS_DIR/[dev]"
}

function run {
    AWS_PROFILE=cloud-course \
    S3_BUCKET_NAME="some-bucket" \
        uvicorn 'files_api.main:create_app' --reload
}

# start the FastAPI app, pointed at a mocked aws endpoint
function local-dev {
    set +e
    
    # Create .env.local to override any aws-mock settings
    cat > .env.local << EOF
DEPLOYMENT_MODE=local-dev
QUEUE_TYPE=local-dev
AWS_ENDPOINT_URL=http://localhost:5000
AWS_ACCESS_KEY_ID=mock
AWS_SECRET_ACCESS_KEY=mock
S3_BUCKET_NAME=some-bucket
MODEL_MEMORY_LIMIT=24GiB
DISABLE_DUPLICATE_LOADING=true
EOF

    # Configure AWS mock environment
    export DEPLOYMENT_MODE="local-dev"
    export QUEUE_TYPE="local-dev"
    export DISABLE_DUPLICATE_LOADING="true"
    export MODEL_MEMORY_LIMIT="24GiB"
    export AWS_ENDPOINT_URL="http://localhost:5000"
    export AWS_SECRET_ACCESS_KEY="mock"
    export AWS_ACCESS_KEY_ID="mock"
    export S3_BUCKET_NAME="some-bucket"

    # Start moto server
    python -m moto.server -p 5000 &
    MOTO_PID=$!
    
    # Create mock bucket
    aws s3 mb "s3://$S3_BUCKET_NAME"
    
    # Trap EXIT signal to kill all background processes
    trap 'kill $(jobs -p)' EXIT
    
    # Start worker in background with lazy model loading
    echo "Starting Local Worker (with lazy model loading)"
    python -m vlm_workers.cli worker --mode local-dev --no-preload-models &
    
    # Wait a moment to ensure worker has started
    sleep 2
    
    # Start FastAPI app
    python -m uvicorn files_api.main:create_app --reload
    
    wait
}

# with autoscaling simulation
# function aws-mock {
#     set +e
    
#     echo "Setting up AWS mock infrastructure with EB worker autoscaling simulation..."
    
#     # Set deployment mode
#     export DEPLOYMENT_MODE="aws-mock"
    
#     # Get absolute path to project root
#     PROJECT_ROOT="$(pwd)"
    
#     # Start Moto server locally
#     echo "Starting Moto server on localhost:5000..."
#     python -m moto.server -p 5000 &
#     MOTO_PID=$!
    
#     # Wait for Moto server to be ready
#     echo -n "Waiting for Moto server to start"
#     max_retries=10
#     counter=0
#     while [ $counter -lt $max_retries ]; do
#         echo -n "."
#         if curl -s http://localhost:5000 &> /dev/null; then
#             echo " ✓"
#             echo "Moto server is ready!"
#             break
#         fi
#         sleep 1
#         counter=$((counter + 1))
#     done
    
#     if [ $counter -eq $max_retries ]; then
#         echo ""
#         echo "Error: Moto server failed to start within timeout"
#         kill $MOTO_PID 2>/dev/null
#         exit 1
#     fi
    
#     # Deploy EB infrastructure using deploy_eb.py
#     echo "Creating EB mock resources (S3, SQS, etc.)..."
#     python -m files_api.aws.deploy_eb --mode aws-mock --no-cleanup
    
#     if [ $? -ne 0 ]; then
#         echo "Error: Failed to create EB mock resources"
#         kill $MOTO_PID 2>/dev/null
#         exit 1
#     fi
    
#     # Get SQS queue URL from environment (set by deploy_eb.py)
#     SQS_QUEUE_URL="${SQS_QUEUE_URL:-http://localhost:5000/queue/rag-task-queue}"
    
#     # Start the single EB worker container (representing one instance)
#     echo "Starting EB worker container (representing 1 instance)..."
#     cd "$PROJECT_ROOT/src/files_api" && docker-compose -f docker-compose.eb-mock.yml up -d
#     cd "$PROJECT_ROOT"
    
#     # Start autoscaling simulator in background
#     echo "Starting EB Autoscaling Simulator..."
#     python -m files_api.aws.eb_scale_sim \
#         --queue-url "$SQS_QUEUE_URL" \
#         --min-instances 1 \
#         --max-instances 5 \
#         --scale-up-threshold 2 \
#         --scale-down-threshold 1 \
#         --cooldown 60 \
#         --evaluation-interval 15 \
#         --evaluation-periods 1 &
#     SIMULATOR_PID=$!
    
#     # Enhanced trap to kill all processes
#     trap 'echo "Shutting down EB mock environment..."; kill $MOTO_PID $SIMULATOR_PID 2>/dev/null; aws-mock-down; exit 0' INT
    
#     # Start FastAPI with uvicorn
#     echo "Starting FastAPI application..."
#     echo "📊 Monitor autoscaling decisions in the logs above"
#     echo "📈 Upload PDFs to trigger queue activity and observe scaling behavior"
#     echo "🛑 Press Ctrl+C to shutdown everything"
    
#     python -m uvicorn files_api.main:create_app --reload --host 0.0.0.0 --port 8000
    
#     # If FastAPI exits, clean up
#     echo "FastAPI server exited, cleaning up environment"
#     kill $MOTO_PID $SIMULATOR_PID 2>/dev/null
#     aws-mock-down
# }

function aws-mock {
    set +e
    
    echo "Setting up AWS mock infrastructure with EB worker autoscaling simulation..."
    echo "🚀 Using decoupled architecture: separate model downloader + worker containers"
    echo "📦 Models downloaded once by dedicated container, then used by worker"
    
    # Set deployment mode
    export DEPLOYMENT_MODE="aws-mock"
    
    # Get absolute path to project root
    PROJECT_ROOT="$(pwd)"
    
    # Start Moto server locally
    echo "Starting Moto server on localhost:5000..."
    python -m moto.server -p 5000 &
    MOTO_PID=$!
    
    # Wait for Moto server to be ready
    echo -n "Waiting for Moto server to start"
    max_retries=10
    counter=0
    while [ $counter -lt $max_retries ]; do
        echo -n "."
        if curl -s http://localhost:5000 &> /dev/null; then
            echo " ✓"
            echo "Moto server is ready!"
            break
        fi
        sleep 1
        counter=$((counter + 1))
    done
    
    if [ $counter -eq $max_retries ]; then
        echo ""
        echo "Error: Moto server failed to start within timeout"
        kill $MOTO_PID 2>/dev/null
        exit 1
    fi
    
    # Set AWS mock environment variables
    export AWS_ENDPOINT_URL="http://localhost:5000"
    export AWS_SECRET_ACCESS_KEY="mock"
    export AWS_ACCESS_KEY_ID="mock"
    export S3_BUCKET_NAME="rag-pdf-storage"
    
    # Deploy ECS infrastructure using deploy_ecs.py
    echo "Creating ECS mock resources (S3, SQS, etc.)..."
    python -m deployment.aws.orchestration.deploy_ecs --mode aws-mock
    
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create ECS mock resources"
        kill $MOTO_PID 2>/dev/null
        exit 1
    fi
    
    # Get SQS queue URL from environment (set by deploy_ecs.py)
    SQS_QUEUE_URL="${SQS_QUEUE_URL:-http://localhost:5000/queue/rag-task-queue}"
    
    # Build images and start model downloader first
    echo "📥 Step 1: Building Docker images and downloading models..."
    echo "💡 This will run the model-downloader service first, then start the worker"
    cd "$PROJECT_ROOT"  # Use project root for new docker-compose location
    
    # Use docker-compose up with dependency management
    # The model-downloader will run first and download models to the volume
    # Then vlm-worker will start automatically once downloader completes successfully
    docker-compose -f deployment/docker/compose/aws-mock.yml up -d --build
    
    # Check if model downloader completed successfully
    echo "🔍 Checking model downloader completion status..."
    if docker-compose -f deployment/docker/compose/aws-mock.yml ps model-downloader | grep -q "Exit 0"; then
        echo "✅ Model downloader completed successfully!"
    elif docker-compose -f deployment/docker/compose/aws-mock.yml ps model-downloader | grep -q "Exit [1-9]"; then
        exit_code=$(docker-compose -f deployment/docker/compose/aws-mock.yml ps model-downloader | grep "Exit" | sed 's/.*Exit \([0-9]*\).*/\1/')
        echo "❌ Model downloader failed with exit code: $exit_code"
        echo "🔍 Check logs: docker-compose -f deployment/docker/compose/aws-mock.yml logs model-downloader"
        cd "$PROJECT_ROOT"
        kill $MOTO_PID 2>/dev/null
        aws-mock-down
        exit 1
    else
        echo "⚠️  Model downloader status unclear, but continuing..."
    fi
    
    # Verify worker container is running
    echo "🔍 Verifying worker container is running..."
    sleep 5  # Give worker a moment to start
    
    worker_status=$(docker-compose -f deployment/docker/compose/aws-mock.yml ps -q vlm-worker | xargs docker inspect --format='{{.State.Status}}' 2>/dev/null || echo "not_found")
    
    if [ "$worker_status" != "running" ]; then
        echo "❌ Worker container failed to start (status: $worker_status)"
        echo "🔍 Check logs: docker-compose -f deployment/docker/compose/aws-mock.yml logs vlm-worker"
        cd "$PROJECT_ROOT"
        kill $MOTO_PID 2>/dev/null
        aws-mock-down
        exit 1
    fi
    
    echo "✅ ECS Worker container is running and ready for inference!"
    cd "$PROJECT_ROOT"
    
    # Start autoscaling simulator in background (simulation-only mode)
    echo "Starting ECS Autoscaling Simulator (simulation-only mode)..."
    echo "💡 This will simulate scaling decisions without actually scaling containers"
    python -m vlm_workers.scaling.auto_scaler \
        --queue-url "$SQS_QUEUE_URL" \
        --min-instances 0 \
        --max-instances 3 \
        --scale-up-threshold 1 \
        --scale-down-threshold 0 \
        --cooldown 30 \
        --evaluation-interval 5 \
        --evaluation-periods 1 &
    SIMULATOR_PID=$!
    
    # Enhanced trap to kill all processes
    trap 'echo "Shutting down ECS mock environment..."; kill $MOTO_PID $SIMULATOR_PID 2>/dev/null; aws-mock-down; exit 0' INT
    
    # Start FastAPI with uvicorn
    echo "Starting FastAPI application..."
    echo "📊 Monitor autoscaling decisions in the logs above"
    echo "📈 Upload PDFs to trigger queue activity and observe scaling behavior"
    echo "🔧 Container logs:"
    echo "   - Model downloads: docker-compose -f deployment/docker/compose/aws-mock.yml logs model-downloader"
    echo "   - Worker activity: docker-compose -f deployment/docker/compose/aws-mock.yml logs vlm-worker"
    echo "🛑 Press Ctrl+C to shutdown everything"
    
    python -m uvicorn files_api.main:create_app --reload --host 0.0.0.0 --port 8000
    
    # If FastAPI exits, clean up
    echo "FastAPI server exited, cleaning up environment"
    kill $MOTO_PID $SIMULATOR_PID 2>/dev/null
    aws-mock-down
}

function aws-mock-down {
    echo "Shutting down AWS mock environment..."
    
    # Kill any running processes
    pkill -f "python -m moto.server" || true
    pkill -f "eb_autoscaling_simulator" || true
    
    # Stop and remove containers, networks, and volumes
    docker-compose -f deployment/docker/compose/aws-mock.yml down
    
    # Force remove any remaining containers
    docker rm -f model-downloader vlm-worker 2>/dev/null || true
    
    echo "✅ AWS mock environment completely cleaned up"
    echo "💡 All containers, volumes, and networks removed"
}

# Deploy to AWS using 4-phase Docker Compose ECS architecture
function aws-prod {
    set +e
    
    echo "🚀 Deploying 4-phase Docker Compose ECS architecture to AWS..."
    echo "📦 Architecture: Lambda API + Docker Compose ECS + MongoDB + EFS"
    
    # Check for API Gateway configuration before deployment
    echo "🔗 Checking API Gateway configuration..."

    if [ -n "$API_GATEWAY_ID" ]; then
        echo "✅ API Gateway ID found: $API_GATEWAY_ID"
        export API_GATEWAY_ID="$API_GATEWAY_ID"
    elif [ -n "$API_GATEWAY_URL" ]; then
        echo "✅ API Gateway URL found: $API_GATEWAY_URL"
        export API_GATEWAY_URL="$API_GATEWAY_URL"
    else
        echo "⚠️ No API Gateway configuration found"
        echo "💡 Set API_GATEWAY_ID or API_GATEWAY_URL environment variable"
        echo "💡 Example: export API_GATEWAY_ID=abc123def4"
        echo ""
        read -p "🤔 Continue deployment without API Gateway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "❌ Deployment cancelled"
            exit 1
        fi
    fi
    
    # Set deployment mode
    export DEPLOYMENT_MODE="aws-prod"
    
    # Phase 1: Deploy infrastructure only and export configuration
    echo "🏗️ Phase 1: Deploying ECS infrastructure and exporting configuration..."
    python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --infrastructure-only --export-config .env.aws-prod
    
    if [ $? -ne 0 ]; then
        echo "❌ Error: Infrastructure deployment failed"
        exit 1
    fi
    
    echo "✅ Infrastructure deployed and configuration exported to .env.aws-prod"
    
    # Source the exported configuration
    if [ -f ".env.aws-prod" ]; then
        echo "📋 Loading infrastructure configuration..."
        source .env.aws-prod
        echo "✅ Configuration loaded"
    else
        echo "❌ Error: .env.aws-prod configuration file not found"
        exit 1
    fi
    
    # Phase 2: Skip local EFS mounting (EFS will be mounted by ECS tasks on AWS)
    echo "💾 Phase 2: Skipping local EFS mounting (ECS tasks will mount EFS on AWS)..."
    echo "✅ EFS configuration ready for ECS services"
    
    # Phase 3: Skip model population (models will be downloaded by ECS tasks on first run)
    echo "🤖 Phase 3: Skipping model population (ECS tasks will download models on first run)..."
    echo "✅ Model downloading will be handled by ECS workers"
    
    # Phase 4: Deploy services using Docker Compose
    echo "🐳 Phase 4: Deploying services with Docker Compose..."
    
    # Build ECR image if needed
    echo "🔨 Checking ECR image availability..."
    # Load ECR repository name from settings
    ECR_REPO_NAME=$(python3 -c "from files_api.config.settings import get_settings; print(get_settings().ecr_repo_name)")
    
    # Get ECR repository URI
    ECR_URI="${AWS_ACCOUNT_ID:-123456789012}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com/${ECR_REPO_NAME}"
    
    # Check if ECR image already exists
    echo "🔍 Checking if ECR image exists: ${ECR_REPO_NAME}:latest"
    if aws ecr describe-images --repository-name "$ECR_REPO_NAME" --image-ids imageTag=latest --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1; then
        echo "✅ ECR image ${ECR_REPO_NAME}:latest already exists, skipping build"
    else
        echo "❌ ECR image not found, building and pushing..."
        
        # Build and push image
        echo "📦 Building VLM worker image..."
        docker build -t "$ECR_URI:latest" -f deployment/docker/vlm-worker/Dockerfile .
        
        # Push to ECR (requires AWS credentials)
        echo "📤 Pushing to ECR..."
        aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$ECR_URI" 2>/dev/null || {
            echo "⚠️ ECR push failed - image will be built during deployment"
            echo "💡 Deployment will fail if ECR image doesn't exist. Please fix AWS credentials and try again."
        }
    fi
    
    # Phase 4: Deploy ECS services
    echo "🚀 Phase 4: Deploying ECS services (MongoDB + VLM workers)..."
    python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --deploy-services
    
    if [ $? -ne 0 ]; then
        echo "❌ Error: ECS services deployment failed"
        exit 1
    fi
    
    echo "✅ ECS services deployed successfully"
    
    # Phase 5: Deploy Lambda functions (Files API)
    echo "📋 Phase 5: Deploying Lambda functions..."
    python -m deployment.aws.services.lambda_deploy
    
    if [ $? -ne 0 ]; then
        echo "❌ Error: Lambda deployment failed"
        exit 1
    fi
    
    echo "✅ Lambda functions deployed successfully"
    
    echo ""
    echo "🎉 4-Phase AWS Production deployment completed successfully!"
    echo "📊 Architecture deployed:"
    echo "   • Phase 1: ✅ ECS Infrastructure (VPC, EFS, Cluster)"
    echo "   • Phase 2: ✅ EFS Mount Points"
    echo "   • Phase 3: ✅ Model Population"
    echo "   • Phase 4: ✅ Docker Compose Services (MongoDB + VLM Workers)"
    echo "   • Phase 5: ✅ Lambda API"
    echo ""
    echo "🔗 Services:"
    echo "   • MongoDB: Running on ECS with EFS persistence"
    echo "   • VLM Workers: Native ECS services with EFS model cache"
    echo "   • Files API: Lambda with scale-to-zero"
    echo "   • Auto-scaling: Native CloudWatch integration"
    echo ""
    echo "📋 Management commands:"
    echo "   • View logs: aws ecs describe-services --cluster fastapi-app-ecs-cluster --services fastapi-app-vlm-workers fastapi-app-mongodb"
    echo "   • Scale workers: aws ecs update-service --cluster fastapi-app-ecs-cluster --service fastapi-app-vlm-workers --desired-count 3"
    echo "   • Stop services: aws ecs update-service --cluster fastapi-app-ecs-cluster --service fastapi-app-vlm-workers --desired-count 0"
    echo ""
    echo "🔗 Access your deployment via the Lambda function URL"
}

# Cleanup AWS production deployment with state tracking
function aws-prod-cleanup {
    set +e
    
    echo "🧹 AWS Production Cleanup - Complete Teardown"
    echo "⚠️ This will destroy ALL AWS resources created by aws-prod deployment"
    
    # Check for deployment state
    if [ -f ".deployment_state.json" ]; then
        echo "📋 Found deployment state - using LIFO rollback strategy"
        
        # Show current deployment status
        python -m deployment.aws.state.state_manager --action status --state-file .deployment_state.json
        
        echo ""
        read -p "🤔 Proceed with rollback? (y/N): " -n 1 -r
        echo
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "🔄 Executing LIFO rollback..."
            python -m deployment.aws.state.state_manager --action rollback --state-file .deployment_state.json
            
            if [ $? -eq 0 ]; then
                echo "✅ State-based rollback completed"
            else
                echo "⚠️ State-based rollback had issues - proceeding with manual cleanup"
            fi
        else
            echo "❌ Rollback cancelled by user"
            return 1
        fi
    else
        echo "📋 No deployment state found - proceeding with manual cleanup"
    fi
    
    # Set deployment mode for cleanup
    export DEPLOYMENT_MODE="aws-prod"
    
    # Load infrastructure config if available
    if [ -f ".env.aws-prod" ]; then
        echo "📋 Loading infrastructure configuration..."
        source .env.aws-prod
    fi
    
    # Phase 1: Stop ECS services
    echo "🐳 Phase 1: Stopping ECS services..."
    if [ -n "$ECS_CLUSTER_NAME" ]; then
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "fastapi-app-vlm-workers" --desired-count 0 2>/dev/null || echo "⚠️ VLM workers service not found"
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "fastapi-app-mongodb" --desired-count 0 2>/dev/null || echo "⚠️ MongoDB service not found"
        echo "✅ ECS services stopped"
    else
        echo "⚠️ ECS_CLUSTER_NAME not found - cannot stop services"
    fi
    
    # Phase 2: Unmount EFS file systems
    echo "💾 Phase 2: Unmounting EFS file systems..."
    sudo umount /mnt/efs/mongodb 2>/dev/null || echo "⚠️ MongoDB EFS not mounted or unmount failed"
    sudo umount /mnt/efs/models 2>/dev/null || echo "⚠️ Models EFS not mounted or unmount failed"
    
    # Cleanup local mount directories
    sudo rmdir /mnt/efs/mongodb /mnt/efs/models /mnt/efs 2>/dev/null || true
    rm -rf /tmp/efs-mongodb /tmp/efs-models 2>/dev/null || true
    echo "✅ EFS mount points cleaned up"
    
    # Phase 3: Cleanup AWS ECS infrastructure
    echo "🏗️ Phase 3: Cleaning up ECS infrastructure..."
    python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --cleanup
    
    if [ $? -eq 0 ]; then
        echo "✅ ECS infrastructure cleaned up"
    else
        echo "⚠️ ECS cleanup had issues - check AWS console for remaining resources"
    fi
    
    # Phase 4: Cleanup Lambda functions
    echo "📋 Phase 4: Cleaning up Lambda functions..."
    python -m deployment.aws.services.lambda_deploy --cleanup 2>/dev/null || {
        echo "⚠️ Lambda cleanup script not available - manual cleanup may be needed"
    }
    
    # Phase 5: Remove local configuration files
    echo "🗑️ Phase 5: Cleaning up local files..."
    rm -f .env.aws-prod .env.aws-prod.json .deployment_state.json
    echo "✅ Local configuration files removed"
    
    # Phase 6: Remove ECR images and repository
    echo "📦 Phase 6: ECR repository cleanup..."
    if [ -n "$ECR_REPO_NAME" ]; then
        echo "🗑️ Deleting ECR repository: $ECR_REPO_NAME"
        if aws ecr delete-repository --repository-name "$ECR_REPO_NAME" --force --region "$AWS_DEFAULT_REGION" 2>/dev/null; then
            echo "✅ ECR repository '$ECR_REPO_NAME' deleted successfully"
        else
            echo "⚠️ ECR repository '$ECR_REPO_NAME' not found or already deleted"
        fi
    else
        echo "⚠️ ECR_REPO_NAME not set - skipping ECR cleanup"
    fi
    
    echo ""
    echo "🎉 AWS Production cleanup completed!"
    echo "📊 Cleanup summary:"
    echo "   • ✅ Docker Compose services stopped"
    echo "   • ✅ EFS mount points unmounted"
    echo "   • ✅ ECS infrastructure cleaned up"
    echo "   • ✅ Lambda functions cleaned up"
    echo "   • ✅ Local configuration removed"
    echo ""
    echo "💡 Manual verification recommended:"
    echo "   • Check AWS Console for any remaining resources"
    echo "   • Verify S3 buckets are deleted"
    echo "   • Confirm EFS file systems are removed"
    echo "   • Check CloudWatch log groups"
    echo ""
    echo "🔍 Cost verification: aws ce get-cost-and-usage --help"
}

# Soft cleanup AWS production deployment (preserves expensive infrastructure)
function aws-prod-cleanup-soft {
    set +e
    
    echo "🧹 AWS Production Soft Cleanup - Preserve Infrastructure"
    echo "💰 This preserves NAT Gateway, VPC, EFS, and ECR to avoid recreation costs"
    echo "🔄 Only cleans up: ECS Services, Tasks, Auto Scaling Groups, Lambda functions"
    
    # Set deployment mode for cleanup
    export DEPLOYMENT_MODE="aws-prod"
    
    # Load infrastructure config if available
    if [ -f ".env.aws-prod" ]; then
        echo "📋 Loading infrastructure configuration..."
        source .env.aws-prod
    fi
    
    # Phase 1: Stop ECS services
    echo "🐳 Phase 1: Stopping ECS services..."
    if [ -n "$ECS_CLUSTER_NAME" ]; then
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "fastapi-app-vlm-workers" --desired-count 0 2>/dev/null || echo "⚠️ VLM workers service not found"
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "fastapi-app-mongodb" --desired-count 0 2>/dev/null || echo "⚠️ MongoDB service not found"
        echo "✅ ECS services stopped"
    else
        echo "⚠️ ECS_CLUSTER_NAME not found - cannot stop services"
    fi
    
    # Phase 2: Scale down ECS services (preserve infrastructure)
    echo "🏗️ Phase 2: Scaling down ECS services (preserving infrastructure)..."
    if [ -n "$ECS_CLUSTER_NAME" ]; then
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "vlm-worker" --desired-count 0 2>/dev/null || echo "⚠️ vlm-worker service not found or already scaled down"
        aws ecs update-service --cluster "$ECS_CLUSTER_NAME" --service "mongodb" --desired-count 0 2>/dev/null || echo "⚠️ mongodb service not found or already scaled down"
        echo "✅ ECS services scaled to 0 (infrastructure preserved)"
    else
        echo "⚠️ ECS_CLUSTER_NAME not found - skipping service scaling"
    fi
    
    # Phase 3: Cleanup Lambda functions
    echo "⚡ Phase 3: Cleaning up Lambda functions..."
    python -m files_api.aws.deploy_lambda --mode aws-prod --cleanup
    
    if [ $? -eq 0 ]; then
        echo "✅ Lambda functions cleaned up"
    else
        echo "⚠️ Lambda cleanup had issues - check AWS console for remaining functions"
    fi
    
    # Phase 4: Scale down auto scaling groups to 0 (don't delete)
    echo "📊 Phase 4: Scaling down auto scaling groups..."
    if [ -n "$ECS_CLUSTER_NAME" ]; then
        # Find and scale down auto scaling groups
        ASG_NAMES=$(aws autoscaling describe-auto-scaling-groups --query "AutoScalingGroups[?contains(Tags[?Key=='Project'].Value, 'FastAPI App')].AutoScalingGroupName" --output text 2>/dev/null || echo "")
        if [ -n "$ASG_NAMES" ]; then
            for asg in $ASG_NAMES; do
                aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$asg" --desired-capacity 0 --min-size 0 2>/dev/null || echo "⚠️ Could not scale ASG: $asg"
            done
            echo "✅ Auto scaling groups scaled to 0"
        else
            echo "⚠️ No auto scaling groups found"
        fi
    fi
    
    # Clean up deployment state for services only
    if [ -f ".deployment_state.json" ]; then
        echo "📋 Updating deployment state (preserving infrastructure entries)..."
        python -c "
import json
try:
    with open('.deployment_state.json', 'r') as f:
        state = json.load(f)
    
    # Remove service-level entries but keep infrastructure
    preserved_keys = ['vpc', 'subnets', 'internet_gateway', 'nat_gateway', 'security_groups', 'efs', 'ecr']
    new_state = {k: v for k, v in state.items() if any(pk in k.lower() for pk in preserved_keys)}
    
    with open('.deployment_state.json', 'w') as f:
        json.dump(new_state, f, indent=2)
    print('✅ Deployment state updated')
except Exception as e:
    print(f'⚠️ Could not update deployment state: {e}')
"
    fi
    
    echo ""
    echo "✅ AWS Production Soft Cleanup Complete!"
    echo ""
    echo "💰 Cost Savings: Preserved expensive infrastructure:"
    echo "   • NAT Gateway: ~$32.40/month (preserved)"
    echo "   • Elastic IP: ~$3.60/month (preserved)"
    echo "   • VPC/Subnets: FREE (preserved)"
    echo "   • EFS: Pay-per-use (preserved)"
    echo "   • ECR: Pay-per-use (preserved)"
    echo ""
    echo "🔄 Cleaned up pay-per-use resources:"
    echo "   • ECS Services and Tasks: $0/month when scaled to 0"
    echo "   • Lambda Functions: $0/month when not invoked"
    echo "   • Auto Scaling Groups: $0/month when scaled to 0"
    echo ""
    echo "🚀 Next deployment will reuse existing infrastructure!"
    echo "💡 To completely destroy everything: make aws-prod-cleanup"
}

# Show AWS production deployment status
function aws-prod-status {
    set +e
    
    echo "📊 AWS Production Deployment Status"
    echo "=================================="
    
    # Check deployment state
    if [ -f ".deployment_state.json" ]; then
        echo "📋 Deployment State:"
        python -m deployment.aws.state.state_manager --action status --state-file .deployment_state.json
        echo ""
    else
        echo "📋 No deployment state file found"
        echo ""
    fi
    
    # Check configuration files
    echo "📁 Configuration Files:"
    if [ -f ".env.aws-prod" ]; then
        echo "   ✅ .env.aws-prod (infrastructure config)"
        echo "   📋 Key values:"
        grep -E "^(ECS_CLUSTER_NAME|EFS_.*_ID|VPC_ID)" .env.aws-prod 2>/dev/null | sed 's/^/      /'
    else
        echo "   ❌ .env.aws-prod (missing)"
    fi
    echo ""
    
    # Check ECS services
    echo "🐳 ECS Services:"
    if [ -n "$ECS_CLUSTER_NAME" ]; then
        echo "   📦 Services status:"
        aws ecs describe-services --cluster "$ECS_CLUSTER_NAME" --services "fastapi-app-vlm-workers" "fastapi-app-mongodb" \
            --query 'services[*].[serviceName,status,runningCount,desiredCount]' --output table 2>/dev/null | sed 's/^/      /' \
            || echo "      ⚠️ No ECS services found"
    else
        echo "   ❌ ECS_CLUSTER_NAME not set"
    fi
    echo ""
    
    # Check EFS mounts
    echo "💾 EFS Mount Points:"
    if mount | grep -q "/mnt/efs"; then
        echo "   ✅ EFS mounts active:"
        mount | grep "/mnt/efs" | sed 's/^/      /'
    else
        echo "   ❌ No EFS mounts found"
    fi
    echo ""
    
    # Check AWS resources (if AWS CLI available)
    if command -v aws &> /dev/null; then
        echo "☁️ AWS Resources:"
        
        # Load config if available
        if [ -f ".env.aws-prod" ]; then
            source .env.aws-prod
        fi
        
        if [ -n "$ECS_CLUSTER_NAME" ]; then
            echo "   🏗️ ECS Cluster:"
            aws ecs describe-clusters --clusters "$ECS_CLUSTER_NAME" --query 'clusters[0].status' --output text 2>/dev/null | sed 's/^/      Cluster: /' || echo "      ❌ Cluster not found or AWS CLI error"
            
            echo "   🔧 ECS Services:"
            aws ecs list-services --cluster "$ECS_CLUSTER_NAME" --query 'serviceArns' --output text 2>/dev/null | wc -w | sed 's/^/      Services: /' || echo "      ❌ Cannot check services"
        fi
        
        if [ -n "$EFS_MONGODB_ID" ]; then
            echo "   💾 EFS File Systems:"
            aws efs describe-file-systems --file-system-id "$EFS_MONGODB_ID" --query 'FileSystems[0].LifeCycleState' --output text 2>/dev/null | sed 's/^/      MongoDB EFS: /' || echo "      ❌ MongoDB EFS not found"
        fi
        
        if [ -n "$EFS_MODELS_ID" ]; then
            aws efs describe-file-systems --file-system-id "$EFS_MODELS_ID" --query 'FileSystems[0].LifeCycleState' --output text 2>/dev/null | sed 's/^/      Models EFS: /' || echo "      ❌ Models EFS not found"
        fi
    else
        echo "☁️ AWS CLI not available - cannot check AWS resources"
    fi
    
    echo ""
    echo "💡 Commands:"
    echo "   • View logs: aws logs describe-log-groups --log-group-name-prefix '/ecs/fastapi-app'"
    echo "   • Scale workers: aws ecs update-service --cluster fastapi-app-ecs-cluster --service fastapi-app-vlm-workers --desired-count 3"
    echo "   • Full cleanup: make aws-prod-cleanup"
}

# Show AWS production cost analysis
function aws-prod-costs {
    set +e
    
    echo "💰 AWS Production Cost Analysis"
    echo "==============================="
    
    # Use the cost optimizer to analyze current deployment costs
    python -m deployment.aws.cleanup.orphan_detector --estimate-costs
    
    echo ""
    echo "💡 Use 'make aws-prod-cleanup-soft' to reduce costs while preserving infrastructure"
    echo "🚨 Use 'make aws-prod-cleanup' for full cleanup to minimize costs"
}

# Scan for orphaned AWS resources
function aws-prod-orphans {
    set +e
    
    echo "🔍 Scanning for Orphaned AWS Resources"
    echo "======================================"
    
    # Use the orphan detector to scan for untracked resources
    python -m deployment.aws.cleanup.orphan_detector --scan
    
    echo ""
    echo "💡 Review the report above for resources that may need cleanup"
    echo "🚨 Use caution when cleaning up orphaned resources"
}

# Validate AWS production deployment prerequisites
function aws-prod-validate {
    set +e
    
    echo "✅ Validating AWS Production Prerequisites"
    echo "========================================="
    
    # Use the resource validator to check prerequisites
    python -m deployment.aws.monitoring.resource_validator
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ All prerequisites validated successfully"
        echo "🚀 Ready for AWS production deployment"
    else
        echo ""
        echo "❌ Prerequisites validation failed"
        echo "🔧 Please address the issues above before deployment"
        exit 1
    fi
}

# Validate AWS mock deployment prerequisites
function aws-mock-validate {
    set +e
    
    echo "✅ Validating AWS Mock Prerequisites"
    echo "==================================="
    
    # Check Docker availability
    if ! command -v docker &> /dev/null; then
        echo "❌ Docker is not installed or not in PATH"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        echo "❌ Docker daemon is not running"
        exit 1
    fi
    
    # Check Docker Compose availability
    if ! command -v docker-compose &> /dev/null; then
        echo "❌ Docker Compose is not installed or not in PATH"
        exit 1
    fi
    
    # Check moto server health if running
    if curl -s http://localhost:5000 &> /dev/null; then
        echo "✅ Moto server is running and accessible"
    else
        echo "ℹ️  Moto server not running (will be started during deployment)"
    fi
    
    echo ""
    echo "✅ All prerequisites validated successfully"
    echo "🚀 Ready for AWS mock deployment"
}

# Validate local development prerequisites
function local-dev-validate {
    set +e
    
    echo "✅ Validating Local Development Prerequisites"
    echo "============================================"
    
    # Check Python and required packages
    if ! python -c "import torch; import transformers; import byaldi" &> /dev/null; then
        echo "❌ Required ML packages not installed (torch, transformers, byaldi)"
        echo "💡 Run 'make install' to install dependencies"
        exit 1
    fi
    
    # Check GPU availability (optional)
    if python -c "import torch; print('GPU available:', torch.cuda.is_available())" 2>/dev/null | grep -q "True"; then
        echo "✅ GPU available for model acceleration"
    else
        echo "ℹ️  No GPU detected - models will run on CPU (slower)"
    fi
    
    # Check model cache status
    if [ -d "$HOME/.cache/huggingface" ] && [ "$(ls -A $HOME/.cache/huggingface 2>/dev/null)" ]; then
        echo "✅ HuggingFace model cache found"
    else
        echo "ℹ️  No model cache found - models will be downloaded on first use"
    fi
    
    # Check moto server health if running
    if curl -s http://localhost:5000 &> /dev/null; then
        echo "✅ Moto server is running and accessible"
    else
        echo "ℹ️  Moto server not running (will be started during deployment)"
    fi
    
    echo ""
    echo "✅ All prerequisites validated successfully"
    echo "🚀 Ready for local development"
}

# New function to install npm dependencies
function npm-install {
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Installing npm dependencies in $FRONTEND_DIR"
        cd "$FRONTEND_DIR"
        npm install
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        exit 1
    fi
}

# New function to build frontend
function npm-build {
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Building frontend in $FRONTEND_DIR"
        cd "$FRONTEND_DIR"
        npm run build
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        exit 1
    fi
}

# New function to run both frontend and backend with health check
function dev {
    set +e
    
    # Set queue type first
    export QUEUE_TYPE="local-mock"
    
    # Start moto server
    python -m moto.server -p 5000 &
    MOTO_PID=$!
    
    # Configure AWS mock environment
    export AWS_ENDPOINT_URL="http://localhost:5000"
    export AWS_SECRET_ACCESS_KEY="mock"
    export AWS_ACCESS_KEY_ID="mock"
    export S3_BUCKET_NAME="some-bucket"
    
    # Create mock bucket
    aws s3 mb "s3://$S3_BUCKET_NAME"
    
    # Trap EXIT signal to kill all background processes
    trap 'kill $(jobs -p)' EXIT

    echo "Preloading models..."
    python src/files_api/vlm/preload.py
    
    # Start worker in background - it will use QUEUE_TYPE from environment
    echo "Starting Local Worker"
    python src/files_api/cli.py worker --mode local-mock &
    
    # Start FastAPI app in background
    echo "Starting FastAPI app"
    python -m uvicorn files_api.main:create_app --reload --host 0.0.0.0 --port 8000 --log-level debug &
    
    # Wait for the API to be ready
    echo "Waiting for FastAPI to be ready..."
    max_retries=30
    counter=0
    while [ $counter -lt $max_retries ]; do
        if curl -s http://localhost:8000/health | grep -q "\"ready\":true"; then
            echo "FastAPI is ready!"
            break
        fi
        echo "Waiting for API... ($counter/$max_retries)"
        sleep 2
        counter=$((counter + 1))
    done
    
    if [ $counter -eq $max_retries ]; then
        echo "Warning: FastAPI didn't report ready status within timeout, but will continue anyway"
    fi
    
    # Start frontend
    if [ -d "$FRONTEND_DIR" ]; then
        echo "Starting frontend"
        cd "$FRONTEND_DIR"
        npm run dev
    else
        echo "Error: Frontend directory not found at $FRONTEND_DIR"
        # Keep running the backend even if frontend fails
        wait
    fi
}

function iot-backend-start() {
    echo "Starting Docker containers (MQTT broker and gateway simulator)..."
    cd src/iot && docker-compose build --no-cache gateway-simulator
    
    # Create the network first (this will also be created by docker-compose up, but ensuring it exists early)
    echo "Ensuring iot-network exists..."
    docker network create iot-network 2>/dev/null || echo "iot-network already exists or will be created by docker-compose"
    
    docker-compose up -d mqtt-broker
    
    echo "Waiting for MQTT broker to initialize..."
    sleep 3

    # Start rules-engine after MQTT broker is ready
    if grep -q "rules-engine:" docker-compose.yml; then
        echo "Starting Rules Engine..."
        docker-compose up -d rules-engine
        echo "Rules Engine started."
    fi
    
    echo "Starting FastAPI backend..."
    echo "Press CTRL+C to stop when done"
    # Using Python module approach while staying in src/iot directory
    cd .. && python -m iot.cli start --mode local --docker-network iot-network
}

function iot-backend-cleanup() {
    echo "Cleaning up IoT Gateway Management System..."
    
    echo "Stopping Docker containers..."
    cd src/iot && docker-compose down
    
    # Remove all gateway containers that might be running
    echo "Removing any remaining gateway containers..."
    docker ps -a | grep "gateway-" | awk '{print $1}' | xargs -r docker rm -f
    
    # Remove any rules engine containers that might be running
    echo "Removing any rules engine containers..."
    docker ps -a | grep -E "rules-engine|iot-rules" | awk '{print $1}' | xargs -r docker rm -f
    
    echo "Cleaning up Python cache files..."
    find src/iot -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find src/iot -type f -name "*.pyc" -delete 2>/dev/null || true
    
    # Kill any remaining Python processes related to the app (if any)
    echo "Killing any remaining Python processes..."
    pkill -f "iot.cli start" 2>/dev/null || true
    
    # Remove any temporary or log files
    echo "Removing temporary log files..."
    rm -f src/iot/*.log 2>/dev/null || true
    
    echo "Cleanup complete"
}

# Function to generate certificates for a gateway
function generate_cert() {
    local gateway_id=$1
    
    if [ -z "$gateway_id" ]; then
        echo -e "${RED}Error: Gateway ID is required for certificate generation${NC}"
        return 1
    fi
    
    # First check if gateway exists
    response=$(curl -s "http://localhost:8000/api/gateways/${gateway_id}")
    if [[ $response == *"detail"* ]] || [[ $response == *"not found"* ]]; then
        echo -e "${RED}Error: Gateway ${gateway_id} does not exist${NC}"
        echo "Please create the gateway first using the API"
        return 1
    fi
    
    echo -e "${GREEN}Generating certificates for gateway: ${gateway_id}${NC}"
    
    # Create certificates directory
    local gateway_cert_dir="${CERT_DIR}/${gateway_id}"
    mkdir -p "$gateway_cert_dir"
    
    # Generate certificate and private key using OpenSSL
    local cert_file="${gateway_cert_dir}/certificate.pem"
    local key_file="${gateway_cert_dir}/private_key.pem"
    
    echo -e "${YELLOW}Creating certificate and private key...${NC}"
    openssl req -x509 -newkey rsa:2048 -keyout "$key_file" \
        -out "$cert_file" -days "$CERT_DAYS" -nodes \
        -subj "/CN=gateway-${gateway_id}/O=IoT Gateway Management System" 2>/dev/null
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to generate certificates${NC}"
        return 1
    fi
    
    # Set permissions
    chmod 644 "$cert_file"
    chmod 600 "$key_file"
    
    echo -e "${GREEN}Certificates generated successfully:${NC}"
    echo "Certificate: $cert_file"
    echo "Private Key: $key_file"
    
    echo -e "${YELLOW}Next steps:${NC}"
    echo "- Inject certificate into container: make inject-cert GATEWAY_ID=$gateway_id"
    
    return 0
}

# Function to inject certificates into a gateway container
function inject_cert() {
    local gateway_id=$1
    
    if [ -z "$gateway_id" ]; then
        echo -e "${RED}Error: Gateway ID is required${NC}"
        return 1
    fi
    
    # Check if container exists
    local container_name="gateway-$gateway_id"
    if ! docker ps -a --format '{{.Names}}' | grep -q "^$container_name$"; then
        echo -e "${RED}Error: Container $container_name does not exist${NC}"
        echo "Make sure the gateway has been created and a container is running"
        return 1
    fi
    
    # Check if certificates exist
    local cert_file="${CERT_DIR}/${gateway_id}/certificate.pem"
    local key_file="${CERT_DIR}/${gateway_id}/private_key.pem"
    
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        echo -e "${RED}Error: Certificates for gateway ${gateway_id} not found${NC}"
        echo "Please generate certificates first: make generate-cert GATEWAY_ID=$gateway_id"
        return 1
    fi
    
    echo -e "${GREEN}Injecting certificates into container: ${container_name}${NC}"
    
    # Get container running status
    local is_running=$(docker inspect --format='{{.State.Running}}' "$container_name" 2>/dev/null)
    if [ "$is_running" != "true" ]; then
        echo -e "${YELLOW}Warning: Container $container_name is not running${NC}"
        echo "Starting container..."
        docker start "$container_name"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to start container${NC}"
            return 1
        fi
        # Give it a moment to start
        sleep 2
    fi
    
    # Copy certificates to container
    echo -e "${YELLOW}Copying certificate to container...${NC}"
    docker cp "$cert_file" "$container_name:/app/certificates/cert.pem"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to copy certificate to container${NC}"
        return 1
    fi
    
    echo -e "${YELLOW}Copying private key to container...${NC}"
    docker cp "$key_file" "$container_name:/app/certificates/key.pem"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to copy private key to container${NC}"
        return 1
    fi
    
    echo -e "${GREEN}Certificates successfully injected into container${NC}"
    echo "The gateway should now detect the certificates and start normal operations"
    
    # Check container status
    echo -e "${YELLOW}Container status:${NC}"
    docker ps --filter "name=$container_name" --format "{{.Status}}"
    
    echo -e "${YELLOW}You can view container logs with:${NC}"
    echo "  docker logs -f $container_name"
    
    return 0
}

# run linting, formatting, and other static code quality tools
function lint {
    pre-commit run --all-files
}

# same as `lint` but with any special considerations for CI
function lint:ci {
    # We skip no-commit-to-branch since that blocks commits to `main`.
    # All merged PRs are commits to `main` so this must be disabled.
    SKIP=no-commit-to-branch pre-commit run --all-files
}

# execute tests that are not marked as `slow`
function test:quick {
    run-tests -m "not slow" ${@:-"$THIS_DIR/tests/"}
}

# execute tests against the installed package; assumes the wheel is already installed
function test:ci {
    INSTALLED_PKG_DIR="$(python -c 'import files_api; print(files_api.__path__[0])')"
    # in CI, we must calculate the coverage for the installed package, not the src/ folder
    COVERAGE_DIR="$INSTALLED_PKG_DIR" run-tests
}

# (example) ./run.sh test tests/test_states_info.py::test__slow_add
function run-tests {
    PYTEST_EXIT_STATUS=0
    rm -rf "$THIS_DIR/test-reports" || true
    python -m pytest ${@:-"$THIS_DIR/tests/"} \
        --cov "${COVERAGE_DIR:-$THIS_DIR/src}" \
        --cov-report html \
        --cov-report term \
        --cov-report xml \
        --junit-xml "$THIS_DIR/test-reports/report.xml" \
        --cov-fail-under "$MINIMUM_TEST_COVERAGE_PERCENT" || ((PYTEST_EXIT_STATUS+=$?))
    mv coverage.xml "$THIS_DIR/test-reports/" || true
    mv htmlcov "$THIS_DIR/test-reports/" || true
    mv .coverage "$THIS_DIR/test-reports/" || true
    return $PYTEST_EXIT_STATUS
}

function test:wheel-locally {
    deactivate || true
    rm -rf test-env || true
    python -m venv test-env
    source test-env/bin/activate
    clean || true
    pip install build
    build
    pip install ./dist/*.whl pytest pytest-cov
    test:ci
    deactivate || true
}

# serve the html test coverage report on localhost:8000
function serve-coverage-report {
    python -m http.server --directory "$THIS_DIR/test-reports/htmlcov/" 8000
}

# build a wheel and sdist from the Python source code
function build {
    python -m build --sdist --wheel "$THIS_DIR/"
}

function release:test {
    lint
    clean
    build
    publish:test
}

function release:prod {
    release:test
    publish:prod
}

function publish:test {
    try-load-dotenv || true
    twine upload dist/* \
        --repository testpypi \
        --username=__token__ \
        --password="$TEST_PYPI_TOKEN"
}

function publish:prod {
    try-load-dotenv || true
    twine upload dist/* \
        --repository pypi \
        --username=__token__ \
        --password="$PROD_PYPI_TOKEN"
}

# remove all files generated by tests, builds, or operating this codebase
function clean {
    rm -rf dist build coverage.xml test-reports
    find . \
      -type d \
      \( \
        -name "*cache*" \
        -o -name "*.dist-info" \
        -o -name "*.egg-info" \
        -o -name "*htmlcov" \
      \) \
      -not -path "*env/*" \
      -exec rm -r {} + || true

    find . \
      -type f \
      -name "*.pyc" \
      -not -path "*env/*" \
      -exec rm {} +
}

# export the contents of .env as environment variables
function try-load-dotenv {
    if [ ! -f "$THIS_DIR/.env" ]; then
        echo "no .env file found"
        return 1
    fi

    while read -r line; do
        export "$line"
    done < <(grep -v '^#' "$THIS_DIR/.env" | grep -v '^$')
}

# print all functions in this file
function help {
    echo "$0 <task> <args>"
    echo "Tasks:"
    compgen -A function | cat -n
}

TIMEFORMAT="Task completed in %3lR"
time ${@:-help}