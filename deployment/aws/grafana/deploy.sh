#!/bin/bash
# deploy.sh
#
# Uploads Grafana provisioning files to S3, then creates or updates the
# CloudFormation stack. The EC2 instance downloads these files during
# UserData bootstrap — no manual SSH/SCP needed after deploy.
#
# Usage (from repo root):
#   bash deployment/aws/grafana/deploy.sh [stack-name]
#
# Example:
#   bash deployment/aws/grafana/deploy.sh grafana-datarecon

set -euo pipefail

STACK_NAME="${1:-grafana-datarecon}"
BUCKET="rag-pdf-storage-1751732972"
PREFIX="grafana/provisioning"
TEMPLATE="deployment/aws/grafana/cloudformation.yaml"
PROVISIONING_DIR="deployment/aws/grafana/provisioning"

# ── Verify we're in the repo root ──
if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: Run this script from the repo root (cd /path/to/crud_pdf)"
  exit 1
fi

echo "==> Uploading provisioning files to s3://$BUCKET/$PREFIX/ ..."
aws s3 cp "$PROVISIONING_DIR/datasources/infinity.yaml" \
  "s3://$BUCKET/$PREFIX/datasources/infinity.yaml"
aws s3 cp "$PROVISIONING_DIR/dashboards/dashboard.yaml" \
  "s3://$BUCKET/$PREFIX/dashboards/dashboard.yaml"
aws s3 cp "$PROVISIONING_DIR/dashboards/datarecon.json" \
  "s3://$BUCKET/$PREFIX/dashboards/datarecon.json"
echo "    Done."

# ── Detect create vs update ──
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [[ "$STACK_STATUS" == "DOES_NOT_EXIST" ]]; then
  echo "==> Creating stack $STACK_NAME ..."
  echo "    KeyPairName? (enter key pair name):"
  read -r KEY_PAIR
  aws cloudformation create-stack \
    --stack-name "$STACK_NAME" \
    --template-body "file://$TEMPLATE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=KeyPairName,ParameterValue="$KEY_PAIR" \
      ParameterKey=AllowedSSHCidr,ParameterValue="0.0.0.0/0" \
      ParameterKey=AllowedGrafanaCidr,ParameterValue="0.0.0.0/0"
  echo "==> Waiting for stack create to complete..."
  aws cloudformation wait stack-create-complete --stack-name "$STACK_NAME"
else
  echo "==> Updating stack $STACK_NAME (current status: $STACK_STATUS) ..."
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
      ParameterKey=InstanceType,UsePreviousValue=true \
      ParameterKey=ProvisioningBucket,UsePreviousValue=true \
      ParameterKey=ProvisioningPrefix,UsePreviousValue=true
  echo "==> Waiting for stack update to complete..."
  aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME"
fi

echo ""
echo "================================================================"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" --output table
echo "================================================================"
echo " Login: admin / admin  (change on first login)"
echo " Dashboard: Dashboards > datarecon > Waste Reconciliation"
echo "================================================================"
