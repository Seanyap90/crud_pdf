# src/files_api/aws/container_scaler.py
import subprocess
import boto3
import logging
import threading
import time
import os

logger = logging.getLogger(__name__)

class ContainerScaler:
    """Bridge between AWS AutoScaling and Docker containers."""
    
    def __init__(self, asg_name="rag-worker-asg", compose_file="docker-compose.aws-mock.yml", poll_interval=30):
        self.asg_name = asg_name
        self.compose_file = compose_file
        self.poll_interval = poll_interval
        self.running = True
        
        # Initialize boto3 client
        self.autoscaling = boto3.client(
            'autoscaling',
            endpoint_url=os.environ.get('AWS_ENDPOINT_URL', 'http://localhost:5000'),
            region_name=os.environ.get('AWS_REGION', 'us-east-1'),
            aws_access_key_id='mock',
            aws_secret_access_key='mock'
        )
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
    
    def start(self):
        """Start the container scaler."""
        logger.info(f"Starting container scaler for ASG {self.asg_name}")
        self.monitor_thread.start()
    
    def stop(self):
        """Stop the container scaler."""
        logger.info("Stopping container scaler")
        self.running = False
        if self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2)
    
    def _monitor_loop(self):
        """Monitor ASG and sync with Docker containers."""
        while self.running:
            try:
                # Get ASG desired capacity
                response = self.autoscaling.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[self.asg_name]
                )
                
                if response['AutoScalingGroups']:
                    asg = response['AutoScalingGroups'][0]
                    desired_capacity = asg['DesiredCapacity']
                    
                    # Get current container count
                    current_count = self._get_container_count()
                    
                    # Sync container count with ASG
                    if current_count != desired_capacity:
                        logger.info(f"Scaling containers from {current_count} to {desired_capacity}")
                        self._scale_containers(desired_capacity)
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {str(e)}")
            
            # Sleep before next check
            time.sleep(self.poll_interval)
    
    def _get_container_count(self):
        """Get current number of worker containers."""
        try:
            # Use docker ps to count containers
            cmd = "docker ps --filter name=aws-mock_worker --format '{{.Names}}' | wc -l"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            count = int(result.stdout.strip())
            return count
        except Exception as e:
            logger.error(f"Error getting container count: {str(e)}")
            return 0
    
    def _scale_containers(self, desired_count):
        """Scale containers to match desired count."""
        try:
            # Use docker-compose to scale with env file
            cmd = f"docker-compose -f {self.compose_file} --env-file .env.aws up -d --scale worker={desired_count}"
            subprocess.run(cmd, shell=True)
            logger.info(f"Scaled to {desired_count} containers")
        except Exception as e:
            logger.error(f"Error scaling containers: {str(e)}")