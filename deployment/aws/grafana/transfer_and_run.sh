#!/bin/bash
# transfer_and_run.sh
#
# Pushes Grafana provisioning files to the EC2 instance and activates the
# Waste Reconciliation dashboard. Grafana calls the datarecon API Gateway
# directly — no source code or FastAPI needed on the instance.
#
# Usage:
#   bash deployment/aws/grafana/transfer_and_run.sh <ec2-public-ip> <path-to-pem>
#
# Example:
#   bash deployment/aws/grafana/transfer_and_run.sh 3.235.185.116 ~/.ssh/grafana-ec2-key.pem
#
# Prerequisites:
#   - CloudFormation stack deployed and in CREATE_COMPLETE state
#   - Run from the repo root: cd /path/to/crud_pdf

set -euo pipefail

EC2_IP="${1:?Usage: $0 <ec2-public-ip> <path-to-pem>}"
PEM="${2:?Usage: $0 <ec2-public-ip> <path-to-pem>}"

SSH_OPTS="-i $PEM -o StrictHostKeyChecking=no -o ConnectTimeout=15"

echo "==> Waiting for SSH on $EC2_IP..."
for i in $(seq 1 20); do
  if ssh $SSH_OPTS ubuntu@$EC2_IP "echo ok" &>/dev/null; then
    echo "    SSH ready."
    break
  fi
  echo "    Attempt $i/20 — retrying in 10s..."
  sleep 10
done

echo "==> Waiting for Grafana service to be running..."
ssh $SSH_OPTS ubuntu@$EC2_IP \
  "until systemctl is-active --quiet grafana-server; do echo '    Waiting...'; sleep 5; done"
echo "    Grafana is running."

echo "==> Transferring Grafana provisioning files..."
scp -r $SSH_OPTS \
  deployment/aws/grafana/ \
  ubuntu@$EC2_IP:/home/ubuntu/grafana-setup/

echo "==> Copying provisioning files into Grafana directories..."
ssh $SSH_OPTS ubuntu@$EC2_IP "sudo bash -s" << 'REMOTE'
  set -e
  SETUP=/home/ubuntu/grafana-setup

  mkdir -p /var/lib/grafana/dashboards

  cp "$SETUP/provisioning/datasources/infinity.yaml" \
     /etc/grafana/provisioning/datasources/infinity.yaml

  cp "$SETUP/provisioning/dashboards/dashboard.yaml" \
     /etc/grafana/provisioning/dashboards/dashboard.yaml

  cp "$SETUP/provisioning/dashboards/datarecon.json" \
     /var/lib/grafana/dashboards/datarecon.json

  chown -R grafana:grafana \
    /etc/grafana/provisioning/datasources/infinity.yaml \
    /etc/grafana/provisioning/dashboards/dashboard.yaml \
    /var/lib/grafana/dashboards/datarecon.json

  systemctl restart grafana-server

  sleep 5
  if ! systemctl is-active --quiet grafana-server; then
    echo "ERROR: grafana-server failed to restart."
    journalctl -u grafana-server --no-pager -n 30
    exit 1
  fi
REMOTE

echo ""
echo "================================================================"
echo " Deployment complete!"
echo " Grafana UI : http://$EC2_IP:3000"
echo " Login      : admin / admin  (change on first login)"
echo " API target : https://1yyjmleyke.execute-api.us-east-1.amazonaws.com/dev"
echo " Dashboard  : Dashboards > datarecon > Waste Reconciliation"
echo "================================================================"
