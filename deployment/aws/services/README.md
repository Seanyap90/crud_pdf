# SQLite HTTP Server Setup Guide

Manual setup instructions for the SQLite HTTP server on Ubuntu 22.04 EC2 instance.

**Updated for Optimized Architecture**: Database stored on EC2 boot volume (not EFS) for better performance.

## 🚀 **Quick Setup (Ubuntu 22.04)**

### **Step 1: Connect to EC2 Instance**
```bash
# SSH into your EC2 instance (replace with your details)
ssh -i ~/.ssh/your-key.pem ubuntu@[PUBLIC_IP_ADDRESS]
```

### **Step 2: Install Dependencies**
```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python 3 and pip (Ubuntu 22.04 comes with Python 3.10)
sudo apt install -y python3 python3-pip sqlite3

# Install Flask (minimal dependencies)
pip3 install flask

# Create application directories
sudo mkdir -p /opt/sqlite-server
sudo mkdir -p /var/log/sqlite-server
sudo mkdir -p /var/lib/sqlite-server

# Set ownership to ubuntu user
sudo chown -R ubuntu:ubuntu /opt/sqlite-server
sudo chown -R ubuntu:ubuntu /var/log/sqlite-server
sudo chown -R ubuntu:ubuntu /var/lib/sqlite-server
```

### **Step 3: Copy SQLite Server Code**

**Option A: Direct copy-paste**
```bash
# Create the server file
nano /opt/sqlite-server/sqlite_server.py
# Copy and paste the entire sqlite_server.py content, then save with Ctrl+X, Y, Enter
```

**Option B: SCP transfer**
```bash
# From your local machine (run this outside SSH):
scp -i ~/.ssh/your-key.pem \
    deployment/aws/services/sqlite_server.py \
    ubuntu@[PUBLIC_IP]:/tmp/

# Then on EC2:
mv /tmp/sqlite_server.py /opt/sqlite-server/
chmod +x /opt/sqlite-server/sqlite_server.py
```

### **Step 4: Create Systemd Service**
```bash
# Create service file
sudo nano /etc/systemd/system/sqlite-server.service
```

**Service file content:**
```ini
[Unit]
Description=SQLite HTTP Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/sqlite-server
ExecStart=/usr/bin/python3 /opt/sqlite-server/sqlite_server.py --db-path /var/lib/sqlite-server/recycling.db --port 8080
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### **Step 5: Enable and Start Service**
```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable sqlite-server.service

# Start the service
sudo systemctl start sqlite-server.service

# Check service status
sudo systemctl status sqlite-server.service
```

## 🔍 **Verification & Testing**

### **Test Server Health**
```bash
# Test health endpoint
curl http://localhost:8080/health

# Expected response:
# {"status": "healthy", "timestamp": "2024-01-01T12:00:00.000000"}
```

### **Test Database Tables**
```bash
# Check if tables were created
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT name FROM sqlite_master WHERE type='\''table'\'';"}'

# Expected response should show: vendor_invoices_docs, gateways_docs, etc.
```

### **Test Document Creation**
```bash
# Test creating a document
curl -X POST http://localhost:8080/execute \
  -H "Content-Type: application/json" \
  -d '{
    "command": "INSERT INTO vendor_invoices_docs (invoice_id, document) VALUES (?, ?)",
    "params": [123, "{\"vendor\": \"test\", \"amount\": 100}"]
  }'

# Test querying documents
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT * FROM vendor_invoices_docs WHERE invoice_id = ?",
    "params": [123]
  }'
```

## 📋 **Service Management Commands**

```bash
# View service status
sudo systemctl status sqlite-server.service

# View service logs
sudo journalctl -u sqlite-server.service -f

# View application logs
tail -f /var/log/sqlite-server/server.log

# Restart service
sudo systemctl restart sqlite-server.service

# Stop service
sudo systemctl stop sqlite-server.service

# Check if service is running
curl http://localhost:8080/health
```

## 🛠️ **Troubleshooting**

### **Service Won't Start**
```bash
# Check service logs
sudo journalctl -u sqlite-server.service -n 50

# Check Python path and permissions
which python3
ls -la /opt/sqlite-server/sqlite_server.py

# Test server manually
cd /opt/sqlite-server
python3 sqlite_server.py --db-path /var/lib/sqlite-server/recycling.db --port 8080
```

### **Permission Issues**
```bash
# Fix ownership
sudo chown -R ubuntu:ubuntu /opt/sqlite-server /var/log/sqlite-server /var/lib/sqlite-server

# Fix permissions
chmod +x /opt/sqlite-server/sqlite_server.py
```

### **Port Issues**
```bash
# Check if port 8080 is in use
sudo netstat -tlnp | grep 8080

# Check security group allows port 8080
# AWS Console → EC2 → Security Groups → Check inbound rules
```

## 📁 **File Structure**
```
/opt/sqlite-server/
└── sqlite_server.py          # Main Flask application

/var/log/sqlite-server/
└── server.log                # Application logs

/var/lib/sqlite-server/
└── recycling.db              # SQLite database file (on EBS boot volume)

/etc/systemd/system/
└── sqlite-server.service     # Systemd service configuration
```

## 🔒 **Security Notes**

- Server runs on port 8080 (accessible via security group rules)
- Database stored on EBS boot volume for optimal performance
- Service runs as `ubuntu` user (non-root)
- Automatic restart on failure
- Access controlled via AWS Security Groups

## 🎯 **Next Steps**

After setup completion:
1. Note the EC2 instance's **private IP address** for Lambda configuration
2. Update your deployment code with the database endpoint
3. Test connectivity from Lambda functions
4. Monitor service logs during initial testing

## 📝 **Important Information to Collect**

After successful setup, collect these values:

```bash
# Get EC2 private IP for Lambda configuration
curl http://169.254.169.254/latest/meta-data/local-ipv4

# Test database connectivity
curl http://localhost:8080/health
```

**Save this information for your code deployment:**
- **Database Host**: [EC2_PRIVATE_IP]
- **Database Port**: 8080
- **Database Path**: `/var/lib/sqlite-server/recycling.db`