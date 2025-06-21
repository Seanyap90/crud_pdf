"""
ECS Autoscaling Simulator
Simulates ECS worker autoscaling decisions based on SQS metrics without spawning real containers.
Implements ECS scaling logic: 1:1 message to task ratio with scale-to-zero capability.
"""
import asyncio
import logging
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from files_api.aws.utils import get_sqs_client
from files_api.settings import get_settings

logger = logging.getLogger(__name__)

@dataclass
class ScalingEvent:
    """Represents a scaling event."""
    timestamp: datetime
    action: str  # 'scale_up', 'scale_down', 'no_action'
    reason: str
    queue_messages: int
    instance_count_before: int
    instance_count_after: int
    
    def to_dict(self):
        return {
            **asdict(self),
            'timestamp': self.timestamp.isoformat()
        }

@dataclass
class ECSScalingConfig:
    """ECS service scaling configuration for simulation."""
    min_instances: int = 0  # Scale to zero capability
    max_instances: int = 3  # Max 3 GPU instances
    scale_up_threshold: int = 1  # 1 message = 1 task (immediate)
    scale_down_threshold: int = 0  # 0 messages = scale to zero
    cooldown_period: int = 300  # 5 minutes (scale-in delay)
    evaluation_periods: int = 1  # Immediate scaling (no delay for scale-out)
    evaluation_interval: int = 15  # seconds between evaluations

class ECSAutoscalingSimulator:
    """Simulates ECS worker autoscaling without real containers."""
    
    def __init__(self, queue_url: str, config: ECSScalingConfig = None):
        self.settings = get_settings()
        self.queue_url = queue_url
        self.config = config or ECSScalingConfig()
        
        # Simulation state
        self.current_instance_count = self.config.min_instances
        self.last_scaling_action = datetime.min
        self.scaling_evaluations = {'up': 0, 'down': 0}
        
        # Event history
        self.scaling_events: List[ScalingEvent] = []
        self.metrics_history: List[Dict] = []
        
        # Running state
        self.running = False
        
        logger.info(f"🎯 ECS Autoscaling Simulator initialized")
        logger.info(f"   Queue: {queue_url}")
        logger.info(f"   Config: {asdict(self.config)}")
    
    async def get_queue_metrics(self) -> Dict:
        """Get current SQS queue metrics."""
        try:
            sqs_client = get_sqs_client()
            
            response = sqs_client.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=[
                    'ApproximateNumberOfMessages',
                    'ApproximateNumberOfMessagesNotVisible'
                ]
            )
            
            attributes = response.get('Attributes', {})
            visible = int(attributes.get('ApproximateNumberOfMessages', 0))
            in_flight = int(attributes.get('ApproximateNumberOfMessagesNotVisible', 0))
            
            return {
                'visible_messages': visible,
                'in_flight_messages': in_flight,
                'total_messages': visible + in_flight,
                'messages_per_instance': (visible + in_flight) / max(1, self.current_instance_count),
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            logger.error(f"Error getting queue metrics: {e}")
            return {
                'visible_messages': 0,
                'in_flight_messages': 0,
                'total_messages': 0,
                'messages_per_instance': 0,
                'timestamp': datetime.now()
            }
    
    def evaluate_scaling_decision(self, metrics: Dict) -> str:
        """Evaluate scaling decision using ECS logic: 1:1 message to task ratio."""
        total_messages = metrics['total_messages']
        current_time = metrics['timestamp']
        
        # ECS Scaling Logic:
        # 0 messages = 0 tasks (scale to zero)
        # 1 message = 1 task (immediate scale-up)
        # 2+ messages = n tasks (1:1 ratio)
        desired_task_count = min(total_messages, self.config.max_instances)
        desired_task_count = max(desired_task_count, self.config.min_instances)
        
        # Determine action needed
        if desired_task_count > self.current_instance_count:
            # Scale out immediately (no cooldown for scale-out in ECS)
            self.scaling_evaluations['up'] += 1
            self.scaling_evaluations['down'] = 0
            
            if self.scaling_evaluations['up'] >= self.config.evaluation_periods:
                return 'scale_up'
        
        elif desired_task_count < self.current_instance_count:
            # Scale in with cooldown period (5-minute delay)
            time_since_last_scaling = (current_time - self.last_scaling_action).total_seconds()
            if time_since_last_scaling < self.config.cooldown_period:
                return 'cooldown'
            
            self.scaling_evaluations['down'] += 1
            self.scaling_evaluations['up'] = 0
            
            if self.scaling_evaluations['down'] >= self.config.evaluation_periods:
                return 'scale_down'
        
        # No scaling needed
        else:
            self.scaling_evaluations = {'up': 0, 'down': 0}
        
        return 'no_action'
    
    def execute_scaling_decision(self, decision: str, metrics: Dict) -> ScalingEvent:
        """Execute scaling decision and return event."""
        instance_count_before = self.current_instance_count
        instance_count_after = self.current_instance_count
        reason = ""
        
        total_messages = metrics['total_messages']
        desired_task_count = min(max(total_messages, self.config.min_instances), self.config.max_instances)
        
        if decision == 'scale_up':
            instance_count_after = desired_task_count
            reason = f"ECS scaling: {total_messages} messages → {desired_task_count} tasks (1:1 ratio)"
            self.last_scaling_action = metrics['timestamp']
            self.scaling_evaluations = {'up': 0, 'down': 0}
            
        elif decision == 'scale_down':
            instance_count_after = desired_task_count  
            reason = f"ECS scaling: {total_messages} messages → {desired_task_count} tasks (5min cooldown passed)"
            self.last_scaling_action = metrics['timestamp']
            self.scaling_evaluations = {'up': 0, 'down': 0}
            
        elif decision == 'cooldown':
            reason = f"Scale-in cooldown active ({(metrics['timestamp'] - self.last_scaling_action).total_seconds():.0f}s < {self.config.cooldown_period}s)"
            
        else:  # no_action
            reason = f"No scaling needed: {total_messages} messages, {self.current_instance_count} tasks running"
        
        # Update instance count
        self.current_instance_count = instance_count_after
        
        # Create scaling event
        event = ScalingEvent(
            timestamp=metrics['timestamp'],
            action=decision,
            reason=reason,
            queue_messages=metrics['total_messages'],
            instance_count_before=instance_count_before,
            instance_count_after=instance_count_after
        )
        
        self.scaling_events.append(event)
        
        # Log the decision
        timestamp = metrics['timestamp'].strftime('%H:%M:%S')
        if decision in ['scale_up', 'scale_down']:
            action_emoji = "📈" if decision == 'scale_up' else "📉"
            logger.info(
                f"[{timestamp}] {action_emoji} SCALING DECISION: {decision.upper()} "
                f"({instance_count_before} → {instance_count_after} instances) "
                f"| Queue: {metrics['total_messages']} msgs "
                f"| Per instance: {metrics['messages_per_instance']:.1f} "
                f"| Reason: {reason}"
            )
        else:
            logger.info(
                f"[{timestamp}] ⚖️  NO SCALING: {reason} "
                f"| Instances: {self.current_instance_count} "
                f"| Queue: {metrics['total_messages']} msgs "
                f"| Per instance: {metrics['messages_per_instance']:.1f} "
                f"| Evaluations: ↑{self.scaling_evaluations['up']}/{self.config.evaluation_periods} ↓{self.scaling_evaluations['down']}/{self.config.evaluation_periods}"
            )
        
        return event
    
    def get_summary_stats(self) -> Dict:
        """Get summary statistics of scaling behavior."""
        if not self.scaling_events:
            return {}
        
        scale_ups = len([e for e in self.scaling_events if e.action == 'scale_up'])
        scale_downs = len([e for e in self.scaling_events if e.action == 'scale_down'])
        no_actions = len([e for e in self.scaling_events if e.action == 'no_action'])
        cooldowns = len([e for e in self.scaling_events if e.action == 'cooldown'])
        
        return {
            'total_evaluations': len(self.scaling_events),
            'scale_up_actions': scale_ups,
            'scale_down_actions': scale_downs,
            'no_action_evaluations': no_actions,
            'cooldown_evaluations': cooldowns,
            'current_instance_count': self.current_instance_count,
            'min_instances_seen': min([e.instance_count_after for e in self.scaling_events]),
            'max_instances_seen': max([e.instance_count_after for e in self.scaling_events]),
        }
    
    async def run(self, duration_seconds: Optional[int] = None):
        """Run the autoscaling simulation."""
        self.running = True
        start_time = datetime.now()
        
        logger.info(f"🚀 Starting ECS Autoscaling Simulation")
        logger.info(f"   Evaluation interval: {self.config.evaluation_interval} seconds")
        if duration_seconds:
            logger.info(f"   Duration: {duration_seconds} seconds")
        else:
            logger.info("   Duration: Indefinite (Ctrl+C to stop)")
        
        try:
            evaluation_count = 0
            last_queue_size = 0
            
            while self.running:
                evaluation_count += 1
                
                # Get current metrics
                metrics = await self.get_queue_metrics()
                self.metrics_history.append(metrics)
                
                # Detect queue changes between evaluations
                current_queue_size = metrics['total_messages']
                if current_queue_size != last_queue_size:
                    timestamp = metrics['timestamp'].strftime('%H:%M:%S')
                    if current_queue_size > last_queue_size:
                        logger.info(f"[{timestamp}] 📤 QUEUE ACTIVITY: +{current_queue_size - last_queue_size} messages (total: {current_queue_size})")
                    elif current_queue_size < last_queue_size:
                        logger.info(f"[{timestamp}] 📥 QUEUE ACTIVITY: -{last_queue_size - current_queue_size} messages (total: {current_queue_size})")
                    last_queue_size = current_queue_size
                
                # Evaluate scaling decision
                decision = self.evaluate_scaling_decision(metrics)
                
                # Execute decision
                event = self.execute_scaling_decision(decision, metrics)
                
                # Check if we should stop
                if duration_seconds:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed >= duration_seconds:
                        break
                
                # Wait before next evaluation
                await asyncio.sleep(self.config.evaluation_interval)
                
        except asyncio.CancelledError:
            logger.info("🛑 Simulation cancelled by user")
        except Exception as e:
            logger.error(f"❌ Simulation error: {e}")
        finally:
            self.running = False
            
            # Print summary
            self.print_summary()
    
    def print_summary(self):
        """Print simulation summary."""
        stats = self.get_summary_stats()
        
        logger.info("\n" + "="*60)
        logger.info("📊 AUTOSCALING SIMULATION SUMMARY")
        logger.info("="*60)
        logger.info(f"Total Evaluations: {stats.get('total_evaluations', 0)}")
        logger.info(f"Scale Up Actions: {stats.get('scale_up_actions', 0)}")
        logger.info(f"Scale Down Actions: {stats.get('scale_down_actions', 0)}")
        logger.info(f"No Action Evaluations: {stats.get('no_action_evaluations', 0)}")
        logger.info(f"Cooldown Evaluations: {stats.get('cooldown_evaluations', 0)}")
        logger.info(f"Final Instance Count: {stats.get('current_instance_count', 0)}")
        logger.info(f"Instance Range: {stats.get('min_instances_seen', 0)} - {stats.get('max_instances_seen', 0)}")
        logger.info("="*60)
        
        # Show recent scaling events
        recent_events = self.scaling_events[-10:]  # Last 10 events
        if recent_events:
            logger.info("\n🕒 RECENT SCALING EVENTS:")
            for event in recent_events:
                if event.action in ['scale_up', 'scale_down']:
                    logger.info(f"   {event.timestamp.strftime('%H:%M:%S')} - {event.action.upper()}: {event.instance_count_before} → {event.instance_count_after}")
    
    def stop(self):
        """Stop the simulation."""
        self.running = False

# CLI interface
async def main():
    """Main function for standalone testing."""
    import argparse
    
    parser = argparse.ArgumentParser(description='ECS Autoscaling Simulator')
    parser.add_argument('--queue-url', required=True, help='SQS Queue URL')
    parser.add_argument('--duration', type=int, help='Simulation duration in seconds (default: run until stopped)')
    parser.add_argument('--min-instances', type=int, default=0, help='Minimum instances (0 for scale-to-zero)')
    parser.add_argument('--max-instances', type=int, default=3, help='Maximum instances (GPU limit)')
    parser.add_argument('--scale-up-threshold', type=int, default=1, help='Scale up threshold (1 message = 1 task)')
    parser.add_argument('--scale-down-threshold', type=int, default=0, help='Scale down threshold (0 messages = scale to zero)')
    parser.add_argument('--cooldown', type=int, default=300, help='Cooldown period in seconds')
    parser.add_argument('--evaluation-periods', type=int, default=1, help='Consecutive evaluation periods required (1 for immediate scaling)')
    parser.add_argument('--evaluation-interval', type=int, default=15, help='Seconds between evaluations')
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create scaling config
    config = ECSScalingConfig(
        min_instances=args.min_instances,
        max_instances=args.max_instances,
        scale_up_threshold=args.scale_up_threshold,
        scale_down_threshold=args.scale_down_threshold,
        cooldown_period=args.cooldown,
        evaluation_periods=args.evaluation_periods,
        evaluation_interval=args.evaluation_interval
    )
    
    # Create and run simulator
    simulator = ECSAutoscalingSimulator(args.queue_url, config)
    
    try:
        await simulator.run(args.duration)
    except KeyboardInterrupt:
        simulator.stop()

if __name__ == "__main__":
    asyncio.run(main())