# API Gateway Integration

## Setup Instructions

### Option 1: Using API Gateway ID (Recommended)
```bash
export API_GATEWAY_ID=your-api-gateway-id
make aws-prod
```

### Option 2: Using Direct URL
```bash
export API_GATEWAY_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com
make aws-prod
```

## Verification

The ECS workers will use the API Gateway URL to communicate with the Lambda API for invoice status updates.

## How It Works

1. **Environment Variable Detection**: The system checks for `API_GATEWAY_ID` or `API_GATEWAY_URL` environment variables
2. **URL Construction**: If `API_GATEWAY_ID` is provided, the system automatically constructs the full URL: `https://{api-id}.execute-api.{region}.amazonaws.com`
3. **ECS Integration**: The API Gateway URL is injected into ECS container environment variables (`API_GATEWAY_URL` and `API_BASE_URL`)
4. **Storage Adapter**: The storage adapter uses the API Gateway URL for HTTP calls to update invoice status
5. **Deployment Validation**: The deployment script validates API Gateway configuration before proceeding

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `API_GATEWAY_ID` | API Gateway ID (recommended) | `abc123def4` |
| `API_GATEWAY_URL` | Direct API Gateway URL | `https://abc123def4.execute-api.us-east-1.amazonaws.com` |
| `AWS_DEFAULT_REGION` | AWS region for URL construction | `us-east-1` |

## Troubleshooting

### No API Gateway Configuration Found
If you see this warning during deployment:
```
⚠️ No API Gateway configuration found
💡 Set API_GATEWAY_ID or API_GATEWAY_URL environment variable
```

**Solution**: Set one of the environment variables before deployment:
```bash
export API_GATEWAY_ID=your-api-gateway-id
# OR
export API_GATEWAY_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com
```

### ECS Workers Cannot Communicate with API
If ECS workers fail to update invoice status:

1. **Check Environment Variables**: Verify that `API_GATEWAY_URL` is set in ECS task definition
2. **Check API Gateway**: Ensure your API Gateway is deployed and accessible
3. **Check Security Groups**: Ensure ECS tasks can reach the API Gateway (outbound HTTPS traffic)
4. **Check Logs**: Review ECS task logs for HTTP connection errors

### API Gateway Validation Failed
If you see validation errors:
```
API Gateway validation failed for abc123def4: ...
```

**Solution**: Verify that:
- The API Gateway ID exists in your AWS account
- Your AWS credentials have permission to access API Gateway
- The API Gateway is in the correct region

## Integration Points

### Files Modified
1. `src/files_api/adapters/storage.py` - API Gateway URL detection
2. `deployment/aws/infrastructure/ecs_task_definitions.py` - ECS environment variable injection
3. `deployment/aws/orchestration/deploy_ecs.py` - Deployment integration
4. `run.sh` - Pre-deployment validation

### New Files Created
1. `deployment/aws/utils/api_gateway.py` - API Gateway management utilities
2. `deployment/aws/README_API_GATEWAY.md` - This documentation

## Example Deployment Flow

```bash
# 1. Set API Gateway ID
export API_GATEWAY_ID=abc123def4

# 2. Deploy to AWS
make aws-prod

# 3. Verify deployment
# Check ECS task environment variables contain API_GATEWAY_URL
aws ecs describe-task-definition --task-definition fastapi-app-vlm-worker
```

## API Gateway URL Construction

The system automatically constructs API Gateway URLs using this pattern:
```
https://{api-id}.execute-api.{region}.amazonaws.com
```

Where:
- `{api-id}` comes from `API_GATEWAY_ID` environment variable
- `{region}` comes from `AWS_DEFAULT_REGION` environment variable (defaults to `us-east-1`)

This ensures consistent URL formatting and reduces configuration errors.