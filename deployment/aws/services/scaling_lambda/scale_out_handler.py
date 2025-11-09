"""
Scale-Out Handler for Long-Running VLM-RAG Workers
Maintains persistent worker pool that continuously polls SQS queue
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any, Tuple

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

def get_queue_depth(queue_url: str) -> Tuple[int, int, int]:
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

def get_asg_state(asg_name: str) -> Tuple[int, int, int]:
    """Get ASG desired capacity, active instances, and max capacity"""
    response = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    if not response['AutoScalingGroups']:
        raise Exception(f"ASG {asg_name} not found")

    group = response['AutoScalingGroups'][0]
    desired = group['DesiredCapacity']
    active = len([i for i in group['Instances'] if i['LifecycleState'] == 'InService'])
    max_capacity = group['MaxSize']

    return desired, active, max_capacity

def get_running_task_count(cluster: str) -> int:
    """Count running ECS tasks in cluster"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    return len(response.get('taskArns', []))

def determine_scaling(total_messages: int, visible: int, in_flight: int,
                     running_tasks: int, max_instances: int) -> Tuple[int, int, str]:
    """
    Long-running worker scaling logic:
    - Workers are daemon processes that continuously poll SQS
    - Each worker can process messages sequentially
    - Scale based on workload, not 1:1 message-to-task ratio

    Scaling strategy:
    - If no workers running: Start 1 worker
    - If messages > workers: Scale up to handle concurrency
    - If sufficient workers exist: No action

    Returns:
        (target_tasks, target_instances, action)
    """

    logger.info(f"💭 Scaling Analysis:")
    logger.info(f"   📨 Messages: {visible} visible + {in_flight} in-flight = {total_messages} total")
    logger.info(f"   🏃 Currently running workers: {running_tasks}")
    logger.info(f"   🖥️ Max instances allowed: {max_instances}")

    # No messages and no workers - no action needed
    if total_messages == 0 and running_tasks == 0:
        logger.info("   ✅ No messages and no workers - system idle")
        return 0, 0, "no_action"

    # No messages but workers running - workers are idle listeners (OK state)
    if total_messages == 0 and running_tasks > 0:
        logger.info(f"   ✅ No messages but {running_tasks} workers listening - system ready")
        return running_tasks, running_tasks, "no_action"

    # Messages exist
    if total_messages > 0:
        # No workers running - start workers based on message count
        if running_tasks == 0:
            needed_workers = min(total_messages, max_instances)  # Start workers based on message count
            logger.info(f"   🚀 No workers running - starting {needed_workers} worker(s)")
            return needed_workers, needed_workers, "scale_up"

        # Workers exist - check if we need more for concurrency
        # Scale up if messages significantly exceed workers (backlog building)
        # This allows one worker to handle a few messages sequentially
        if total_messages > running_tasks:
            # Only scale up if backlog is significant (more than 2x workers)
            if total_messages > running_tasks * 2:
                needed_workers = min(total_messages, max_instances)
                logger.info(f"   🚀 High backlog detected ({total_messages} messages > {running_tasks * 2}) - scaling to {needed_workers} workers")
                return needed_workers, needed_workers, "scale_up"
            else:
                logger.info(f"   ✅ Current {running_tasks} worker(s) can handle {total_messages} messages")
                return running_tasks, running_tasks, "no_action"
        else:
            # Workers >= messages - sufficient capacity
            logger.info(f"   ✅ Sufficient workers: {running_tasks} worker(s) for {total_messages} message(s)")
            return running_tasks, running_tasks, "no_action"

    # Default - no action
    return running_tasks, running_tasks, "no_action"

def start_asg_instances(asg_name: str, instance_ids: list) -> Dict[str, Any]:
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

def wait_for_container_instances(cluster: str, max_wait: int = 60) -> bool:
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

def launch_tasks(cluster: str, task_def: str, count: int) -> Dict[str, Any]:
    """Launch ECS tasks with 1:1 instance constraint"""
    if count <= 0:
        return {"launched": 0, "failures": 0, "success": True}

    try:
        logger.info(f"🚀 Launching {count} long-running worker task(s) with distinctInstance constraint")
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

def get_asg_instances(asg_name: str) -> list:
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
    """
    Scale-out handler for long-running VLM-RAG workers

    Strategy:
    - Maintain pool of persistent workers that poll SQS
    - Scale based on workload backlog, not 1:1 message ratio
    - Workers process messages sequentially from queue
    """

    logger.info("=== Scale-Out Handler Started (Long-Running Workers) ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Environment variables
        queue_url = os.environ['SQS_QUEUE_URL']
        cluster_name = os.environ['ECS_CLUSTER_NAME']
        task_def = os.environ['ECS_TASK_DEF']
        asg_name = os.environ['ASG_NAME']

        logger.info(f"Config: Queue={queue_url}, Cluster={cluster_name}, ASG={asg_name}")

        # Step 1: Get current state
        total_messages, visible, in_flight = get_queue_depth(queue_url)
        asg_desired, asg_active, max_instances = get_asg_state(asg_name)
        running_tasks = get_running_task_count(cluster_name)

        logger.info(f"📊 Current State:")
        logger.info(f"   Messages: {total_messages} | Workers: {running_tasks} | ASG: {asg_active}/{asg_desired} (max: {max_instances})")

        # Step 2: Determine scaling targets
        target_tasks, target_instances, action = determine_scaling(
            total_messages, visible, in_flight, running_tasks, max_instances
        )

        logger.info(f"🎯 Target: {target_tasks} tasks, {target_instances} instances | Action: {action}")

        # Step 3: Execute scaling
        if action == "no_action":
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'action': 'no_action',
                    'message': 'Workers already at optimal level',
                    'total_messages': total_messages,
                    'running_workers': running_tasks,
                    'timestamp': datetime.utcnow().isoformat()
                })
            }

        elif action == "scale_up":
            # Calculate how many NEW tasks to launch
            tasks_to_launch = target_tasks - running_tasks

            if tasks_to_launch <= 0:
                logger.info("✅ No new tasks needed")
                result_msg = "no_new_tasks_needed"
            else:
                # Start stopped ASG instances first
                stopped_instances = get_asg_instances(asg_name)

                if stopped_instances:
                    instances_to_start = stopped_instances[:target_instances]
                    logger.info(f"Starting {len(instances_to_start)} stopped instances")
                    start_result = start_asg_instances(asg_name, instances_to_start)
                else:
                    # Update ASG desired capacity to create new instances
                    logger.info(f"No stopped instances, updating ASG desired capacity to {target_instances}")
                    asg.set_desired_capacity(
                        AutoScalingGroupName=asg_name,
                        DesiredCapacity=target_instances,
                        HonorCooldown=False
                    )

                # Launch NEW ECS tasks (only the delta)
                logger.info(f"🚀 Launching {tasks_to_launch} NEW worker task(s)...")

                # Wait for container instances
                if wait_for_container_instances(cluster_name, max_wait=60):
                    launch_result = launch_tasks(cluster_name, task_def, tasks_to_launch)
                    result_msg = f"scaled_up_launched_{launch_result['launched']}_new_workers"
                else:
                    result_msg = f"scaled_to_{target_instances}_tasks_deferred"

        response = {
            'statusCode': 200,
            'body': json.dumps({
                'timestamp': datetime.utcnow().isoformat(),
                'total_messages': total_messages,
                'visible_messages': visible,
                'in_flight_messages': in_flight,
                'running_workers': running_tasks,
                'target_workers': target_tasks,
                'target_instances': target_instances,
                'action': action,
                'result': result_msg
            })
        }

        logger.info(f"✅ Scale-out completed: {result_msg}")
        return response

    except Exception as e:
        logger.error(f"❌ Scale-out failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
        }
