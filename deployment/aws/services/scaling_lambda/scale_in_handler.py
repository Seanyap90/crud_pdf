"""
Scale-In Handler based on ref_code.py pattern
Scales down when no messages (visible + in-flight = 0) are being processed
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS clients
sqs = boto3.client('sqs')
ecs = boto3.client('ecs')
asg = boto3.client('autoscaling')
ec2 = boto3.client('ec2')

def get_queue_depth(queue_url):
    """Get total message count: visible + in-flight (being processed by tasks)"""
    response = sqs.get_queue_attributes(
        QueueUrl=queue_url, 
        AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
    )
    
    visible = int(response['Attributes']['ApproximateNumberOfMessages'])
    in_flight = int(response['Attributes']['ApproximateNumberOfMessagesNotVisible'])
    total = visible + in_flight
    
    logger.info(f"📬 SQS State: {visible} visible + {in_flight} in-flight = {total} total messages")
    return total, visible, in_flight

def get_running_task_count(cluster):
    """Count running ECS tasks in cluster"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    task_count = len(response.get('taskArns', []))
    
    # Also get task details for better visibility
    if task_count > 0:
        task_arns = response.get('taskArns', [])
        try:
            describe_response = ecs.describe_tasks(cluster=cluster, tasks=task_arns)
            running_tasks = []
            for task in describe_response.get('tasks', []):
                running_tasks.append({
                    'taskArn': task['taskArn'],
                    'lastStatus': task.get('lastStatus'),
                    'desiredStatus': task.get('desiredStatus'),
                    'createdAt': task.get('createdAt')
                })
            logger.info(f"🏃 {task_count} running tasks found")
            return task_count, running_tasks
        except Exception as e:
            logger.warning(f"Could not get task details: {e}")
    
    return task_count, []

def get_asg_instances(asg_name):
    """Get ASG instances and their states"""
    try:
        response = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        if not response['AutoScalingGroups']:
            return [], 0, 0
        
        asg_group = response['AutoScalingGroups'][0]
        desired = asg_group['DesiredCapacity']
        instances = asg_group.get('Instances', [])
        active = len([i for i in instances if i['LifecycleState'] == 'InService'])
        
        instance_ids = [inst['InstanceId'] for inst in instances]
        
        if not instance_ids:
            return [], desired, active
        
        # Get detailed EC2 instance states
        ec2_response = ec2.describe_instances(InstanceIds=instance_ids)
        detailed_instances = []
        
        for reservation in ec2_response['Reservations']:
            for instance in reservation['Instances']:
                detailed_instances.append({
                    'instance_id': instance['InstanceId'],
                    'state': instance['State']['Name'],
                    'private_ip': instance.get('PrivateIpAddress')
                })
        
        return detailed_instances, desired, active
        
    except Exception as e:
        logger.error(f"Error getting ASG instances: {e}")
        return [], 0, 0

def stop_all_tasks(cluster):
    """Stop all running tasks in cluster"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    task_arns = response.get('taskArns', [])
    
    if not task_arns:
        logger.info("No running tasks to stop")
        return 0
    
    stopped_count = 0
    for task_arn in task_arns:
        try:
            ecs.stop_task(
                cluster=cluster, 
                task=task_arn, 
                reason='Scaling down - no messages in queue'
            )
            stopped_count += 1
            logger.info(f"Stopped task: {task_arn}")
        except Exception as e:
            logger.error(f"Error stopping task {task_arn}: {e}")
    
    return stopped_count

def stop_asg_instances(asg_name, instance_ids):
    """Stop running instances in ASG (but keep desired capacity)"""
    if not instance_ids:
        return {'stopped': [], 'errors': []}
    
    try:
        logger.info(f"Stopping ASG instances: {instance_ids}")
        response = ec2.stop_instances(InstanceIds=instance_ids)
        
        stopped = []
        for instance in response.get('StoppingInstances', []):
            stopped.append({
                'instance_id': instance['InstanceId'],
                'previous_state': instance['PreviousState']['Name'],
                'current_state': instance['CurrentState']['Name']
            })
        
        return {'stopped': stopped, 'errors': []}
        
    except Exception as e:
        logger.error(f"Error stopping instances: {e}")
        return {'stopped': [], 'errors': [str(e)]}

def determine_scale_in_safety(total_messages, visible, in_flight, running_tasks):
    """
    Determine if it's safe to scale in based on queue state and running tasks.
    
    Returns:
    - can_scale_in: bool
    - reason: str  
    - action: str
    """
    
    logger.info(f"🔍 Scale-in Analysis:")
    logger.info(f"   📨 Queue: {visible} visible + {in_flight} in-flight = {total_messages} total")
    logger.info(f"   🏃 Running tasks: {running_tasks}")
    
    # Most accurate approach: Check both queue and task states
    if total_messages == 0 and running_tasks == 0:
        return True, "No messages in queue and no tasks running", "scale_down"
    elif total_messages == 0 and running_tasks > 0:
        return False, f"No messages but {running_tasks} tasks still running - wait for completion", "wait"
    elif total_messages > 0:
        return False, f"{total_messages} messages still in queue (visible: {visible}, in-flight: {in_flight})", "wait"
    else:
        return False, "Unknown state", "wait"

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Scale-in handler: Scale down when no processing is happening
    
    Scale-in conditions:
    1. No visible messages in SQS (no new work)
    2. No in-flight messages in SQS (no active processing)  
    3. No running ECS tasks (no ongoing work)
    """
    
    logger.info("=== Scale-In Handler Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Environment variables (from ref_code.py pattern)
        queue_url = os.environ['SQS_QUEUE_URL']
        cluster_name = os.environ['ECS_CLUSTER_NAME']
        asg_name = os.environ['ASG_NAME']
        
        logger.info(f"Config: Queue={queue_url}, Cluster={cluster_name}, ASG={asg_name}")
        
        # Step 1: Get current state
        total_messages, visible, in_flight = get_queue_depth(queue_url)
        running_tasks, task_details = get_running_task_count(cluster_name)
        instances, asg_desired, asg_active = get_asg_instances(asg_name)
        
        logger.info(f"📊 State: {total_messages} messages | {running_tasks} tasks | ASG: {asg_active}/{asg_desired}")
        
        # Step 2: Determine if scale-in is safe
        can_scale_in, reason, action = determine_scale_in_safety(
            total_messages, visible, in_flight, running_tasks
        )
        
        logger.info(f"🎯 Scale-in decision: {action} - {reason}")
        
        # Step 3: Execute scale-in if safe
        result_msg = reason
        
        if action == "scale_down" and can_scale_in:
            logger.info("🔽 Initiating scale-down...")
            
            # Stop ECS tasks first
            if running_tasks > 0:
                stopped_tasks = stop_all_tasks(cluster_name)
                logger.info(f"Stopped {stopped_tasks} ECS tasks")
            
            # Stop ASG instances (but keep desired capacity at 2 for fast restart)
            running_instances = [inst['instance_id'] for inst in instances if inst['state'] == 'running']
            if running_instances:
                logger.info(f"Stopping {len(running_instances)} running instances")
                stop_result = stop_asg_instances(asg_name, running_instances)
                result_msg = f"Scaled down: stopped {len(stop_result['stopped'])} instances, kept desired=2"
            else:
                result_msg = "No running instances to stop"
                
        elif action == "wait":
            result_msg = f"Scale-in deferred: {reason}"
        
        response = {
            'statusCode': 200,
            'body': json.dumps({
                'timestamp': datetime.utcnow().isoformat(),
                'total_messages': total_messages,
                'visible_messages': visible,
                'in_flight_messages': in_flight,
                'running_tasks': running_tasks,
                'asg_desired': asg_desired,
                'asg_active': asg_active,
                'can_scale_in': can_scale_in,
                'action': action,
                'result': result_msg
            })
        }
        
        logger.info(f"✅ Scale-in completed: {result_msg}")
        return response
        
    except Exception as e:
        logger.error(f"❌ Scale-in failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
        }