"""EC2 Database Manager for SQLite HTTP Server Infrastructure."""
import logging
import json
import time
import base64
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EC2DatabaseManager:
    """Manager for EC2-based SQLite HTTP server infrastructure."""
    
    def __init__(self, region: str = None):
        self.region = region or settings.aws_region
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.instance_id = None
        self.private_ip = None
        self.security_group_id = None
        
    def create_database_instance(self, vpc_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create EC2 instance for SQLite HTTP server."""
        instance_name = f"{settings.app_name}-database-server"
        
        try:
            # Check for existing instance
            existing_instance = self._find_existing_instance(instance_name)
            if existing_instance:
                self.instance_id = existing_instance['InstanceId']
                self.private_ip = existing_instance['PrivateIpAddress']
                logger.info(f"Using existing database instance: {self.instance_id}")
                return {
                    'instance_id': self.instance_id,
                    'private_ip': self.private_ip,
                    'instance_state': existing_instance['State']['Name']
                }
            
            # Create security group for database server
            self.security_group_id = self._create_database_security_group(vpc_config)
            
            # Generate user data script
            user_data_script = self.setup_user_data_script()
            
            # Launch EC2 instance
            response = self.ec2_client.run_instances(
                ImageId=self._get_amazon_linux_ami(),
                InstanceType='t3.medium',  # Cost-effective, sufficient for SQLite
                MinCount=1,
                MaxCount=1,
                SecurityGroupIds=[self.security_group_id],
                SubnetId=vpc_config['private_subnet_id'],  # Private subnet for security
                UserData=user_data_script,
                IamInstanceProfile={
                    'Name': self._get_or_create_instance_profile()
                },
                TagSpecifications=[{
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': instance_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'SQLite-Database-Server'},
                        {'Key': 'Component', 'Value': 'Database'}
                    ]
                }],
                Monitoring={'Enabled': True}  # Enable detailed monitoring
            )
            
            instance = response['Instances'][0]
            self.instance_id = instance['InstanceId']
            self.private_ip = instance['PrivateIpAddress']
            
            logger.info(f"Created database instance: {self.instance_id}")
            
            # Wait for instance to be running
            logger.info("Waiting for database instance to be running...")
            waiter = self.ec2_client.get_waiter('instance_running')
            waiter.wait(InstanceIds=[self.instance_id])
            
            # SQLite HTTP server will start automatically via systemd
            logger.info("✅ EC2 instance running - SQLite HTTP server starting via systemd")
            logger.info("Note: Server readiness can be verified manually if needed")
            
            return {
                'instance_id': self.instance_id,
                'private_ip': self.private_ip,
                'security_group_id': self.security_group_id,
                'instance_state': 'running'
            }
            
        except ClientError as e:
            logger.error(f"Failed to create database instance: {e}")
            raise
    
    def setup_user_data_script(self) -> str:
        """Generate compact user data script for SQLite HTTP server."""
        script = """#!/bin/bash
set -e
yum update -y
yum install -y python3 python3-pip
pip3 install flask gunicorn
mkdir -p /opt/sqlite-server /var/log/sqlite-server /mnt/efs/database

# Create compact SQLite server
cat > /opt/sqlite-server/server.py << 'EOF'
import sqlite3,json,threading,os
from datetime import datetime
from flask import Flask,request,jsonify
app=Flask(__name__)
DB_PATH='/mnt/efs/database/recycling.db'
DB_LOCK=threading.RLock()
os.makedirs(os.path.dirname(DB_PATH),exist_ok=True)
if not os.path.exists(DB_PATH):
    with DB_LOCK:
        conn=sqlite3.connect(DB_PATH)
        conn.execute('CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT,collection TEXT NOT NULL,doc_id TEXT NOT NULL,document TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,UNIQUE(collection,doc_id))')
        conn.commit()
        conn.close()
@app.route('/health')
def health():return jsonify({'status':'healthy','timestamp':datetime.utcnow().isoformat()})
@app.route('/query',methods=['POST'])
def query():
    data=request.get_json()
    with DB_LOCK:
        conn=sqlite3.connect(DB_PATH)
        conn.row_factory=sqlite3.Row
        cursor=conn.execute(data['query'],data.get('params',[]))
        results=[dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({'results':results})
@app.route('/execute',methods=['POST'])
def execute():
    data=request.get_json()
    with DB_LOCK:
        conn=sqlite3.connect(DB_PATH)
        cursor=conn.execute(data['command'],data.get('params',[]))
        conn.commit()
        result={'rowcount':cursor.rowcount,'lastrowid':cursor.lastrowid}
        conn.close()
        return jsonify(result)
if __name__=='__main__':app.run(host='0.0.0.0',port=8080)
EOF

# Create systemd service
cat > /etc/systemd/system/sqlite-server.service << 'EOF'
[Unit]
Description=SQLite HTTP Server
After=network.target
[Service]
Type=exec
User=root
WorkingDirectory=/opt/sqlite-server
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sqlite-server
systemctl start sqlite-server
sleep 5
curl -f http://localhost:8080/health
"""
        return base64.b64encode(script.encode()).decode()
    
    def _get_amazon_linux_ami(self) -> str:
        """Get the latest Amazon Linux 2 AMI ID."""
        try:
            response = self.ec2_client.describe_images(
                Filters=[
                    {'Name': 'name', 'Values': ['amzn2-ami-hvm-*-x86_64-gp2']},
                    {'Name': 'owner-alias', 'Values': ['amazon']},
                    {'Name': 'state', 'Values': ['available']}
                ],
                Owners=['amazon']
            )
            
            # Sort by creation date and get the latest
            images = sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)
            if not images:
                raise Exception("No Amazon Linux 2 AMI found")
            
            ami_id = images[0]['ImageId']
            logger.info(f"Using Amazon Linux 2 AMI: {ami_id}")
            return ami_id
            
        except ClientError as e:
            logger.error(f"Failed to get Amazon Linux AMI: {e}")
            raise
    
    def _create_database_security_group(self, vpc_config: Dict[str, Any]) -> str:
        """Create security group for database server."""
        sg_name = f"{settings.app_name}-database-sg"
        
        try:
            # Check for existing security group
            try:
                response = self.ec2_client.describe_security_groups(
                    Filters=[
                        {'Name': 'group-name', 'Values': [sg_name]},
                        {'Name': 'vpc-id', 'Values': [vpc_config['vpc_id']]}
                    ]
                )
                if response['SecurityGroups']:
                    sg_id = response['SecurityGroups'][0]['GroupId']
                    logger.info(f"Using existing database security group: {sg_id}")
                    return sg_id
            except ClientError:
                pass
            
            # Create new security group
            sg_response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description="Security group for SQLite HTTP database server",
                VpcId=vpc_config['vpc_id'],
                TagSpecifications=[{
                    'ResourceType': 'security-group',
                    'Tags': [
                        {'Key': 'Name', 'Value': sg_name},
                        {'Key': 'Project', 'Value': settings.app_name},
                        {'Key': 'Purpose', 'Value': 'Database-Server'}
                    ]
                }]
            )
            sg_id = sg_response['GroupId']
            
            # Add inbound rules: Allow HTTP 8080 from Lambda and ECS workers
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8080,
                        'ToPort': 8080,
                        'IpRanges': [{'CidrIp': '10.0.0.0/16', 'Description': 'SQLite HTTP API from VPC'}]
                    }
                ]
            )
            
            logger.info(f"Created database security group: {sg_id}")
            return sg_id
            
        except ClientError as e:
            logger.error(f"Failed to create database security group: {e}")
            raise
    
    def _get_or_create_instance_profile(self) -> str:
        """Get or create IAM instance profile for EC2 database server."""
        profile_name = f"{settings.app_name}-database-instance-profile"
        role_name = f"{settings.app_name}-database-instance-role"
        
        try:
            from deployment.aws.utils.aws_clients import get_iam_client
            iam_client = get_iam_client()
            
            # Check for existing instance profile
            try:
                response = iam_client.get_instance_profile(InstanceProfileName=profile_name)
                logger.info(f"Using existing instance profile: {profile_name}")
                return profile_name
            except ClientError:
                pass
            
            # Create IAM role for EC2 instance
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            
            try:
                iam_client.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="IAM role for SQLite database server EC2 instance",
                    Tags=[
                        {'Key': 'Name', 'Value': role_name},
                        {'Key': 'Project', 'Value': settings.app_name}
                    ]
                )
                logger.info(f"Created IAM role: {role_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise
            
            # Attach basic EC2 permissions (CloudWatch, SSM for monitoring)
            iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy'
            )
            
            # Create instance profile
            try:
                iam_client.create_instance_profile(
                    InstanceProfileName=profile_name,
                    Tags=[
                        {'Key': 'Name', 'Value': profile_name},
                        {'Key': 'Project', 'Value': settings.app_name}
                    ]
                )
                logger.info(f"Created instance profile: {profile_name}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    raise
            
            # Add role to instance profile
            try:
                iam_client.add_role_to_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role_name
                )
            except ClientError as e:
                if e.response['Error']['Code'] != 'LimitExceeded':
                    raise
            
            return profile_name
            
        except ClientError as e:
            logger.error(f"Failed to create instance profile: {e}")
            raise
    
    def _find_existing_instance(self, instance_name: str) -> Optional[Dict[str, Any]]:
        """Find existing EC2 instance by name tag."""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:Name', 'Values': [instance_name]},
                    {'Name': 'tag:Project', 'Values': [settings.app_name]},
                    {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping', 'stopped']}
                ]
            )
            
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    if instance['State']['Name'] in ['running', 'pending']:
                        return instance
            return None
            
        except ClientError:
            return None
    
    def get_instance_private_ip(self, instance_id: str) -> str:
        """Get private IP address of the database instance."""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            instance = response['Reservations'][0]['Instances'][0]
            return instance['PrivateIpAddress']
        except (ClientError, KeyError, IndexError) as e:
            logger.error(f"Failed to get instance private IP: {e}")
            raise
    
    def cleanup_database_instance(self) -> None:
        """Clean up database instance and related resources."""
        try:
            if self.instance_id:
                # Terminate instance
                self.ec2_client.terminate_instances(InstanceIds=[self.instance_id])
                logger.info(f"Terminated database instance: {self.instance_id}")
                
                # Wait for termination
                waiter = self.ec2_client.get_waiter('instance_terminated')
                waiter.wait(InstanceIds=[self.instance_id])
            
            if self.security_group_id:
                # Delete security group
                try:
                    self.ec2_client.delete_security_group(GroupId=self.security_group_id)
                    logger.info(f"Deleted security group: {self.security_group_id}")
                except ClientError as e:
                    logger.warning(f"Failed to delete security group: {e}")
            
        except ClientError as e:
            logger.error(f"Failed to cleanup database instance: {e}")
            raise