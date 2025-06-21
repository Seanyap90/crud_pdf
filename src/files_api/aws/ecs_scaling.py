"""
ECS Auto-scaling with CloudWatch Metrics
Implements native AWS auto-scaling for ECS services with scale-to-zero capability.
"""
import logging
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

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
    max_capacity: int = 3  # Max 3 GPU instances
    target_queue_messages_per_task: int = 1  # 1 message = 1 task
    scale_out_cooldown: int = 300  # 5 minutes
    scale_in_cooldown: int = 300   # 5 minutes
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
        except Exception as e:
            logger.error(f"Scaling simulation failed: {e}")
            raise
    
    def cleanup_auto_scaling(self) -> None:
        """Clean up auto-scaling resources."""
        try:
            # Delete CloudWatch alarms
            alarm_names = [
                f"{self.config.service_name}-scale-out-alarm",
                f"{self.config.service_name}-scale-in-alarm"
            ]
            
            for alarm_name in alarm_names:
                try:
                    self.cloudwatch_client.delete_alarms(AlarmNames=[alarm_name])
                    logger.info(f"Deleted alarm: {alarm_name}")
                except Exception as e:
                    logger.warning(f"Failed to delete alarm {alarm_name}: {e}")
            
            # Delete scaling policies
            policy_names = [
                f"{self.config.service_name}-scale-out",
                f"{self.config.service_name}-scale-in"
            ]
            
            for policy_name in policy_names:
                try:
                    self.autoscaling_client.delete_scaling_policy(
                        PolicyName=policy_name,
                        ServiceNamespace=self.namespace,
                        ResourceId=self.resource_id,
                        ScalableDimension=self.scalable_dimension
                    )
                    logger.info(f"Deleted scaling policy: {policy_name}")
                except Exception as e:
                    logger.warning(f"Failed to delete policy {policy_name}: {e}")
            
            # Deregister scalable target
            try:
                self.autoscaling_client.deregister_scalable_target(
                    ServiceNamespace=self.namespace,
                    ResourceId=self.resource_id,
                    ScalableDimension=self.scalable_dimension
                )
                logger.info(f"Deregistered scalable target: {self.resource_id}")
            except Exception as e:
                logger.warning(f"Failed to deregister scalable target: {e}")
                
        except Exception as e:
            logger.error(f"Error during auto-scaling cleanup: {e}")
            raise


def create_vlm_auto_scaler(cluster_name: str, service_name: str) -> ECSAutoScaler:
    """Factory function to create ECS auto-scaler for VLM workers."""
    config = ECSScalingConfig(
        service_name=service_name,
        cluster_name=cluster_name,
        min_capacity=0,  # Scale to zero
        max_capacity=3,  # Max 3 GPU instances
        target_queue_messages_per_task=1,  # 1 message = 1 task
        scale_out_cooldown=300,  # 5 minutes
        scale_in_cooldown=300,   # 5 minutes
        evaluation_periods=2,    # 2 consecutive periods
        datapoint_period=60      # 1 minute periods
    )
    
    return ECSAutoScaler(config)


# CLI entry point for testing
def main():
    """Main entry point for testing auto-scaling."""
    import argparse
    
    parser = argparse.ArgumentParser(description="ECS Auto-scaling Manager")
    parser.add_argument("--cluster", required=True, help="ECS cluster name")
    parser.add_argument("--service", required=True, help="ECS service name") 
    parser.add_argument("--queue-url", required=True, help="SQS queue URL")
    parser.add_argument("--setup", action="store_true", help="Setup auto-scaling")
    parser.add_argument("--simulate", type=int, metavar="MINUTES", help="Simulate scaling for N minutes")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup auto-scaling")
    
    args = parser.parse_args()
    
    # Create auto-scaler
    scaler = create_vlm_auto_scaler(args.cluster, args.service)
    
    try:
        if args.setup:
            result = scaler.setup_auto_scaling(args.queue_url)
            print(json.dumps(result, indent=2))
            
        elif args.simulate:
            events = scaler.simulate_scaling_behavior(args.queue_url, args.simulate)
            print(json.dumps(events, indent=2))
            
        elif args.cleanup:
            scaler.cleanup_auto_scaling()
            print("Auto-scaling resources cleaned up")
            
        else:
            # Just show current metrics
            metrics = scaler.get_current_metrics(args.queue_url)
            print(json.dumps(metrics, indent=2))
            
    except Exception as e:
        logger.error(f"Auto-scaling operation failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())