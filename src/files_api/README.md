# Invoice Processing with ColPali and SmolVLM2

## Summary

This is a serverless invoice processing API that uses Vision Language Models (VLMs) for document understanding and RAG capabilities. The system processes PDF invoices, extracts structured data using ColPali and SmolVLM2, and provides both REST API access via AWS Lambda and asynchronous processing via ECS workers.

Key features:
- **Document Upload & Processing**: Upload invoices for automated data extraction
- **VLM-powered Extraction**: Uses ColPali for document retrieval and SmolVLM2 for vision-language understanding
- **Decoupled Architecture**: Separate API layer (Lambda) from compute layer (ECS workers) with queue-based communication for scalability and fault tolerance
- **Storage & Queuing**: S3 for document storage, SQS for task queuing
- **Database**: SQLite on EC2 for structured data storage

**Tested Configurations:**
- **Local (deploy-aws-local)**: Intel i7, 32GB RAM, NVIDIA RTX 4060 8GB VRAM
- **AWS (deploy-aws)**: g4dn.xlarge EC2 instance (4 vCPU, 16GB RAM, Tesla T4 16GB VRAM)

## Architecture


<img width="807" height="1042" alt="DataReconcile_vlm_rag_iot-files_api drawio" src="https://github.com/user-attachments/assets/27a5f184-05ec-45a8-ad88-143647d90793" />


## Initial Setup

Before running the application locally or deploying to AWS, you need to install the required Python dependencies.

### Installing Dependencies with pyproject.toml

From the root directory of the project, install all required packages:

```bash
pip install -e ".[dev]"
```

This command installs:
- Core application dependencies
- Development dependencies (testing, linting, etc.)
- The package in editable mode for local development

**Note**: These dependencies are required for both:
- Running the application locally (`make deploy-aws-local`)
- Executing deployment scripts to AWS (`make deploy-aws`)

### Verify Installation

You can verify the installation by checking if key packages are available:

```bash
python -c "import fastapi, boto3, torch; print('Dependencies installed successfully')"
```

## AWS Infrastructure Setup

**This section is ONLY required for `make deploy-aws` (AWS production deployment).**

If you're running `make deploy-aws-local`, you can **skip this entire section** as it runs locally with Docker and mocked AWS services.

Before running `make deploy-aws`, you need to manually configure the following AWS resources through the AWS Console. This section documents all the setup steps required.

### Overview

The following AWS resources must be configured:
- ✓ VPC with public subnets
- ✓ Security Groups for Lambda, ECS, and EC2
- ✓ IAM Roles and Policies
- ✓ EC2 instance for SQLite database
- ✓ API Gateway
- ✓ Lambda functions
- ✓ ECS cluster and task definitions

### 4.1 VPC Configuration

Create a VPC with public subnets to host all your AWS resources.

#### Steps:
1. Navigate to **VPC Console** → **Create VPC**
2. Configure VPC settings:
   - Name: `files-api-vpc` (or your preferred name)
   - IPv4 CIDR block: `10.0.0.0/16` (or your preferred range)
3. Create **public subnets** across multiple availability zones:
   - Subnet 1: `10.0.1.0/24` (us-east-1a)
   - Subnet 2: `10.0.2.0/24` (us-east-1b)
4. Create and attach an **Internet Gateway**:
   - Name: `files-api-igw`
   - Attach to your VPC
5. Configure **Route Tables**:
   - Create a route table for public subnets
   - Add route: `0.0.0.0/0` → Internet Gateway
   - Associate public subnets with this route table

<img width="800" alt="VPC Configuration Overview" src="[PLACEHOLDER - Upload VPC dashboard screenshot]" />

<img width="800" alt="Public Subnets Configuration" src="[PLACEHOLDER - Upload subnets list screenshot]" />

<img width="800" alt="Route Table with Internet Gateway" src="[PLACEHOLDER - Upload route table screenshot]" />

### 4.2 Security Groups

Create security groups to control traffic to your Lambda functions, ECS tasks, and EC2 instance.

#### Lambda Security Group

Create a security group for Lambda functions:
- Name: `files-api-lambda-sg`
- VPC: Select your VPC
- **Inbound rules**: None required (Lambda is invoked via API Gateway)
- **Outbound rules**:
  - Type: All traffic, Destination: `0.0.0.0/0` (for internet access and EC2 database)

<img width="800" alt="Lambda Security Group Rules" src="[PLACEHOLDER - Upload Lambda SG screenshot]" />

#### ECS Security Group

Create a security group for ECS tasks:
- Name: `files-api-ecs-sg`
- VPC: Select your VPC
- **Inbound rules**: None required (tasks are not directly accessed)
- **Outbound rules**:
  - Type: All traffic, Destination: `0.0.0.0/0` (for S3, SQS, Lambda, and EC2 access)

<img width="800" alt="ECS Security Group Rules" src="[PLACEHOLDER - Upload ECS SG screenshot]" />

#### EC2 Security Group (SQLite Server)

Create a security group for the EC2 SQLite database server:
- Name: `files-api-ec2-sg`
- VPC: Select your VPC
- **Inbound rules**:
  - Type: Custom TCP, Port: `8000`, Source: Lambda Security Group
  - Type: Custom TCP, Port: `8000`, Source: ECS Security Group
  - Type: SSH, Port: `22`, Source: Your IP (for management)
- **Outbound rules**:
  - Type: All traffic, Destination: `0.0.0.0/0`

<img width="800" alt="EC2 Security Group Rules" src="[PLACEHOLDER - Upload EC2 SG screenshot]" />

### 4.3 IAM Roles and Policies

Create IAM roles with appropriate policies for Lambda and ECS services.

#### Lambda Execution Role

Create an IAM role for Lambda function execution:

1. Navigate to **IAM Console** → **Roles** → **Create role**
2. Select **AWS service** → **Lambda**
3. Role name: `files-api-lambda-role`

**AWS Managed Policies:**
- `AWSLambdaVPCAccessExecutionRole` (for VPC networking)
- `AWSLambdaBasicExecutionRole` (for CloudWatch Logs)

**Inline Policy** (for accessing EC2, S3, SQS):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeNetworkInterfaces",
        "ec2:CreateNetworkInterface",
        "ec2:DeleteNetworkInterface",
        "ec2:DescribeInstances",
        "ec2:AttachNetworkInterface"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::your-bucket-name/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:*:*:*"
    }
  ]
}
```

<img width="800" alt="Lambda IAM Role Summary" src="[PLACEHOLDER - Upload Lambda role screenshot]" />

<img width="800" alt="Lambda Inline Policy JSON" src="[PLACEHOLDER - Upload Lambda inline policy screenshot]" />

#### ECS Task Execution Role

Create an IAM role for ECS task execution (pulling images from ECR):

1. Navigate to **IAM Console** → **Roles** → **Create role**
2. Select **AWS service** → **Elastic Container Service** → **Elastic Container Service Task**
3. Role name: `files-api-ecs-execution-role`

**AWS Managed Policies:**
- `AmazonECSTaskExecutionRolePolicy`

**Inline Policy** (for ECR access):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

<img width="800" alt="ECS Execution Role Summary" src="[PLACEHOLDER - Upload ECS execution role screenshot]" />

<img width="800" alt="ECS Execution Inline Policy JSON" src="[PLACEHOLDER - Upload ECS execution inline policy screenshot]" />

#### ECS Task Role

Create an IAM role for ECS tasks (application permissions):

1. Navigate to **IAM Console** → **Roles** → **Create role**
2. Select **AWS service** → **Elastic Container Service** → **Elastic Container Service Task**
3. Role name: `files-api-ecs-task-role`

**Inline Policy** (for S3, SQS, Lambda access):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket-name",
        "arn:aws:s3:::your-bucket-name/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility"
      ],
      "Resource": "arn:aws:sqs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:*:*:function:*"
    }
  ]
}
```

<img width="800" alt="ECS Task Role Summary" src="[PLACEHOLDER - Upload ECS task role screenshot]" />

<img width="800" alt="ECS Task Inline Policy JSON" src="[PLACEHOLDER - Upload ECS task inline policy screenshot]" />

### 4.4 EC2 Instance (SQLite Database Server)

Set up an EC2 instance to host the SQLite database server.

#### Steps:
1. Navigate to **EC2 Console** → **Launch Instance**
2. **AMI Selection**: Ubuntu Server 22.04 LTS or Amazon Linux 2023
3. **Instance Type**: `t3.small` or `t2.micro` (adjust based on your needs)
4. **Network Settings**:
   - VPC: Select your VPC
   - Subnet: Select one of your public subnets
   - Auto-assign public IP: **Enable**
5. **Security Group**: Select `files-api-ec2-sg` (created earlier)
6. **Storage**: 20 GB gp3 (or as needed)
7. Launch the instance

#### Obtain Public IP Address:
After the instance is running:
1. Go to **EC2 Console** → **Instances**
2. Select your instance
3. Copy the **Public IPv4 address** (e.g., `54.123.45.67`)
4. **Important**: This IP will be used as the `DB_HOST` environment variable for Lambda and ECS

<img width="800" alt="EC2 Instance Launch Configuration" src="[PLACEHOLDER - Upload EC2 launch config screenshot]" />

<img width="800" alt="EC2 Instance Details with Public IP" src="[PLACEHOLDER - Upload EC2 instance details screenshot]" />

### 4.5 API Gateway Setup

Create an API Gateway to expose Lambda functions as REST endpoints.

#### Steps:
1. Navigate to **API Gateway Console** → **Create API**
2. Select **REST API** → **Build**
3. API name: `files-api-gateway`
4. **Create Resources and Methods**:
   - Create resource: `/v1`
   - Create proxy resource: `/{proxy+}` with `ANY` method
   - Integration: Lambda Function (proxy integration)
5. **Enable CORS** (if needed):
   - Select resource → **Enable CORS**
   - Configure allowed origins, methods, headers
6. **Deploy API**:
   - Actions → **Deploy API**
   - Stage name: `prod` or `dev`
7. **Copy Invoke URL**: You'll see a URL like `https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod`
   - **Important**: This URL will be used as the `LAMBDA_URL` environment variable for ECS tasks

<img width="800" alt="API Gateway Resources" src="[PLACEHOLDER - Upload API Gateway resources screenshot]" />

<img width="800" alt="API Gateway Method Integration" src="[PLACEHOLDER - Upload API Gateway integration screenshot]" />

<img width="800" alt="API Gateway Deployment Stages" src="[PLACEHOLDER - Upload API Gateway stages screenshot]" />

<img width="800" alt="API Gateway Invoke URL" src="[PLACEHOLDER - Upload API Gateway invoke URL screenshot]" />

### 4.6 Lambda Function Configuration

Configure Lambda function with VPC settings and environment variables.

#### Steps:
1. Navigate to **Lambda Console** → **Create function**
2. Function name: `files-api-handler`
3. Runtime: Python 3.11 or 3.12
4. **VPC Configuration**:
   - VPC: Select your VPC
   - Subnets: Select your public subnets
   - Security groups: Select `files-api-lambda-sg`
5. **Environment Variables**:
   - `DB_HOST`: `<EC2-public-IP>` (e.g., `54.123.45.67`)
   - `DB_PORT`: `8000`
   - `AWS_REGION`: `us-east-1`
   - Other variables as required by your application
6. **IAM Role**: Assign `files-api-lambda-role`
7. **Timeout**: Set to 30 seconds or more (adjust based on workload)
8. **Memory**: 512 MB or more (adjust based on workload)

<img width="800" alt="Lambda VPC Configuration" src="[PLACEHOLDER - Upload Lambda VPC config screenshot]" />

<img width="800" alt="Lambda Environment Variables" src="[PLACEHOLDER - Upload Lambda env vars screenshot]" />

<img width="800" alt="Lambda General Configuration" src="[PLACEHOLDER - Upload Lambda general config screenshot]" />

### 4.7 ECS Configuration

Configure ECS cluster, task definition, and service.

#### Create ECS Cluster:
1. Navigate to **ECS Console** → **Clusters** → **Create cluster**
2. Cluster name: `files-api-cluster`
3. Infrastructure: AWS Fargate

#### Create Task Definition:
1. Navigate to **Task Definitions** → **Create new task definition**
2. Task definition family: `files-api-worker`
3. **Infrastructure**:
   - Launch type: Fargate
   - CPU: 4 vCPU
   - Memory: 16 GB (adjust based on model requirements)
4. **Container Definition**:
   - Name: `rag-worker`
   - Image URI: `<your-account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest`
   - **Environment Variables**:
     - `LAMBDA_URL`: `<API-Gateway-Invoke-URL>` (e.g., `https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod`)
     - `AWS_REGION`: `us-east-1`
     - `S3_BUCKET`: Your S3 bucket name
     - `SQS_QUEUE_URL`: Your SQS queue URL
     - `DB_HOST`: `<EC2-public-IP>`
     - Other variables as required
5. **IAM Roles**:
   - Task execution role: `files-api-ecs-execution-role`
   - Task role: `files-api-ecs-task-role`

#### Create ECS Service:
1. Navigate to your cluster → **Services** → **Create**
2. Launch type: Fargate
3. Task definition: Select `files-api-worker`
4. Service name: `rag-worker-service`
5. **Networking**:
   - VPC: Select your VPC
   - Subnets: Select your public subnets
   - Security groups: Select `files-api-ecs-sg`
   - Auto-assign public IP: **ENABLED**
6. Desired tasks: 1 (or more based on workload)

<img width="800" alt="ECS Task Definition Details" src="[PLACEHOLDER - Upload ECS task definition screenshot]" />

<img width="800" alt="ECS Container Environment Variables" src="[PLACEHOLDER - Upload ECS container env vars screenshot]" />

<img width="800" alt="ECS Service Network Configuration" src="[PLACEHOLDER - Upload ECS service network screenshot]" />

### 4.8 Configuration Summary

After completing all AWS Console setup, you should have the following configuration values:

| Component | Parameter | Example Value | Where to Find |
|-----------|-----------|---------------|---------------|
| EC2 Instance | Public IP | `54.123.45.67` | EC2 Console → Instances → Select instance → Public IPv4 address |
| Lambda | DB_HOST | `54.123.45.67` | Use EC2 public IP |
| API Gateway | Invoke URL | `https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod` | API Gateway Console → Stages → prod → Invoke URL |
| ECS Task | LAMBDA_URL | `https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod` | Use API Gateway invoke URL |
| All Services | AWS_REGION | `us-east-1` | Your selected region |

**Configuration Checklist:**
- [ ] VPC and public subnets created
- [ ] Security groups configured for Lambda, ECS, and EC2
- [ ] IAM roles created with inline policies
- [ ] EC2 instance running with SQLite server
- [ ] EC2 public IP address noted
- [ ] API Gateway created and deployed
- [ ] API Gateway invoke URL obtained
- [ ] Lambda function configured with DB_HOST environment variable
- [ ] ECS task definition created with LAMBDA_URL environment variable
- [ ] ECS service running

## How to Run

There are two deployment modes:
1. **Local Development (`deploy-aws-local`)**: Runs entirely on your local machine with Docker, no AWS infrastructure required
2. **AWS Production (`deploy-aws`)**: Deploys to actual AWS infrastructure (requires AWS Console setup above)

### Local Development (deploy-aws-local)

Run the application locally with Docker to simulate the AWS environment. This mode:
- Uses Moto server to mock AWS services (S3, SQS, etc.)
- Downloads ML models to local Docker volumes (first time only)
- Creates Docker images that can later be pushed to ECR
- Runs FastAPI on `localhost:8000`
- **Does NOT require any AWS infrastructure setup**

```bash
make deploy-aws-local
```

#### What This Does:
1. Starts a local Moto server to simulate AWS services
2. Runs a model-downloader container (first time) to download ColPali and SmolVLM2 models
3. Starts the VLM worker container for processing
4. Launches FastAPI application on localhost:8000
5. Simulates autoscaling behavior

#### Prerequisites (deploy-aws-local)

1. **Python Dependencies**: Install required packages (from Initial Setup section)
   ```bash
   pip install -e ".[dev]"
   ```

2. **Docker Desktop**: Must be running
   - Verify with: `docker info`
   - Ensure Docker Compose is available: `docker-compose --version`

3. **Disk Space**: ~10-15GB for ML models (downloaded once, cached in Docker volumes)


### AWS Production Deployment (deploy-aws)

Deploy the application to actual AWS infrastructure. This mode:
- Deploys to real AWS services (Lambda, ECS, API Gateway, EC2)
- Requires all AWS infrastructure configured via AWS Console (see section above)
- Uses Docker images from ECR
- Requires custom AMI with ML models pre-baked

**⚠️ Important**: Complete the "AWS Infrastructure Setup" section above before proceeding.

#### Deployment Workflow:

1. **First, run `deploy-aws-local`** to create Docker images:
   ```bash
   make deploy-aws-local
   ```

2. **Push Docker images to Amazon ECR**:
   ```bash
   # Login to ECR
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

   # Create ECR repository (if not exists)
   aws ecr create-repository --repository-name rag-worker --region us-east-1

   # Tag and push the image
   docker tag vlm-worker:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest
   ```

3. **Deploy to AWS**:
   ```bash
   make deploy-aws
   ```

#### What This Does:
1. Detects or builds custom AMI with ML models pre-installed (saves ~2 hours if recent AMI exists)
2. Deploys Lambda functions for API endpoints
3. Creates/updates ECS cluster, task definitions, and services
4. Configures EventBridge scaling Lambda functions
5. Sets up all necessary AWS infrastructure via deployment scripts

#### Prerequisites (deploy-aws)

1. **Complete AWS Infrastructure Setup**: See "AWS Infrastructure Setup" section above
   - VPC and public subnets
   - Security groups
   - IAM roles with inline policies
   - EC2 instance for SQLite database
   - API Gateway created and deployed
   - All configuration values noted (DB_HOST, API Gateway URL)

2. **Docker Images in ECR**: Push images created from `deploy-aws-local` to Amazon ECR (see workflow above)

3. **AWS CLI Configuration**: Login with your AWS profile
   ```bash
   aws configure --profile <your-profile-name>
   export AWS_PROFILE=<your-profile-name>
   ```

4. **Environment Variables**: Set in `.env.deploy-aws` file
   - `CUSTOM_AMI_ID`: AMI ID (auto-detected if built recently, or follow manual AMI build guide)
   - `DB_HOST`: EC2 public IP address
   - AWS region, VPC IDs, subnet IDs, security group IDs
   - S3 bucket name, SQS queue URL

5. **Validation**: Run validation before deployment
   ```bash
   make deploy-aws-validate
   ```

### Frontend Configuration and Startup

The frontend needs to be configured to point to the correct API endpoint depending on whether you're running locally or on AWS.

#### Configure API_BASE_URL

1. **Open a separate terminal session** (keep the backend running in the other terminal)

2. **Navigate to the frontend directory**:
   ```bash
   cd files-api-client
   ```

3. **Edit the API configuration file**: `files-api-client/lib/api_client.ts`

   The file contains two API_BASE_URL configurations (lines 18-20):

   **For Local Development (deploy-aws-local)**:
   ```typescript
   // Comment out the AWS URL and uncomment the local URL
   // const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://jbmiwd4wt7.execute-api.us-east-1.amazonaws.com/dev';
   const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
   ```

   **For AWS Deployment (deploy-aws)**:
   ```typescript
   // Use your actual API Gateway invoke URL
   const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod';
   // const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
   ```

4. **Start the frontend**:
   ```bash
   npm run dev
   ```

5. **Access the application**:
   The frontend will be available at `http://localhost:3000` (or the port specified by Next.js).

**Important**: Remember to update `API_BASE_URL` in `api_client.ts` whenever you switch between local and AWS deployments.
