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
- **Local (aws-mock)**: Intel i7, 32GB RAM, NVIDIA RTX 4060 8GB VRAM
- **AWS (aws-prod)**: g4dn.xlarge EC2 instance (4 vCPU, 16GB RAM, Tesla T4 16GB VRAM)

## Architecture

<!-- Insert architecture diagram here -->

## How to Run

### Local Development (aws-mock)

Run the application locally with Docker to simulate the AWS environment:

```bash
make aws-mock
```

#### Prerequisites (aws-mock)

1. **Python Dependencies**: Install required packages
   ```bash
   pip install -e ".[dev]"
   ```
   Or use the pyproject.toml to install all dependencies.

2. **Docker Desktop**: Must be running
   - Verify with: `docker info`
   - Ensure Docker Compose is available: `docker-compose --version`


### AWS Production Deployment (aws-prod)

Deploy the application to AWS:

```bash
make aws-prod
```

#### Prerequisites (aws-prod)

1. **Docker Images in ECR**: Push images created from aws-mock to Amazon ECR
   ```bash
   # After running make aws-mock, tag and push images
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
   docker tag rag-worker:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-worker:latest
   ```

2. **AWS CLI Configuration**: Login with your AWS profile
   ```bash
   aws configure --profile <your-profile-name>
   export AWS_PROFILE=<your-profile-name>
   ```

3. **AWS Resources**: Ensure required AWS resources are configured
   - VPC and subnets
   - Security groups
   - IAM roles
   - Run validation: `make aws-prod-validate`

### Starting the Frontend

Open another terminal in the same directory as the codebase:

```bash
cd client
npm run dev
```

The frontend will be available at `http://localhost:3000` (or the port specified by Next.js).
