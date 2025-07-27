#!/bin/bash
# SQLite HTTP Server Installation Script
set -e

# Update system
yum update -y

# Install Python 3 and pip
yum install -y python3 python3-pip

# Install required Python packages
pip3 install flask gunicorn requests

# Create application directory
mkdir -p /opt/sqlite-server
mkdir -p /mnt/efs/database
mkdir -p /var/log/sqlite-server

# Copy server script (will be provided via user data)
cat > /opt/sqlite-server/server.py << 'PYTHON_SCRIPT_CONTENT'
PYTHON_SCRIPT_CONTENT

# Create systemd service file
cat > /etc/systemd/system/sqlite-server.service << 'EOF'
[Unit]
Description=SQLite HTTP Server
After=network.target

[Service]
Type=exec
User=root
WorkingDirectory=/opt/sqlite-server
ExecStart=/usr/bin/python3 -m gunicorn --bind 0.0.0.0:8080 --workers 4 --timeout 120 server:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
systemctl daemon-reload
systemctl enable sqlite-server
systemctl start sqlite-server

# Wait for service to start
sleep 10

# Test the service
curl -f http://localhost:8080/health || {
    echo "SQLite server failed to start"
    systemctl status sqlite-server
    exit 1
}

echo "SQLite HTTP server installation completed successfully"