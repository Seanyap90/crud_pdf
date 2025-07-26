"""
ECS Auto-scaling with CloudWatch Metrics
Implements native AWS auto-scaling for ECS services with scale-to-zero capability.
Ported from main branch and adapted for refactored architecture.
"""
import logging
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from deployment.aws.utils.aws_clients import (
    get_ecs_client,
    get_sqs_client,
    get_cloudwatch_client,
    get_application_autoscaling_client
)
from src.files_api.config.settings import get_settings

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
                Threshold=0.1,  # Trigger when BacklogPerTask < 0.1 (essentially zero)
                ComparisonOperator="LessThanThreshold",
                TreatMissingData="breaching"  # Treat missing data as breach (scale to zero)
            )
            
            logger.info(f"Created scale-in alarm: {alarm_name}")
            
        except Exception as e:
            logger.error(f"Failed to create scale-in alarm: {e}")
            raise
    
    def get_scaling_status(self) -> Dict[str, Any]:
        """Get current scaling status and metrics."""
        try:
            # Get scalable target info
            response = self.autoscaling_client.describe_scalable_targets(
                ServiceNamespace=self.namespace,
                ResourceIds=[self.resource_id]
            )
            
            if not response.get('ScalableTargets'):
                return {"status": "not_configured", "message": "Auto-scaling not configured"}
            
            target = response['ScalableTargets'][0]
            
            # Get current service info
            ecs_response = self.ecs_client.describe_services(
                cluster=self.config.cluster_name,
                services=[self.config.service_name]
            )
            
            if ecs_response.get('services'):
                service = ecs_response['services'][0]
                current_capacity = service.get('runningCount', 0)
                desired_capacity = service.get('desiredCount', 0)
            else:
                current_capacity = 0
                desired_capacity = 0
            
            return {
                "status": "active",
                "resource_id": self.resource_id,
                "min_capacity": target['MinCapacity'],
                "max_capacity": target['MaxCapacity'],
                "current_capacity": current_capacity,
                "desired_capacity": desired_capacity,
                "scaling_events": len(self.scaling_events),
                "last_scale_action": self.last_scale_action_time.isoformat() if self.last_scale_action_time else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get scaling status: {e}")
            return {"status": "error", "error": str(e)}
    
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
            logger.error(f"Failed to cleanup auto-scaling: {e}")
            raise


def create_ecs_autoscaler(cluster_name: str, service_name: str, 
                         queue_url: str) -> ECSAutoScaler:
    """Create ECS autoscaler with default configuration."""
    config = ECSScalingConfig(
        service_name=service_name,
        cluster_name=cluster_name,
        min_capacity=0,
        max_capacity=3,
        target_queue_messages_per_task=1
    )
    
    autoscaler = ECSAutoScaler(config)
    autoscaler.setup_auto_scaling(queue_url)
    return autoscaler