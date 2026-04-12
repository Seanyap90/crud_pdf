#!/bin/bash
# deploy.sh
#
# Creates or updates the grafana-datarecon CloudFormation stack.
# All provisioning files are embedded in cloudformation.yaml via cfn-init —
# no S3 upload or manual SSH steps required.
#
# Usage (from repo root):
#   bash deployment/aws/grafana/deploy.sh [stack-name]
#
# Example:
#   bash deployment/aws/grafana/deploy.sh grafana-datarecon

set -euo pipefail

STACK_NAME="${1:-grafana-datarecon}"
TEMPLATE="deployment/aws/grafana/cloudformation.yaml"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: Run this script from the repo root (cd /path/to/crud_pdf)"
  exit 1
fi

STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [[ "$STACK_STATUS" == "DOES_NOT_EXIST" ]]; then
  echo "==> Stack $STACK_NAME not found — creating..."
  echo -n "    EC2 key pair name: "
  read -r KEY_PAIR
  echo -n "    Your IP for SSH/Grafana access (e.g. 1.2.3.4/32, or 0.0.0.0/0 for open): "
  read -r MY_CIDR

  aws cloudformation create-stack \
    --stack-name "$STACK_NAME" \
    --template-body "file://$TEMPLATE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=KeyPairName,ParameterValue="$KEY_PAIR" \
      ParameterKey=AllowedSSHCidr,ParameterValue="$MY_CIDR" \
      ParameterKey=AllowedGrafanaCidr,ParameterValue="$MY_CIDR"

  echo "==> Waiting for CREATE_COMPLETE (up to 15 min)..."
  aws cloudformation wait stack-create-complete --stack-name "$STACK_NAME"
else
  echo "==> Stack $STACK_NAME exists ($STACK_STATUS) — updating..."
  aws cloudformation update-stack \
    --stack-name "$STACK_NAME" \
    --template-body "file://$TEMPLATE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=KeyPairName,UsePreviousValue=true \
      ParameterKey=AllowedSSHCidr,UsePreviousValue=true \
      ParameterKey=AllowedGrafanaCidr,UsePreviousValue=true \
      ParameterKey=VpcId,UsePreviousValue=true \
      ParameterKey=SubnetId,UsePreviousValue=true \
      ParameterKey=InstanceType,UsePreviousValue=true

  echo "==> Waiting for UPDATE_COMPLETE (up to 15 min)..."
  aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME"
fi

echo ""
echo "================================================================"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" --output table
echo "================================================================"
echo " Login      : admin / admin  (change on first login)"
echo " Dashboard  : Dashboards > datarecon > Waste Reconciliation"
echo "================================================================"
