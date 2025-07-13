"""
ECS Auto-scaling with CloudWatch Metrics
Implements native AWS auto-scaling for ECS services with scale-to-zero capability.
Enhanced with Docker Compose service discovery for hybrid deployments.
"""
import logging
import time
import json
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path

from files_api.aws.utils import (
    get_ecs_client,
    get_sqs_client,
    get_cloudwatch_client,
    get_application_autoscaling_client
)
from files_api.settings import get_settings

logger = logging.getLogger(__name__)

@dataclass
class ECSScalingConfig:
    """ECS service scaling configuration."""
    service_name: str
    cluster_name: str
    min_capacity: int = 0  # Scale to zero capability
    max_capacity: int = 3  # HARD LIMIT: cost-controlled GPU instances
    target_queue_messages_per_task: int = 1  # 1 message = 1 task
    scale_out_cooldown: int = 300  # 5 minutes
    scale_in_cooldown: int = 600   # 10 minutes (longer for cost control)
    evaluation_periods: int = 2    # Consecutive periods before scaling
    datapoint_period: int = 60     # 1 minute periods

@dataclass 
class ScalingEvent:
    """Represents a scaling event."""
    timestamp: datetime
    action: str  # 'scale_out', 'scale_in', 'no_action'
    reason: str
    queue_messages: int
    running_tasks_before: int
    running_tasks_after: int
    desired_capacity: int
    
    def to_dict(self):
        return {
            **asdict(self),
            'timestamp': self.timestamp.isoformat()
        }


class ECSAutoScaler:
    """Native CloudWatch auto-scaling for ECS services."""
    
    def __init__(self, config: ECSScalingConfig):
        self.settings = get_settings()
        self.config = config
        self.ecs_client = get_ecs_client()
        self.sqs_client = get_sqs_client()
        self.cloudwatch_client = get_cloudwatch_client()
        self.autoscaling_client = get_application_autoscaling_client()
        
        # Scaling target resource ID
        self.resource_id = f"service/{config.cluster_name}/{config.service_name}"
        self.namespace = "ecs"
        self.scalable_dimension = "ecs:service:DesiredCount"
        
        self.scaling_events: List[ScalingEvent] = []
        self.last_scale_action_time = None
        
    def setup_auto_scaling(self, queue_url: str) -> Dict[str, Any]:
        """Set up CloudWatch auto-scaling for the ECS service."""
        try:
            # Register scalable target
            self._register_scalable_target()
            
            # Create custom CloudWatch metric for BacklogPerTask
            self._create_backlog_per_task_metric(queue_url)
            
            # Create scaling policies
            scale_out_policy_arn = self._create_scale_out_policy()
            scale_in_policy_arn = self._create_scale_in_policy()
            
            # Create CloudWatch alarms
            self._create_scale_out_alarm(scale_out_policy_arn, queue_url)
            self._create_scale_in_alarm(scale_in_policy_arn, queue_url)
            
            scaling_setup = {
                "resource_id": self.resource_id,
                "min_capacity": self.config.min_capacity,
                "max_capacity": self.config.max_capacity,
                "scale_out_policy": scale_out_policy_arn,
                "scale_in_policy": scale_in_policy_arn,
                "metric_name": "BacklogPerTask",
                "status": "active"
            }
            
            logger.info(f"Auto-scaling configured for {self.config.service_name}")
            return scaling_setup
            
        except Exception as e:
            logger.error(f"Failed to setup auto-scaling: {e}")
            raise
    
    def _register_scalable_target(self) -> None:
        """Register the ECS service as a scalable target."""
        try:
            self.autoscaling_client.register_scalable_target(
                ServiceNamespace=self.namespace,
                ResourceId=self.resource_id,
                ScalableDimension=self.scalable_dimension,
                MinCapacity=self.config.min_capacity,
                MaxCapacity=self.config.max_capacity
            )
            logger.info(f"Registered scalable target: {self.resource_id}")
            
        except self.autoscaling_client.exceptions.ValidationException as e:
            if "already exists" in str(e):
                logger.info(f"Scalable target already exists: {self.resource_id}")
            else:
                raise
        except Exception as e:
            logger.error(f"Failed to register scalable target: {e}")
            raise
    
    def _create_backlog_per_task_metric(self, queue_url: str) -> None:
        """Create custom CloudWatch metric for BacklogPerTask calculation."""
        # This would typically be done by a Lambda function or CloudWatch Logs Insights
        # For now, we'll document the metric calculation logic
        
        metric_name = "BacklogPerTask"
        namespace = "ECS/VLM"
        
        # The BacklogPerTask metric calculation:
        # BacklogPerTask = SQS_ApproximateNumberOfMessages / max(ECS_RunningTaskCount, 1)
        # 
        # This metric is calculated by:
        # 1. Lambda function triggered by SQS/ECS events, OR
        # 2. CloudWatch Logs metric filter, OR  
        # 3. Custom metric publisher
        
        logger.info(f"BacklogPerTask metric configured: {namespace}/{metric_name}")
        logger.info("Metric calculation: SQS_Messages / max(RunningTasks, 1)")
    
    def _create_scale_out_policy(self) -> str:
        """Create scale-out policy for increasing capacity."""
        policy_name = f"{self.config.service_name}-scale-out"
        
        try:
            response = self.autoscaling_client.put_scaling_policy(
                PolicyName=policy_name,
                ServiceNamespace=self.namespace,
                ResourceId=self.resource_id,
                ScalableDimension=self.scalable_dimension,
                PolicyType="StepScaling",
                StepScalingPolicyConfiguration={
                    'AdjustmentType': 'ExactCapacity',
                    'Cooldown': self.config.scale_out_cooldown,
                    'MetricAggregationType': 'Average',
                    'StepAdjustments': [
                        {
                            'MetricIntervalLowerBound': 0.0,
                            'MetricIntervalUpperBound': 1.0,
                            'ScalingAdjustment': 1  # 0 < BacklogPerTask < 1 → 1 task
                        },
                        {
                            'MetricIntervalLowerBound': 1.0,
                            'MetricIntervalUpperBound': 2.0, 
                            'ScalingAdjustment': 2  # 1 ≤ BacklogPerTask < 2 → 2 tasks
                        },
                        {
                            'MetricIntervalLowerBound': 2.0,
                            'ScalingAdjustment': 3  # BacklogPerTask ≥ 2 → 3 tasks (max)
                        }
                    ]
                }
            )
            
            policy_arn = response['PolicyARN']
            logger.info(f"Created scale-out policy: {policy_name}")
            return policy_arn
            
        except Exception as e:
            logger.error(f"Failed to create scale-out policy: {e}")
            raise
    
    def _create_scale_in_policy(self) -> str:
        """Create scale-in policy for decreasing capacity."""
        policy_name = f"{self.config.service_name}-scale-in"
        
        try:
            response = self.autoscaling_client.put_scaling_policy(
                PolicyName=policy_name,
                ServiceNamespace=self.namespace,
                ResourceId=self.resource_id,
                ScalableDimension=self.scalable_dimension,
                PolicyType="StepScaling",
                StepScalingPolicyConfiguration={
                    'AdjustmentType': 'ExactCapacity',
                    'Cooldown': self.config.scale_in_cooldown,
                    'MetricAggregationType': 'Average',
                    'StepAdjustments': [
                        {
                            'MetricIntervalUpperBound': 0.0,
                            'ScalingAdjustment': 0  # BacklogPerTask = 0 → Scale to zero
                        }
                    ]
                }
            )
            
            policy_arn = response['PolicyARN']
            logger.info(f"Created scale-in policy: {policy_name}")
            return policy_arn
            
        except Exception as e:
            logger.error(f"Failed to create scale-in policy: {e}")
            raise
    
    def _create_scale_out_alarm(self, policy_arn: str, queue_url: str) -> None:
        """Create CloudWatch alarm for scaling out."""
        alarm_name = f"{self.config.service_name}-scale-out-alarm"
        
        try:
            self.cloudwatch_client.put_metric_alarm(
                AlarmName=alarm_name,
                AlarmDescription=f"Scale out {self.config.service_name} when backlog per task > 0",
                ActionsEnabled=True,
                AlarmActions=[policy_arn],
                MetricName="BacklogPerTask",
                Namespace="ECS/VLM",
                Statistic="Average",
                Dimensions=[
                    {
                        'Name': 'ServiceName',
                        'Value': self.config.service_name
                    },
                    {
                        'Name': 'ClusterName', 
                        'Value': self.config.cluster_name
                    }
                ],
                Period=self.config.datapoint_period,
                EvaluationPeriods=self.config.evaluation_periods,
                Threshold=0.5,  # Trigger when BacklogPerTask > 0.5 (meaning there are messages)
                ComparisonOperator="GreaterThanThreshold",
                TreatMissingData="notBreaching"
            )
            
            logger.info(f"Created scale-out alarm: {alarm_name}")
            
        except Exception as e:
            logger.error(f"Failed to create scale-out alarm: {e}")
            raise
    
    def _create_scale_in_alarm(self, policy_arn: str, queue_url: str) -> None:
        """Create CloudWatch alarm for scaling in."""
        alarm_name = f"{self.config.service_name}-scale-in-alarm"
        
        try:
            self.cloudwatch_client.put_metric_alarm(
                AlarmName=alarm_name,
                AlarmDescription=f"Scale in {self.config.service_name} when backlog per task = 0",
                ActionsEnabled=True,
                AlarmActions=[policy_arn],
                MetricName="BacklogPerTask",
                Namespace="ECS/VLM", 
                Statistic="Average",
                Dimensions=[
                    {
                        'Name': 'ServiceName',
                        'Value': self.config.service_name
                    },
                    {
                        'Name': 'ClusterName',
                        'Value': self.config.cluster_name
                    }
                ],
                Period=self.config.datapoint_period,
                EvaluationPeriods=self.config.evaluation_periods,
                Threshold=0.1,  # Scale in when BacklogPerTask < 0.1 (effectively 0)
                ComparisonOperator="LessThanThreshold",
                TreatMissingData="breaching"  # Treat missing data as 0 (scale in)
            )
            
            logger.info(f"Created scale-in alarm: {alarm_name}")
            
        except Exception as e:
            logger.error(f"Failed to create scale-in alarm: {e}")
            raise
    
    def get_current_metrics(self, queue_url: str) -> Dict[str, Any]:
        """Get current scaling metrics for monitoring."""
        try:
            # Get SQS message count
            queue_attrs = self.sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['ApproximateNumberOfMessages']
            )
            message_count = int(queue_attrs['Attributes']['ApproximateNumberOfMessages'])
            
            # Get ECS service task count
            service_response = self.ecs_client.describe_services(
                cluster=self.config.cluster_name,
                services=[self.config.service_name]
            )
            
            if service_response['services']:
                service = service_response['services'][0]
                running_count = service['runningCount']
                desired_count = service['desiredCount']
            else:
                running_count = 0
                desired_count = 0
            
            # Calculate BacklogPerTask
            backlog_per_task = message_count / max(running_count, 1)
            
            metrics = {
                "timestamp": datetime.now().isoformat(),
                "queue_messages": message_count,
                "running_tasks": running_count,
                "desired_tasks": desired_count,
                "backlog_per_task": backlog_per_task,
                "service_name": self.config.service_name,
                "cluster_name": self.config.cluster_name
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to get current metrics: {e}")
            raise
    
    def publish_backlog_metric(self, queue_url: str) -> None:
        """Publish BacklogPerTask metric to CloudWatch."""
        try:
            metrics = self.get_current_metrics(queue_url)
            
            # Publish custom metric
            self.cloudwatch_client.put_metric_data(
                Namespace="ECS/VLM",
                MetricData=[
                    {
                        'MetricName': 'BacklogPerTask',
                        'Dimensions': [
                            {
                                'Name': 'ServiceName',
                                'Value': self.config.service_name
                            },
                            {
                                'Name': 'ClusterName',
                                'Value': self.config.cluster_name
                            }
                        ],
                        'Value': metrics['backlog_per_task'],
                        'Unit': 'Count',
                        'Timestamp': datetime.now()
                    }
                ]
            )
            
            logger.debug(f"Published BacklogPerTask metric: {metrics['backlog_per_task']}")
            
        except Exception as e:
            logger.error(f"Failed to publish backlog metric: {e}")
            raise
    
    def simulate_scaling_behavior(self, queue_url: str, duration_minutes: int = 30) -> List[Dict[str, Any]]:
        """Simulate and monitor scaling behavior for testing."""
        logger.info(f"Starting scaling behavior simulation for {duration_minutes} minutes")
        
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        simulation_events = []
        
        try:
            while datetime.now() < end_time:
                # Get current metrics
                current_metrics = self.get_current_metrics(queue_url)
                
                # Publish metric to trigger auto-scaling
                self.publish_backlog_metric(queue_url)
                
                # Log current state
                logger.info(f"Metrics: {current_metrics}")
                simulation_events.append(current_metrics)
                
                # Wait before next evaluation
                time.sleep(30)  # 30 second intervals
            
            logger.info("Scaling behavior simulation completed")
            return simulation_events
            
        except KeyboardInterrupt:
            logger.info("Scaling simulation interrupted by user")
            return simulation_events


class DockerComposeECSIntegration:
    """Integration layer for Docker Compose ECS services with auto-scaling."""
    
    def __init__(self, compose_file: str = "src/files_api/docker-compose.aws-prod.yml"):
        self.compose_file = Path(compose_file)
        self.settings = get_settings()
        
    def discover_compose_services(self) -> Dict[str, Any]:
        """Discover ECS-related services from Docker Compose file."""
        try:
            if not self.compose_file.exists():
                logger.warning(f"Compose file not found: {self.compose_file}")
                return {}
            
            # Parse compose file to find scalable services
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "config", "--services"],
                capture_output=True, text=True, check=True
            )
            
            services = result.stdout.strip().split('\n')
            scalable_services = {}
            
            for service in services:
                if 'worker' in service.lower() or 'vlm' in service.lower():
                    scalable_services[service] = {
                        "type": "worker",
                        "scalable": True,
                        "compose_file": str(self.compose_file)
                    }
                elif 'mongodb' in service.lower():
                    scalable_services[service] = {
                        "type": "database", 
                        "scalable": False,
                        "compose_file": str(self.compose_file)
                    }
            
            logger.info(f"Discovered {len(scalable_services)} services: {list(scalable_services.keys())}")
            return scalable_services
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to discover compose services: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error discovering compose services: {e}")
            return {}
    
    def get_compose_service_scale(self, service_name: str) -> int:
        """Get current scale (replicas) of a compose service."""
        try:
            result = subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "ps", "-q", service_name],
                capture_output=True, text=True, check=True
            )
            
            # Count running containers for the service
            containers = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
            scale = len(containers)
            
            logger.debug(f"Service {service_name} current scale: {scale}")
            return scale
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get service scale for {service_name}: {e}")
            return 0
        except Exception as e:
            logger.error(f"Error getting service scale: {e}")
            return 0
    
    def scale_compose_service(self, service_name: str, replicas: int) -> bool:
        """Scale a Docker Compose service to specified replicas."""
        try:
            current_scale = self.get_compose_service_scale(service_name)
            
            if current_scale == replicas:
                logger.info(f"Service {service_name} already at desired scale: {replicas}")
                return True
            
            logger.info(f"Scaling {service_name}: {current_scale} → {replicas}")
            
            # Scale the service
            subprocess.run(
                ["docker-compose", "-f", str(self.compose_file), "up", "-d", 
                 "--scale", f"{service_name}={replicas}", "--no-recreate"],
                check=True, capture_output=True
            )
            
            # Verify new scale
            new_scale = self.get_compose_service_scale(service_name)
            
            if new_scale == replicas:
                logger.info(f"✅ Successfully scaled {service_name} to {replicas}")
                return True
            else:
                logger.error(f"❌ Scale verification failed: expected {replicas}, got {new_scale}")
                return False
                
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to scale service {service_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error scaling service: {e}")
            return False
    
    def apply_ecs_scaling_to_compose(self, scaling_config: ECSScalingConfig, 
                                   queue_url: str) -> bool:
        """Apply ECS scaling logic to Docker Compose services."""
        try:
            # Get SQS queue metrics
            sqs_client = get_sqs_client()
            
            # Get approximate number of messages
            response = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['ApproximateNumberOfMessages']
            )
            
            queue_messages = int(response['Attributes']['ApproximateNumberOfMessages'])
            
            # Get current service scale
            current_scale = self.get_compose_service_scale(scaling_config.service_name)
            
            # Calculate desired capacity using ECS scaling logic
            if queue_messages == 0:
                # Scale to zero when no messages
                desired_capacity = scaling_config.min_capacity
                action = "scale_to_zero"
            elif queue_messages > 0:
                # Scale 1:1 with messages (up to max capacity)
                desired_capacity = min(queue_messages, scaling_config.max_capacity)
                action = "scale_out" if desired_capacity > current_scale else "maintain"
            else:
                desired_capacity = current_scale
                action = "no_action"
            
            logger.info(f"Scaling decision: {queue_messages} messages, "
                       f"{current_scale} current, {desired_capacity} desired ({action})")
            
            # Apply scaling if needed
            if desired_capacity != current_scale:
                success = self.scale_compose_service(scaling_config.service_name, desired_capacity)
                
                if success:
                    logger.info(f"🔄 Applied ECS scaling logic to compose service: "
                              f"{scaling_config.service_name} scaled to {desired_capacity}")
                return success
            else:
                logger.debug(f"No scaling needed for {scaling_config.service_name}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to apply ECS scaling to compose: {e}")
            return False
    
    def start_compose_scaling_monitor(self, scaling_config: ECSScalingConfig, 
                                    queue_url: str, interval_seconds: int = 60) -> None:
        """Start monitoring and scaling Docker Compose services based on ECS logic."""
        logger.info(f"Starting compose scaling monitor for {scaling_config.service_name}")
        logger.info(f"Monitoring interval: {interval_seconds}s")
        logger.info(f"Queue URL: {queue_url}")
        
        try:
            while True:
                self.apply_ecs_scaling_to_compose(scaling_config, queue_url)
                time.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logger.info("Compose scaling monitor stopped by user")
        except Exception as e:
            logger.error(f"Compose scaling monitor error: {e}")
            raise


def create_enhanced_autoscaler(cluster_name: str, service_name: str, 
                             queue_url: str, use_compose: bool = False) -> Any:
    """Create enhanced autoscaler with optional compose integration."""
    config = ECSScalingConfig(
        service_name=service_name,
        cluster_name=cluster_name,
        min_capacity=0,
        max_capacity=3,
        target_queue_messages_per_task=1
    )
    
    if use_compose:
        # Return compose integration
        compose_integration = DockerComposeECSIntegration()
        services = compose_integration.discover_compose_services()
        
        if service_name in services:
            logger.info(f"Using Docker Compose scaling for {service_name}")
            return compose_integration, config
        else:
            logger.warning(f"Service {service_name} not found in compose - falling back to ECS")
    
    # Return standard ECS autoscaler
    autoscaler = ECSAutoScaler(config)
    autoscaler.setup_auto_scaling(queue_url)
    return autoscaler, config


# CLI interface for compose integration
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ECS scaling with compose integration")
    parser.add_argument("--service-name", required=True, help="Service name to scale")
    parser.add_argument("--cluster-name", default="crud-pdf-ecs-cluster", help="ECS cluster name")
    parser.add_argument("--queue-url", required=True, help="SQS queue URL")
    parser.add_argument("--compose", action="store_true", help="Use Docker Compose scaling")
    parser.add_argument("--monitor", action="store_true", help="Start scaling monitor")
    parser.add_argument("--interval", type=int, default=60, help="Monitor interval in seconds")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    if args.compose:
        compose_integration = DockerComposeECSIntegration()
        
        if args.monitor:
            config = ECSScalingConfig(
                service_name=args.service_name,
                cluster_name=args.cluster_name
            )
            compose_integration.start_compose_scaling_monitor(
                config, args.queue_url, args.interval
            )
        else:
            # Single scaling operation
            config = ECSScalingConfig(
                service_name=args.service_name,
                cluster_name=args.cluster_name
            )
            compose_integration.apply_ecs_scaling_to_compose(config, args.queue_url)
    else:
        # Standard ECS scaling
        config = ECSScalingConfig(
            service_name=args.service_name,
            cluster_name=args.cluster_name
        )
        autoscaler = ECSAutoScaler(config)
        autoscaler.setup_auto_scaling(args.queue_url)
        
        if args.monitor:
            try:
                autoscaler.simulate_scaling_behavior(args.queue_url, duration_minutes=60)
            except Exception as e:
                logger.error(f"Scaling simulation failed: {e}")
                raise
