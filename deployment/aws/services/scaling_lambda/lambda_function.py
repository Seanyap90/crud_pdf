import boto3
import json
import time
import os
from datetime import datetime

# Initialize AWS clients
sqs = boto3.client('sqs')
ecs = boto3.client('ecs') 
asg = boto3.client('autoscaling')
cloudwatch = boto3.client('cloudwatch')

def lambda_handler(event, context):
    """
    ECS-SQS Auto Scaler - Simple Async Version
    
    Key Features:
    - 1:1 message-to-instance ratio
    - ASG scaling and ECS task launch are independent/async
    - ECS tasks automatically wait in PENDING for capacity
    - Considers both visible + in-flight SQS messages
    - Gets max instances dynamically from ASG MaxSize
    """
    
    print(f"=== Auto Scaler Started at {datetime.utcnow()} ===")
    
    # Environment variables
    QUEUE_URL = os.environ['SQS_QUEUE_URL']
    CLUSTER_NAME = os.environ['ECS_CLUSTER_NAME']
    TASK_DEF = os.environ['ECS_TASK_DEF']
    ASG_NAME = os.environ['ASG_NAME']
    
    try:
        # Step 1: Get current state
        total_messages = get_queue_depth(QUEUE_URL)
        asg_desired, asg_active, max_instances = get_asg_state(ASG_NAME)
        warm_pool_count = get_warm_pool_count(ASG_NAME)
        running_tasks = get_running_task_count(CLUSTER_NAME)
        
        print(f"📊 Total Messages: {total_messages} | Tasks: {running_tasks} | ASG: {asg_active}/{asg_desired} (max: {max_instances}) | Warm: {warm_pool_count}")
        
        # Step 2: Calculate targets and action
        target_tasks, target_asg, action = determine_scaling(
            total_messages, running_tasks, asg_active, warm_pool_count, max_instances
        )
        
        print(f"🎯 Target: {target_tasks} tasks, {target_asg} instances | Action: {action}")
        
        # Step 3: Execute action
        result = execute_action(action, target_tasks, target_asg, running_tasks, 
                               CLUSTER_NAME, TASK_DEF, ASG_NAME)
        
        response = {
            'timestamp': datetime.utcnow().isoformat(),
            'total_messages': total_messages,
            'running_tasks': running_tasks,
            'target_tasks': target_tasks,
            'target_asg': target_asg,
            'action': action,
            'result': result
        }
        
        print(f"✅ Complete: {result}")
        return {'statusCode': 200, 'body': json.dumps(response)}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}

# --- Core Functions ---

def get_queue_depth(queue_url):
    """Get total message count: visible + in-flight (being processed by tasks)"""
    response = sqs.get_queue_attributes(
        QueueUrl=queue_url, 
        AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
    )
    
    visible = int(response['Attributes']['ApproximateNumberOfMessages'])
    in_flight = int(response['Attributes']['ApproximateNumberOfMessagesNotVisible'])
    total = visible + in_flight
    
    print(f"📬 SQS State: {visible} visible + {in_flight} in-flight = {total} total messages")
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

def get_warm_pool_count(asg_name):
    """Get number of instances in warm pool"""
    try:
        response = asg.describe_warm_pool(AutoScalingGroupName=asg_name)
        return len(response.get('Instances', []))
    except:
        return 0

def get_running_task_count(cluster):
    """Count running ECS tasks in cluster"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    return len(response.get('taskArns', []))

def determine_scaling(total_messages, running_tasks, asg_active, warm_pool, max_instances):
    """
    Simple 1:1 instance-to-task scaling with edge case handling:
    - Instances: Scale to min(total_messages, asg_max_capacity)  
    - Tasks: Launch only what instances can handle (considering currently running tasks)
    - max_instances comes directly from ASG MaxSize configuration
    """
    
    if total_messages == 0:
        print("💭 No messages in queue - scaling down everything")
        return 0, 0, "scale_down"
    else:
        # Scale instances up to max capacity
        needed_instances = min(total_messages, max_instances)
        
        # Calculate available task slots (1:1 instance-to-task ratio)
        # If instances are busy running tasks, we can't launch more
        available_instance_slots = max(0, needed_instances - running_tasks)
        
        # Target tasks = what we can actually run now
        target_tasks = min(total_messages, needed_instances)
        
        # Calculate excess messages and busy instance scenario
        excess_messages = max(0, total_messages - target_tasks)
        
        # Enhanced logging for edge cases
        print(f"💭 Scaling Analysis:")
        print(f"   📨 Total messages: {total_messages}")
        print(f"   🏃 Currently running tasks: {running_tasks}")
        print(f"   🖥️ Max instances: {max_instances}")
        print(f"   📊 Needed instances: {needed_instances}")
        print(f"   🎯 Target tasks: {target_tasks}")
        
        if excess_messages > 0:
            print(f"   ⏳ {excess_messages} messages will remain in SQS (exceeds instance capacity)")
        
        if running_tasks > 0 and total_messages > running_tasks:
            print(f"   🔄 Edge case: {running_tasks} tasks still running while {total_messages - running_tasks} new messages arrived")
        
        return target_tasks, needed_instances, "scale_up"

def execute_action(action, target_tasks, target_asg, running_tasks, cluster, task_def, asg_name):
    """Execution with short wait for container instances"""
    
    if action == "scale_down":
        print("🔽 Scaling down everything...")
        if running_tasks > 0:
            stop_all_tasks(cluster)
        asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=0, HonorCooldown=False)
        return "scaled_down_all"
    
    elif action == "scale_up":
        tasks_to_launch = target_tasks - running_tasks
        
        print(f"⚡ Scaling ASG to {target_asg} instances...")
        asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=target_asg, HonorCooldown=False)
        
        if tasks_to_launch > 0:
            print(f"🚀 Launching {tasks_to_launch} tasks...")
            
            # Wait briefly for container instances (hybrid approach)
            if wait_for_container_instances(cluster, max_wait=60):
                # Container instances ready - launch tasks
                launch_result = launch_tasks(cluster, task_def, tasks_to_launch)
                
                if launch_result["success"]:
                    return f"scaled_to_{target_asg}_launched_{launch_result['launched']}_tasks"
                else:
                    failure_summary = "_".join(launch_result.get("failure_reasons", ["unknown"]))[:50]
                    return f"scaled_to_{target_asg}_tasks_failed_{failure_summary}"
            else:
                # Still no instances - defer to next run
                print(f"⏳ Container instances not ready yet, tasks deferred to next run")
                return f"scaled_to_{target_asg}_tasks_deferred"
        
        elif tasks_to_launch == 0 and running_tasks > 0:
            print(f"⏸️ No new tasks to launch - instances are busy running {running_tasks} existing tasks")
            print(f"💡 Remaining messages will be processed when current tasks complete")
            return f"scaled_to_{target_asg}_instances_busy_running_{running_tasks}_tasks"
        
        else:
            print(f"ℹ️ No tasks to launch (target: {target_tasks}, running: {running_tasks})")
            return f"scaled_to_{target_asg}_no_tasks_needed"
    
    return "no_action"

def launch_tasks(cluster, task_def, count):
    """Launch ECS tasks - they'll queue in PENDING state until instances ready"""
    if count <= 0:
        return {"launched": 0, "failures": 0, "success": True}
    
    try:
        response = ecs.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            count=count,
            launchType='EC2'
        )
        
        # Get actual results from ECS response
        launched = len(response.get('tasks', []))
        failures = response.get('failures', [])
        failure_count = len(failures)
        
        print(f"✅ ECS Response: {launched} tasks launched, {failure_count} failures")
        
        if failures:
            for failure in failures:
                reason = failure.get('reason', 'Unknown')
                print(f"❌ Task launch failure: {reason}")
        
        return {
            "launched": launched,
            "failures": failure_count, 
            "success": launched > 0,
            "failure_reasons": [f.get('reason', 'Unknown') for f in failures]
        }
        
    except Exception as e:
        print(f"❌ RunTask error: {e}")
        return {"launched": 0, "failures": count, "success": False, "error": str(e)}

def stop_all_tasks(cluster):
    """Stop all running tasks"""
    response = ecs.list_tasks(cluster=cluster, desiredStatus='RUNNING')
    task_arns = response.get('taskArns', [])
    
    for task_arn in task_arns:
        ecs.stop_task(cluster=cluster, task=task_arn, reason='Scaling down - no messages')

def wait_for_container_instances(cluster, max_wait=60):
    """
    Wait briefly for container instances to register with ECS cluster
    Returns True if instances found, False if timeout
    """
    print(f"⏳ Checking for container instances (max {max_wait}s)...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            response = ecs.list_container_instances(cluster=cluster)
            instance_count = len(response.get('containerInstanceArns', []))
            
            if instance_count > 0:
                elapsed = int(time.time() - start_time)
                print(f"✅ Found {instance_count} container instances in {elapsed}s")
                return True
            
            print(f"⏳ No container instances yet, waiting...")
            time.sleep(10)  # Check every 10 seconds
            
        except Exception as e:
            print(f"⚠️ Error checking container instances: {e}")
            time.sleep(10)
    
    print(f"⏰ Timeout after {max_wait}s - no container instances found")
    return False