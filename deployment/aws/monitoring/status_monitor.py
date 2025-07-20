"""
Real-time deployment status checking and health monitoring.

Monitors health and status of deployed AWS resources including ECS services,
Lambda functions, and EFS mount states.
"""

import boto3
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from botocore.exceptions import ClientError, NoCredentialsError

from deployment.aws.utils.aws_clients import get_ecs_client, get_lambda_client, get_efs_client
from src.files_api.config.settings import get_settings


class StatusMonitor:
    """Monitor deployment health and status of AWS resources."""
    
    def __init__(self):
        self.settings = get_settings()
        self.ecs_client = None
        self.lambda_client = None
        self.efs_client = None
        self.cloudwatch_client = None
        
    def _init_clients(self):
        """Initialize AWS clients lazily."""
        if not self.ecs_client:
            self.ecs_client = get_ecs_client()
        if not self.lambda_client:
            self.lambda_client = get_lambda_client()
        if not self.efs_client:
            self.efs_client = get_efs_client()
        if not self.cloudwatch_client:
            self.cloudwatch_client = boto3.client('cloudwatch', region_name=self.settings.aws_region)
    
    def check_deployment_health(self) -> Dict[str, Any]:
        """
        Check overall deployment health across all services.
        
        Returns:
            Dict containing health status of all components
        """
        self._init_clients()
        
        health_report = {
            'timestamp': datetime.utcnow().isoformat(),
            'overall_status': 'healthy',
            'components': {},
            'warnings': [],
            'errors': []
        }
        
        # Check ECS services
        try:
            ecs_status = self._check_ecs_services()
            health_report['components']['ecs'] = ecs_status
            
            if ecs_status['status'] != 'healthy':
                health_report['overall_status'] = 'degraded'
                if ecs_status.get('errors'):
                    health_report['errors'].extend(ecs_status['errors'])
                    
        except Exception as e:
            health_report['components']['ecs'] = {'status': 'error', 'error': str(e)}
            health_report['overall_status'] = 'unhealthy'
            health_report['errors'].append(f"ECS health check failed: {e}")
        
        # Check Lambda functions
        try:
            lambda_status = self._check_lambda_functions()
            health_report['components']['lambda'] = lambda_status
            
            if lambda_status['status'] != 'healthy':
                if health_report['overall_status'] == 'healthy':
                    health_report['overall_status'] = 'degraded'
                if lambda_status.get('errors'):
                    health_report['errors'].extend(lambda_status['errors'])
                    
        except Exception as e:
            health_report['components']['lambda'] = {'status': 'error', 'error': str(e)}
            health_report['overall_status'] = 'unhealthy'
            health_report['errors'].append(f"Lambda health check failed: {e}")
        
        # Check EFS mount states
        try:
            efs_status = self._check_efs_mounts()
            health_report['components']['efs'] = efs_status
            
            if efs_status['status'] != 'healthy':
                if health_report['overall_status'] == 'healthy':
                    health_report['overall_status'] = 'degraded'
                if efs_status.get('warnings'):
                    health_report['warnings'].extend(efs_status['warnings'])
                    
        except Exception as e:
            health_report['components']['efs'] = {'status': 'error', 'error': str(e)}
            health_report['overall_status'] = 'unhealthy'
            health_report['errors'].append(f"EFS health check failed: {e}")
        
        return health_report
    
    def _check_ecs_services(self) -> Dict[str, Any]:
        """Check ECS service health."""
        cluster_name = f"{self.settings.app_name}-ecs-cluster"
        
        try:
            # List services in cluster
            services_response = self.ecs_client.list_services(cluster=cluster_name)
            service_arns = services_response.get('serviceArns', [])
            
            if not service_arns:
                return {
                    'status': 'warning',
                    'message': 'No ECS services found',
                    'services': []
                }
            
            # Describe services
            describe_response = self.ecs_client.describe_services(
                cluster=cluster_name,
                services=service_arns
            )
            
            services_status = []
            overall_healthy = True
            errors = []
            
            for service in describe_response.get('services', []):
                service_name = service['serviceName']
                desired_count = service['desiredCount']
                running_count = service['runningCount']
                pending_count = service['pendingCount']
                status = service['status']
                
                service_health = {
                    'name': service_name,
                    'status': status,
                    'desired': desired_count,
                    'running': running_count,
                    'pending': pending_count,
                    'healthy': running_count == desired_count and status == 'ACTIVE'
                }
                
                if not service_health['healthy']:
                    overall_healthy = False
                    if running_count < desired_count:
                        errors.append(f"Service {service_name}: {running_count}/{desired_count} tasks running")
                
                services_status.append(service_health)
            
            return {
                'status': 'healthy' if overall_healthy else 'degraded',
                'cluster': cluster_name,
                'services': services_status,
                'errors': errors
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ClusterNotFoundException':
                return {
                    'status': 'error',
                    'error': f"ECS cluster '{cluster_name}' not found"
                }
            raise
    
    def _check_lambda_functions(self) -> Dict[str, Any]:
        """Check Lambda function health."""
        function_names = [
            f"{self.settings.app_name}-files-api",
            f"{self.settings.app_name}-iot-backend"
        ]
        
        functions_status = []
        overall_healthy = True
        errors = []
        
        for function_name in function_names:
            try:
                # Get function configuration
                response = self.lambda_client.get_function(FunctionName=function_name)
                config = response['Configuration']
                
                function_health = {
                    'name': function_name,
                    'state': config['State'],
                    'last_modified': config['LastModified'],
                    'runtime': config['Runtime'],
                    'timeout': config['Timeout'],
                    'memory': config['MemorySize'],
                    'healthy': config['State'] == 'Active'
                }
                
                if not function_health['healthy']:
                    overall_healthy = False
                    errors.append(f"Function {function_name} state: {config['State']}")
                
                # Check recent invocations
                try:
                    end_time = datetime.utcnow()
                    start_time = end_time - timedelta(hours=1)
                    
                    metrics = self.cloudwatch_client.get_metric_statistics(
                        Namespace='AWS/Lambda',
                        MetricName='Errors',
                        Dimensions=[{'Name': 'FunctionName', 'Value': function_name}],
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=3600,
                        Statistics=['Sum']
                    )
                    
                    error_count = sum(point['Sum'] for point in metrics['Datapoints'])
                    function_health['recent_errors'] = error_count
                    
                    if error_count > 0:
                        function_health['healthy'] = False
                        overall_healthy = False
                        errors.append(f"Function {function_name}: {error_count} errors in last hour")
                        
                except Exception as e:
                    function_health['metrics_error'] = str(e)
                
                functions_status.append(function_health)
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    functions_status.append({
                        'name': function_name,
                        'status': 'not_found',
                        'healthy': False
                    })
                    overall_healthy = False
                    errors.append(f"Function {function_name} not found")
                else:
                    raise
        
        return {
            'status': 'healthy' if overall_healthy else 'degraded',
            'functions': functions_status,
            'errors': errors
        }
    
    def _check_efs_mounts(self) -> Dict[str, Any]:
        """Check EFS mount target health."""
        try:
            # List EFS file systems
            response = self.efs_client.describe_file_systems()
            file_systems = response.get('FileSystems', [])
            
            app_file_systems = [
                fs for fs in file_systems 
                if fs.get('Name', '').startswith(self.settings.app_name)
            ]
            
            if not app_file_systems:
                return {
                    'status': 'warning',
                    'message': 'No EFS file systems found for this app',
                    'file_systems': []
                }
            
            efs_status = []
            overall_healthy = True
            warnings = []
            
            for fs in app_file_systems:
                fs_id = fs['FileSystemId']
                
                # Get mount targets
                mount_response = self.efs_client.describe_mount_targets(FileSystemId=fs_id)
                mount_targets = mount_response.get('MountTargets', [])
                
                healthy_mounts = sum(1 for mt in mount_targets if mt['LifeCycleState'] == 'available')
                
                fs_health = {
                    'id': fs_id,
                    'name': fs.get('Name', 'unnamed'),
                    'state': fs['LifeCycleState'],
                    'mount_targets': len(mount_targets),
                    'healthy_mounts': healthy_mounts,
                    'size_bytes': fs.get('SizeInBytes', {}).get('Value', 0),
                    'healthy': fs['LifeCycleState'] == 'available' and healthy_mounts > 0
                }
                
                if not fs_health['healthy']:
                    overall_healthy = False
                    if fs['LifeCycleState'] != 'available':
                        warnings.append(f"EFS {fs_id} state: {fs['LifeCycleState']}")
                    if healthy_mounts == 0:
                        warnings.append(f"EFS {fs_id}: No healthy mount targets")
                
                efs_status.append(fs_health)
            
            return {
                'status': 'healthy' if overall_healthy else 'degraded',
                'file_systems': efs_status,
                'warnings': warnings
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def get_resource_status(self, resource_type: str, resource_id: str) -> Dict[str, Any]:
        """
        Get detailed status for a specific resource.
        
        Args:
            resource_type: Type of resource ('ecs_service', 'lambda', 'efs')
            resource_id: Resource identifier
            
        Returns:
            Dict containing detailed resource status
        """
        self._init_clients()
        
        if resource_type == 'ecs_service':
            return self._get_ecs_service_status(resource_id)
        elif resource_type == 'lambda':
            return self._get_lambda_status(resource_id)
        elif resource_type == 'efs':
            return self._get_efs_status(resource_id)
        else:
            return {'error': f'Unknown resource type: {resource_type}'}
    
    def _get_ecs_service_status(self, service_name: str) -> Dict[str, Any]:
        """Get detailed ECS service status."""
        cluster_name = f"{self.settings.app_name}-ecs-cluster"
        
        try:
            response = self.ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name]
            )
            
            if not response.get('services'):
                return {'error': f'Service {service_name} not found'}
            
            service = response['services'][0]
            
            # Get task details
            tasks_response = self.ecs_client.list_tasks(
                cluster=cluster_name,
                serviceName=service_name
            )
            
            task_details = []
            if tasks_response.get('taskArns'):
                tasks_describe = self.ecs_client.describe_tasks(
                    cluster=cluster_name,
                    tasks=tasks_response['taskArns']
                )
                
                for task in tasks_describe.get('tasks', []):
                    task_details.append({
                        'arn': task['taskArn'],
                        'status': task['lastStatus'],
                        'health': task.get('healthStatus', 'UNKNOWN'),
                        'created': task['createdAt'].isoformat(),
                        'cpu_utilization': task.get('cpu', 'unknown'),
                        'memory_utilization': task.get('memory', 'unknown')
                    })
            
            return {
                'service': {
                    'name': service['serviceName'],
                    'status': service['status'],
                    'desired_count': service['desiredCount'],
                    'running_count': service['runningCount'],
                    'pending_count': service['pendingCount'],
                    'task_definition': service['taskDefinition'],
                    'platform_version': service.get('platformVersion', 'unknown')
                },
                'tasks': task_details,
                'events': service.get('events', [])[:5]  # Last 5 events
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def _get_lambda_status(self, function_name: str) -> Dict[str, Any]:
        """Get detailed Lambda function status."""
        try:
            # Get function details
            response = self.lambda_client.get_function(FunctionName=function_name)
            config = response['Configuration']
            
            # Get recent metrics
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=24)
            
            metrics = {}
            metric_names = ['Invocations', 'Errors', 'Duration', 'Throttles']
            
            for metric_name in metric_names:
                try:
                    metric_response = self.cloudwatch_client.get_metric_statistics(
                        Namespace='AWS/Lambda',
                        MetricName=metric_name,
                        Dimensions=[{'Name': 'FunctionName', 'Value': function_name}],
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=3600,
                        Statistics=['Sum', 'Average'] if metric_name == 'Duration' else ['Sum']
                    )
                    
                    datapoints = metric_response.get('Datapoints', [])
                    if datapoints:
                        if metric_name == 'Duration':
                            metrics[metric_name] = {
                                'average_ms': sum(p['Average'] for p in datapoints) / len(datapoints),
                                'total_invocations': sum(p['Sum'] for p in datapoints)
                            }
                        else:
                            metrics[metric_name] = sum(p['Sum'] for p in datapoints)
                    else:
                        metrics[metric_name] = 0
                        
                except Exception as e:
                    metrics[metric_name] = f'Error: {e}'
            
            return {
                'function': {
                    'name': config['FunctionName'],
                    'state': config['State'],
                    'runtime': config['Runtime'],
                    'timeout': config['Timeout'],
                    'memory': config['MemorySize'],
                    'last_modified': config['LastModified'],
                    'code_size': config['CodeSize'],
                    'environment': config.get('Environment', {}).get('Variables', {})
                },
                'metrics_24h': metrics
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def _get_efs_status(self, file_system_id: str) -> Dict[str, Any]:
        """Get detailed EFS status."""
        try:
            # Get file system details
            fs_response = self.efs_client.describe_file_systems(FileSystemId=file_system_id)
            
            if not fs_response.get('FileSystems'):
                return {'error': f'EFS {file_system_id} not found'}
            
            fs = fs_response['FileSystems'][0]
            
            # Get mount targets
            mount_response = self.efs_client.describe_mount_targets(FileSystemId=file_system_id)
            mount_targets = mount_response.get('MountTargets', [])
            
            # Get access points
            access_response = self.efs_client.describe_access_points(FileSystemId=file_system_id)
            access_points = access_response.get('AccessPoints', [])
            
            return {
                'file_system': {
                    'id': fs['FileSystemId'],
                    'name': fs.get('Name', 'unnamed'),
                    'state': fs['LifeCycleState'],
                    'performance_mode': fs['PerformanceMode'],
                    'throughput_mode': fs['ThroughputMode'],
                    'size_bytes': fs.get('SizeInBytes', {}).get('Value', 0),
                    'created': fs['CreationTime'].isoformat()
                },
                'mount_targets': [
                    {
                        'id': mt['MountTargetId'],
                        'subnet_id': mt['SubnetId'],
                        'state': mt['LifeCycleState'],
                        'ip_address': mt.get('IpAddress', 'unknown')
                    }
                    for mt in mount_targets
                ],
                'access_points': [
                    {
                        'id': ap['AccessPointId'],
                        'state': ap['LifeCycleState'],
                        'path': ap.get('RootDirectory', {}).get('Path', '/')
                    }
                    for ap in access_points
                ]
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def generate_status_report(self, output_format: str = 'json') -> str:
        """
        Generate a comprehensive status report.
        
        Args:
            output_format: Format for output ('json', 'text')
            
        Returns:
            Formatted status report
        """
        health_data = self.check_deployment_health()
        
        if output_format == 'json':
            return json.dumps(health_data, indent=2)
        
        elif output_format == 'text':
            report = []
            report.append(f"AWS Deployment Status Report")
            report.append(f"Generated: {health_data['timestamp']}")
            report.append(f"Overall Status: {health_data['overall_status'].upper()}")
            report.append("")
            
            # ECS Services
            if 'ecs' in health_data['components']:
                ecs = health_data['components']['ecs']
                report.append("ECS Services:")
                if 'services' in ecs:
                    for service in ecs['services']:
                        status_icon = "✅" if service['healthy'] else "❌"
                        report.append(f"  {status_icon} {service['name']}: {service['running']}/{service['desired']} tasks")
                else:
                    report.append(f"  Status: {ecs.get('status', 'unknown')}")
                report.append("")
            
            # Lambda Functions
            if 'lambda' in health_data['components']:
                lambda_data = health_data['components']['lambda']
                report.append("Lambda Functions:")
                if 'functions' in lambda_data:
                    for func in lambda_data['functions']:
                        status_icon = "✅" if func['healthy'] else "❌"
                        report.append(f"  {status_icon} {func['name']}: {func.get('state', 'unknown')}")
                else:
                    report.append(f"  Status: {lambda_data.get('status', 'unknown')}")
                report.append("")
            
            # EFS File Systems
            if 'efs' in health_data['components']:
                efs = health_data['components']['efs']
                report.append("EFS File Systems:")
                if 'file_systems' in efs:
                    for fs in efs['file_systems']:
                        status_icon = "✅" if fs['healthy'] else "❌"
                        size_gb = fs['size_bytes'] / (1024**3) if fs['size_bytes'] > 0 else 0
                        report.append(f"  {status_icon} {fs['name']}: {fs['state']} ({size_gb:.2f} GB)")
                else:
                    report.append(f"  Status: {efs.get('status', 'unknown')}")
                report.append("")
            
            # Warnings and Errors
            if health_data['warnings']:
                report.append("Warnings:")
                for warning in health_data['warnings']:
                    report.append(f"  ⚠️ {warning}")
                report.append("")
            
            if health_data['errors']:
                report.append("Errors:")
                for error in health_data['errors']:
                    report.append(f"  ❌ {error}")
                report.append("")
            
            return "\n".join(report)
        
        else:
            raise ValueError(f"Unsupported output format: {output_format}")


def main():
    """CLI entry point for status monitoring."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor AWS deployment status')
    parser.add_argument('--format', choices=['json', 'text'], default='text',
                       help='Output format')
    parser.add_argument('--resource-type', choices=['ecs_service', 'lambda', 'efs'],
                       help='Check specific resource type')
    parser.add_argument('--resource-id', help='Specific resource ID to check')
    parser.add_argument('--continuous', action='store_true',
                       help='Continuous monitoring mode')
    parser.add_argument('--interval', type=int, default=60,
                       help='Monitoring interval in seconds (for continuous mode)')
    
    args = parser.parse_args()
    
    monitor = StatusMonitor()
    
    try:
        if args.resource_type and args.resource_id:
            # Check specific resource
            result = monitor.get_resource_status(args.resource_type, args.resource_id)
            print(json.dumps(result, indent=2))
        
        elif args.continuous:
            # Continuous monitoring
            print("Starting continuous monitoring... (Press Ctrl+C to stop)")
            try:
                while True:
                    print(f"\n{'='*60}")
                    print(f"Status Check: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print('='*60)
                    
                    report = monitor.generate_status_report(args.format)
                    print(report)
                    
                    time.sleep(args.interval)
                    
            except KeyboardInterrupt:
                print("\nMonitoring stopped.")
        
        else:
            # Single status check
            report = monitor.generate_status_report(args.format)
            print(report)
            
    except NoCredentialsError:
        print("❌ Error: AWS credentials not configured")
        exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()