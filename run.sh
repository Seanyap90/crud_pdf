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

# Function to load environment variables from .env files
function load_env_file {
    local env_file=$1
    if [ -f "$env_file" ]; then
        set -a  # automatically export all variables
        source "$env_file"
        set +a
        echo "✅ Loaded environment from $env_file"
        return 0
    else
        echo "❌ Environment file $env_file not found"
        return 1
    fi
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
    
    # Load local development environment
    load_env_file ".env.local-dev"
    
    if [ $? -ne 0 ]; then
        echo "⚠️ Could not load .env.local-dev, using fallback configuration"
        # Fallback configuration
        export DEPLOYMENT_MODE="local-dev"
        export QUEUE_TYPE="local-dev"
        export DISABLE_DUPLICATE_LOADING="true"
        export MODEL_MEMORY_LIMIT="24GiB"
        export AWS_ENDPOINT_URL="http://localhost:5000"
        export AWS_SECRET_ACCESS_KEY="mock"
        export AWS_ACCESS_KEY_ID="mock"
        export S3_BUCKET_NAME="some-bucket"
    fi

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
    
    # Load AWS mock environment
    load_env_file ".env.aws-mock"
    
    if [ $? -ne 0 ]; then
        echo "⚠️ Could not load .env.aws-mock, using fallback configuration"
        # Fallback configuration
        export DEPLOYMENT_MODE="aws-mock"
        export AWS_ENDPOINT_URL="http://localhost:5000"
        export AWS_SECRET_ACCESS_KEY="mock"
        export AWS_ACCESS_KEY_ID="mock"
        export S3_BUCKET_NAME="rag-pdf-storage"
    fi
    
    echo "Setting up AWS mock infrastructure with ECS worker autoscaling simulation..."
    echo "🚀 Using decoupled architecture: separate model downloader + worker containers"
    echo "📦 Models downloaded once by dedicated container, then used by worker"
    
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

# Deploy to AWS using optimized hybrid console+code architecture
function aws-prod {
    set +e
    
    # Load AWS production environment first
    load_env_file ".env.aws-prod"
    
    echo "🚀 AWS Production Deployment"
    echo "============================"
    echo "Select deployment mode:"
    echo "  1) Hybrid (Console infrastructure + Parallel Lambda+ECS)"
    echo "  2) Infrastructure Only (Setup + Export config)" 
    echo "  3) Status & Cost Analysis"
    echo "  4) Cleanup Options"
    echo ""
    
    read -p "Choose mode (1-4) [default: 1]: " mode
    
    export DEPLOYMENT_MODE="aws-prod"
    
    case $mode in
        1|"")
            echo "Using hybrid deployment with parallel Lambda+ECS"
            aws_prod_hybrid_parallel
            ;;
        2)
            aws_prod_infrastructure_only
            ;;
        3)
            aws_prod_status_enhanced
            ;;
        4)
            aws_prod_cleanup_menu
            ;;
        *)
            echo "❌ Invalid selection. Please choose 1-4."
            exit 1
            ;;
    esac
}

# Hybrid console+code deployment with true make-based parallel execution
function aws_prod_hybrid_parallel {
    echo "🔍 Hybrid Console+Code with Parallel Deployment"
    echo "==============================================="
    echo "📦 Architecture: Console infrastructure + parallel Lambda+ECS deployment"
    
    # Environment already loaded by parent aws-prod function
    
    # Validate infrastructure first (dependency)
    echo "🏗️ Validating infrastructure and prerequisites..."
    make aws-prod-infra
    
    if [ $? -ne 0 ]; then
        echo "❌ Infrastructure validation failed"
        exit 1
    fi
    
    echo "✅ Infrastructure validation completed"
    
    # Run Lambda and ECS in parallel using make -j2
    echo "🚀 Starting parallel Lambda + ECS deployment..."
    make -j2 aws-prod-lambda aws-prod-ecs
    
    if [ $? -eq 0 ]; then
        echo "✅ Parallel deployment completed successfully"
        
        # Post-deployment verification
        echo "🔐 Verifying IAM roles..."
        python -c "
from deployment.aws.utils.iam_verification import generate_iam_verification_report
import json
try:
    report = generate_iam_verification_report(['fastapi-app-files-api'], 'fastapi-app-ecs-cluster')
    print('✅ IAM Verification:', 'PASS' if report['overall_status'] == 'PASS' else 'FAIL')
except Exception as e:
    print('⚠️ IAM Verification: Unable to verify -', str(e))
" 2>/dev/null
        
        echo ""
        echo "🎉 Hybrid Console+Code deployment with parallel Lambda+ECS completed!"
        echo "💰 Cost Optimization: NAT Gateway eliminated (saves $32.40/month)"
        _show_deployment_summary
    else
        echo "❌ Parallel deployment failed"
        exit 1
    fi
}

# Infrastructure-only deployment
function aws_prod_infrastructure_only {
    echo "🏗️ Infrastructure-Only AWS Production Deployment"
    echo "==============================================="
    echo "📦 Architecture: Infrastructure setup + configuration export"
    
    # Phase 1: Deploy optimized infrastructure only
    echo "🏗️ Deploying optimized infrastructure (single EFS, public subnets)..."
    python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --infrastructure-only --export-config .env.aws-prod
    
    if [ $? -ne 0 ]; then
        echo "❌ Error: Infrastructure deployment failed"
        exit 1
    fi
    
    echo ""
    echo "🎉 Infrastructure-only deployment completed!"
    echo "📋 Configuration exported to .env.aws-prod"
    echo "💡 Next steps:"
    echo "   1. Review .env.aws-prod configuration"
    echo "   2. Complete manual database setup (see deployment/aws/services/README.md)"
    echo "   3. Run services deployment: make aws-prod (choose option 1 or 2)"
}

# Shared deployment summary function
function _show_deployment_summary {
    # Get database public IP from configuration file
    if [ -f ".env.aws-prod" ]; then
        DATABASE_PUBLIC_IP=$(grep "^DATABASE_PUBLIC_IP=" .env.aws-prod | cut -d= -f2 2>/dev/null)
        DATABASE_HOST=$(grep "^DATABASE_HOST=" .env.aws-prod | cut -d= -f2 2>/dev/null)
        EFS_SHARED_MODELS_ID=$(grep "^EFS_SHARED_MODELS_ID=" .env.aws-prod | cut -d= -f2 2>/dev/null)
        VPC_ID=$(grep "^VPC_ID=" .env.aws-prod | cut -d= -f2 2>/dev/null)
    fi
    
    echo "📊 Deployment Summary:"
    echo "====================="
    if [ -n "$VPC_ID" ]; then
        echo "   • VPC: $VPC_ID"
    fi
    if [ -n "$EFS_SHARED_MODELS_ID" ]; then
        echo "   • Shared Models EFS: $EFS_SHARED_MODELS_ID"
    fi
    if [ -n "$DATABASE_HOST" ]; then
        echo "   • Database: $DATABASE_HOST:8080"
    fi
    echo "   • Lambda: Deployed without VPC (faster cold starts)"
    echo "   • ECS Workers: Auto-scaling enabled"
    echo ""
    echo "💰 Cost Optimizations Applied:"
    echo "   • Single EFS instead of 3 (simplified storage)"
    echo "   • Database on EC2 boot volume (no database EFS)"
    echo "   • Lambda without VPC (no NAT Gateway needed for Lambda)"
    echo "   • Public subnet architecture (reduced networking costs)"
    echo ""
    
    if [ -n "$DATABASE_PUBLIC_IP" ]; then
        echo "📋 Manual Database Setup:"
        echo "   1. SSH to database instance:"
        echo "      ssh -i ~/.ssh/*database-key*.pem ubuntu@${DATABASE_PUBLIC_IP}"
        echo "   2. Follow setup guide: deployment/aws/services/README.md"
        echo "   3. Test database: curl http://${DATABASE_HOST:-$DATABASE_PUBLIC_IP}:8080/health"
    else
        echo "⚠️ Database IP not found - check .env.aws-prod for connection details"
    fi
    
    echo ""
    echo "🔗 Management Commands:"
    echo "   • View ECS services: aws ecs list-services --cluster *-ecs-cluster"
    echo "   • Scale workers: aws ecs update-service --cluster *-ecs-cluster --service *-vlm-workers --desired-count 2"
    echo "   • View logs: aws logs tail /ecs/*"
}

# Cleanup AWS production deployment with parallel cleanup support
function aws-prod-cleanup {
    set +e
    
    echo "🧹 AWS Production Cleanup"
    echo "========================"
    echo "This will destroy ALL AWS resources created by aws-prod deployment"
    echo ""
    read -p "Are you sure? (y/N): " confirm
    
    if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
        echo "🚀 Running cleanup..."
        
        # Set deployment mode
        export DEPLOYMENT_MODE="aws-prod"
        
        # Load config
        if [ -f ".env.aws-prod" ]; then
            load_env_file ".env.aws-prod"
        fi
        
        # Sequential cleanup (simple and reliable)
        echo "⚡ Cleaning up Lambda functions..."
        python -m deployment.aws.services.lambda_deploy --mode aws-prod --cleanup
        
        echo "🐳 Cleaning up ECS infrastructure..."
        python -m deployment.aws.orchestration.deploy_ecs --mode aws-prod --cleanup
        
        # Remove config files
        rm -f .env.aws-prod .env.aws-prod.json .deployment_state.json
        
        echo "🎉 Cleanup completed!"
    else
        echo "❌ Cleanup cancelled"
    fi
}

# Show AWS production deployment status with enhanced analysis
function aws-prod-status {
    set +e
    
    echo "📊 AWS Production Status & Cost Analysis"
    echo "======================================="
    
    # Load config if available
    if [ -f ".env.aws-prod" ]; then
        source .env.aws-prod
        echo "✅ Configuration loaded from .env.aws-prod"
        echo "   📋 Key resources:"
        grep -E "^(ECS_CLUSTER_NAME|EFS_.*_ID|VPC_ID|DATABASE_HOST)" .env.aws-prod 2>/dev/null | sed 's/^/      /' || echo "      ⚠️ Key variables not found"
    else
        echo "❌ .env.aws-prod not found"
    fi
    echo ""
    
    # Deployment status
    echo "🚀 Deployment Status:"
    if command -v aws &> /dev/null; then
        # Check ECS cluster
        if [ -n "$ECS_CLUSTER_NAME" ]; then
            cluster_status=$(aws ecs describe-clusters --clusters "$ECS_CLUSTER_NAME" --query 'clusters[0].status' --output text 2>/dev/null || echo "NOT_FOUND")
            echo "   🏗️ ECS Cluster ($ECS_CLUSTER_NAME): $cluster_status"
            
            # Check services
            service_count=$(aws ecs list-services --cluster "$ECS_CLUSTER_NAME" --query 'length(serviceArns)' --output text 2>/dev/null || echo "0")
            echo "   🔧 ECS Services: $service_count active"
        else
            echo "   ❌ ECS_CLUSTER_NAME not set"
        fi
        
        # Check Lambda functions
        lambda_count=$(aws lambda list-functions --query 'length(Functions[?starts_with(FunctionName, `fastapi-app`)])' --output text 2>/dev/null || echo "0")
        echo "   ⚡ Lambda Functions: $lambda_count deployed"
        
        # Check EFS
        if [ -n "$EFS_FILE_SYSTEM_ID" ]; then
            efs_status=$(aws efs describe-file-systems --file-system-id "$EFS_FILE_SYSTEM_ID" --query 'FileSystems[0].LifeCycleState' --output text 2>/dev/null || echo "NOT_FOUND")
            echo "   💾 Shared Models EFS: $efs_status"
        fi
    else
        echo "   ⚠️ AWS CLI not available - cannot check deployment status"
    fi
    echo ""
    
    # Cost analysis
    echo "💰 Cost Analysis:"
    python -m deployment.aws.cleanup.orphan_detector --estimate-costs --brief 2>/dev/null | head -10 || echo "   ⚠️ Cost analysis unavailable"
    echo ""
    
    # Resource health
    echo "🔍 Resource Health Check:"
    python -m deployment.aws.cleanup.orphan_detector --scan --brief 2>/dev/null | head -5 || echo "   ⚠️ Resource scan unavailable"
    echo ""
    
    # IAM verification
    echo "🔐 IAM Status:"
    python -c "
from deployment.aws.utils.iam_verification import generate_iam_verification_report
try:
    report = generate_iam_verification_report(['fastapi-app-files-api'], 'fastapi-app-ecs-cluster')
    print('   Overall IAM Status:', report['overall_status'])
    if report.get('lambda_functions'):
        for func, status in report['lambda_functions'].items():
            print(f'   Lambda {func}: {'✅ PASS' if status else '❌ FAIL'}')
    if report.get('ecs_services', {}).get('valid') is not None:
        ecs_status = report['ecs_services']['valid']
        print(f'   ECS Services: {'✅ PASS' if ecs_status else '❌ FAIL'}')
except Exception as e:
    print('   ⚠️ IAM verification unavailable:', str(e)[:50])
" 2>/dev/null
    echo ""
    
    # Database connectivity
    if [ -n "$DATABASE_HOST" ]; then
        echo "🗄️ Database Status:"
        if curl -s --connect-timeout 5 "http://$DATABASE_HOST:8080/health" >/dev/null 2>&1; then
            echo "   ✅ Database server responding on $DATABASE_HOST:8080"
        else
            echo "   ❌ Database server not responding on $DATABASE_HOST:8080"
        fi
        echo ""
    fi
    
    echo "💡 Commands:"
    echo "   • Full deployment: make aws-prod"
    echo "   • Cleanup options: make aws-prod-cleanup" 
    echo "   • Prerequisites: make aws-prod-validate"
    echo "   • Detailed costs: python -m deployment.aws.cleanup.orphan_detector --estimate-costs"
    echo "   • Resource scan: python -m deployment.aws.cleanup.orphan_detector --scan"
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