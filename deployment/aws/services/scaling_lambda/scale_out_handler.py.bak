"""
Scale-Out Handler based on ref_code.py pattern
Launches ECS tasks according to SQS message count and starts ASG instances as capacity provider
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
    return total

def get_asg_state(asg_name):
    """Get ASG desired capacity, active instances, and max capacity"""
    response = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    if not response['AutoScalingGroups']:
        raise Exception(f"ASG {asg_name} not found")
    
    group = response['AutoScalingGroups'][0]
    desired = group['DesiredCapacity']
    active = len([i for i in group['Instances'] if i['LifecycleState'] == 'InService'])
    max_capacity = group['MaxSize']
    
    return desired, active, max_capacity

def get_running_task_count(cluster):
    """Count running ECS tasks in cluster"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    return len(response.get('taskArns', []))

def determine_scaling(total_messages, running_tasks, asg_active, max_instances):
    """
    Simplified scaling logic for AMI deployment:
    - 1:1 message-to-task ratio  
    - ASG instances as capacity provider (no warm pool)
    - Scale instances up to max capacity
    """
    
    if total_messages == 0:
        logger.info("💭 No messages in queue - no scaling needed")
        return 0, 0, "no_action"
    else:
        # Scale tasks to match messages (up to max instance limit)
        needed_tasks = min(total_messages, max_instances)
        needed_instances = min(total_messages, max_instances)
        
        logger.info(f"💭 Scaling Analysis:")
        logger.info(f"   📨 Total messages: {total_messages}")
        logger.info(f"   🏃 Currently running tasks: {running_tasks}")
        logger.info(f"   🖥️ Max instances: {max_instances}")
        logger.info(f"   🎯 Target tasks: {needed_tasks}")
        logger.info(f"   📊 Target instances: {needed_instances}")
        
        return needed_tasks, needed_instances, "scale_up"

def start_asg_instances(asg_name, instance_ids):
    """Start stopped instances in ASG"""
    if not instance_ids:
        return {'started': [], 'errors': []}
    
    try:
        logger.info(f"Starting ASG instances: {instance_ids}")
        response = ec2.start_instances(InstanceIds=instance_ids)
        
        started = []
        for instance in response.get('StartingInstances', []):
            started.append({
                'instance_id': instance['InstanceId'],
                'previous_state': instance['PreviousState']['Name'],
                'current_state': instance['CurrentState']['Name']
            })
        
        return {'started': started, 'errors': []}
        
    except Exception as e:
        logger.error(f"Error starting instances: {e}")
        return {'started': [], 'errors': [str(e)]}

def wait_for_container_instances(cluster, max_wait=60):
    """Wait for container instances to register with ECS cluster"""
    logger.info(f"⏳ Checking for container instances (max {max_wait}s)...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            response = ecs.list_container_instances(cluster=cluster)
            instance_count = len(response.get('containerInstanceArns', []))
            
            if instance_count > 0:
                elapsed = int(time.time() - start_time)
                logger.info(f"✅ Found {instance_count} container instances in {elapsed}s")
                return True
            
            logger.info("⏳ No container instances yet, waiting...")
            time.sleep(10)
            
        except Exception as e:
            logger.warning(f"⚠️ Error checking container instances: {e}")
            time.sleep(10)
    
    logger.warning(f"⏰ Timeout after {max_wait}s - no container instances found")
    return False

def launch_tasks(cluster, task_def, count):
    """Launch ECS tasks with 1:1 instance constraint - they'll queue in PENDING state until instances ready"""
    if count <= 0:
        return {"launched": 0, "failures": 0, "success": True}
    
    try:
        logger.info(f"🚀 Launching {count} ECS tasks with distinctInstance constraint (1:1 ratio)")
        response = ecs.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            count=count,
            launchType='EC2',
            placementConstraints=[
                {
                    'type': 'distinctInstance'  # Ensure 1:1 task-to-instance ratio
                }
            ]
        )
        
        launched = len(response.get('tasks', []))
        failures = response.get('failures', [])
        failure_count = len(failures)
        
        logger.info(f"✅ ECS Response: {launched} tasks launched, {failure_count} failures")
        
        if failures:
            for failure in failures:
                reason = failure.get('reason', 'Unknown')
                logger.error(f"❌ Task launch failure: {reason}")
        
        return {
            "launched": launched,
            "failures": failure_count, 
            "success": launched > 0,
            "failure_reasons": [f.get('reason', 'Unknown') for f in failures]
        }
        
    except Exception as e:
        logger.error(f"❌ RunTask error: {e}")
        return {"launched": 0, "failures": count, "success": False, "error": str(e)}

def get_asg_instances(asg_name):
    """Get stopped instances in ASG that can be started"""
    try:
        response = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        if not response['AutoScalingGroups']:
            return []
        
        asg_group = response['AutoScalingGroups'][0]
        instance_ids = [inst['InstanceId'] for inst in asg_group.get('Instances', [])]
        
        if not instance_ids:
            return []
        
        # Get EC2 instance states
        ec2_response = ec2.describe_instances(InstanceIds=instance_ids)
        stopped_instances = []
        
        for reservation in ec2_response['Reservations']:
            for instance in reservation['Instances']:
                if instance['State']['Name'] == 'stopped':
                    stopped_instances.append(instance['InstanceId'])
        
        return stopped_instances
        
    except Exception as e:
        logger.error(f"Error getting ASG instances: {e}")
        return []

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Scale-out handler: Launch ECS tasks and start ASG instances as capacity provider"""
    
    logger.info("=== Scale-Out Handler Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Environment variables (from ref_code.py pattern)
        queue_url = os.environ['SQS_QUEUE_URL']
        cluster_name = os.environ['ECS_CLUSTER_NAME']
        task_def = os.environ['ECS_TASK_DEF']
        asg_name = os.environ['ASG_NAME']
        
        logger.info(f"Config: Queue={queue_url}, Cluster={cluster_name}, ASG={asg_name}")
        
        # Step 1: Get current state
        total_messages = get_queue_depth(queue_url)
        asg_desired, asg_active, max_instances = get_asg_state(asg_name)
        running_tasks = get_running_task_count(cluster_name)
        
        logger.info(f"📊 State: {total_messages} messages | {running_tasks} tasks | ASG: {asg_active}/{asg_desired} (max: {max_instances})")
        
        # Step 2: Determine scaling targets (simplified - no warm pool)
        target_tasks, target_instances, action = determine_scaling(
            total_messages, running_tasks, asg_active, max_instances
        )
        
        logger.info(f"🎯 Target: {target_tasks} tasks, {target_instances} instances | Action: {action}")
        
        # Step 3: Execute scaling
        if action == "no_action":
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'action': 'no_action',
                    'message': 'No messages in queue',
                    'timestamp': datetime.utcnow().isoformat()
                })
            }
        
        elif action == "scale_up":
            # Start stopped ASG instances first
            stopped_instances = get_asg_instances(asg_name)
            
            if stopped_instances:
                logger.info(f"Starting {len(stopped_instances)} stopped instances")
                start_result = start_asg_instances(asg_name, stopped_instances)
            else:
                # Update ASG desired capacity to create new instances
                logger.info(f"No stopped instances, updating ASG desired capacity to {target_instances}")
                asg.set_desired_capacity(
                    AutoScalingGroupName=asg_name, 
                    DesiredCapacity=target_instances, 
                    HonorCooldown=False
                )
            
            # Launch ECS tasks (may go to PENDING until instances ready)
            tasks_to_launch = target_tasks - running_tasks
            if tasks_to_launch > 0:
                logger.info(f"🚀 Launching {tasks_to_launch} ECS tasks...")
                
                # Brief wait for container instances
                if wait_for_container_instances(cluster_name, max_wait=60):
                    launch_result = launch_tasks(cluster_name, task_def, tasks_to_launch)
                    result_msg = f"scaled_to_{target_instances}_launched_{launch_result['launched']}_tasks"
                else:
                    result_msg = f"scaled_to_{target_instances}_tasks_deferred"
            else:
                result_msg = f"scaled_to_{target_instances}_no_new_tasks_needed"
        
        response = {
            'statusCode': 200,
            'body': json.dumps({
                'timestamp': datetime.utcnow().isoformat(),
                'total_messages': total_messages,
                'running_tasks': running_tasks,
                'target_tasks': target_tasks,
                'target_instances': target_instances,
                'action': action,
                'result': result_msg
            })
        }
        
        logger.info(f"✅ Scale-out completed: {result_msg}")
        return response
        
    except Exception as e:
        logger.error(f"❌ Scale-out failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
        }