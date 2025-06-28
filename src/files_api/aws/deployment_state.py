"""
Deployment State Management and Tracking
Tracks deployment phases for rollback and cleanup with LIFO cleanup.
"""
import json
import logging
import time
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class DeploymentPhase(Enum):
    """Deployment phases in order."""
    INFRASTRUCTURE = "infrastructure"
    EFS_MOUNTS = "efs_mounts"
    MODEL_POPULATION = "model_population"
    COMPOSE_SERVICES = "compose_services"
    LAMBDA_API = "lambda_api"


class DeploymentStatus(Enum):
    """Deployment status for each phase."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class PhaseState:
    """State of a single deployment phase."""
    phase: str
    status: str
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    resources: Dict[str, Any] = None
    error_message: Optional[str] = None
    rollback_commands: List[str] = None
    
    def __post_init__(self):
        if self.resources is None:
            self.resources = {}
        if self.rollback_commands is None:
            self.rollback_commands = []


@dataclass
class DeploymentState:
    """Complete deployment state tracking."""
    deployment_id: str
    mode: str
    started_at: float
    phases: Dict[str, PhaseState]
    current_phase: Optional[str] = None
    status: str = "in_progress"
    completed_at: Optional[float] = None
    total_duration: Optional[float] = None
    
    def __post_init__(self):
        if not self.phases:
            self.phases = {}


class DeploymentStateManager:
    """Manages deployment state tracking and cleanup."""
    
    def __init__(self, state_file: str = ".deployment_state.json"):
        self.state_file = Path(state_file)
        self.state: Optional[DeploymentState] = None
    
    def start_deployment(self, deployment_id: str, mode: str) -> DeploymentState:
        """Start tracking a new deployment."""
        self.state = DeploymentState(
            deployment_id=deployment_id,
            mode=mode,
            started_at=time.time(),
            phases={}
        )
        
        # Initialize all phases as pending
        for phase in DeploymentPhase:
            self.state.phases[phase.value] = PhaseState(
                phase=phase.value,
                status=DeploymentStatus.PENDING.value
            )
        
        self._save_state()
        logger.info(f"🚀 Started deployment tracking: {deployment_id} ({mode})")
        return self.state
    
    def start_phase(self, phase: DeploymentPhase) -> None:
        """Mark a phase as started."""
        if not self.state:
            raise ValueError("No active deployment")
        
        phase_state = self.state.phases[phase.value]
        phase_state.status = DeploymentStatus.IN_PROGRESS.value
        phase_state.started_at = time.time()
        
        self.state.current_phase = phase.value
        self._save_state()
        
        logger.info(f"📋 Phase started: {phase.value}")
    
    def complete_phase(self, phase: DeploymentPhase, resources: Dict[str, Any] = None,
                      rollback_commands: List[str] = None) -> None:
        """Mark a phase as completed with resource tracking."""
        if not self.state:
            raise ValueError("No active deployment")
        
        phase_state = self.state.phases[phase.value]
        phase_state.status = DeploymentStatus.COMPLETED.value
        phase_state.completed_at = time.time()
        
        if phase_state.started_at:
            phase_state.duration_seconds = phase_state.completed_at - phase_state.started_at
        
        if resources:
            phase_state.resources.update(resources)
        
        if rollback_commands:
            phase_state.rollback_commands.extend(rollback_commands)
        
        self._save_state()
        
        duration_str = f" in {phase_state.duration_seconds:.1f}s" if phase_state.duration_seconds else ""
        logger.info(f"✅ Phase completed: {phase.value}{duration_str}")
    
    def fail_phase(self, phase: DeploymentPhase, error_message: str) -> None:
        """Mark a phase as failed."""
        if not self.state:
            raise ValueError("No active deployment")
        
        phase_state = self.state.phases[phase.value]
        phase_state.status = DeploymentStatus.FAILED.value
        phase_state.error_message = error_message
        phase_state.completed_at = time.time()
        
        if phase_state.started_at:
            phase_state.duration_seconds = phase_state.completed_at - phase_state.started_at
        
        self.state.status = "failed"
        self.state.completed_at = time.time()
        self.state.total_duration = self.state.completed_at - self.state.started_at
        
        self._save_state()
        
        logger.error(f"❌ Phase failed: {phase.value} - {error_message}")
    
    def complete_deployment(self) -> None:
        """Mark the entire deployment as completed."""
        if not self.state:
            raise ValueError("No active deployment")
        
        self.state.status = "completed"
        self.state.completed_at = time.time()
        self.state.total_duration = self.state.completed_at - self.state.started_at
        self.state.current_phase = None
        
        self._save_state()
        
        logger.info(f"🎉 Deployment completed: {self.state.deployment_id} in {self.state.total_duration:.1f}s")
    
    def load_state(self) -> Optional[DeploymentState]:
        """Load deployment state from file."""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                
                # Convert phases dict back to PhaseState objects
                phases = {}
                for phase_name, phase_data in data['phases'].items():
                    phases[phase_name] = PhaseState(**phase_data)
                
                data['phases'] = phases
                self.state = DeploymentState(**data)
                
                logger.info(f"📋 Loaded deployment state: {self.state.deployment_id}")
                return self.state
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to load deployment state: {e}")
        
        return None
    
    def _save_state(self) -> None:
        """Save deployment state to file."""
        if not self.state:
            return
        
        try:
            # Convert to dict for JSON serialization
            state_dict = asdict(self.state)
            
            with open(self.state_file, 'w') as f:
                json.dump(state_dict, f, indent=2)
                
        except Exception as e:
            logger.error(f"❌ Failed to save deployment state: {e}")
    
    def get_rollback_plan(self) -> List[Dict[str, Any]]:
        """Generate LIFO rollback plan based on completed phases."""
        if not self.state:
            return []
        
        rollback_plan = []
        
        # Process phases in reverse order (LIFO)
        phase_order = list(DeploymentPhase)
        phase_order.reverse()
        
        for phase in phase_order:
            phase_state = self.state.phases.get(phase.value)
            if (phase_state and 
                phase_state.status == DeploymentStatus.COMPLETED.value and 
                phase_state.rollback_commands):
                
                rollback_plan.append({
                    "phase": phase.value,
                    "commands": phase_state.rollback_commands,
                    "resources": phase_state.resources
                })
        
        return rollback_plan
    
    def execute_rollback(self, dry_run: bool = False) -> bool:
        """Execute LIFO rollback of deployment."""
        rollback_plan = self.get_rollback_plan()
        
        if not rollback_plan:
            logger.info("📋 No rollback actions needed")
            return True
        
        logger.info(f"🔄 Executing rollback plan ({len(rollback_plan)} phases)")
        
        if dry_run:
            logger.info("🔍 DRY RUN - No actual changes will be made")
        
        success = True
        
        for step in rollback_plan:
            phase = step["phase"]
            commands = step["commands"]
            
            logger.info(f"🔄 Rolling back phase: {phase}")
            
            for command in commands:
                logger.info(f"   Command: {command}")
                
                if not dry_run:
                    try:
                        # Execute rollback command
                        # This is a simplified implementation
                        # In production, you'd want more sophisticated command execution
                        import subprocess
                        result = subprocess.run(command, shell=True, capture_output=True, text=True)
                        
                        if result.returncode != 0:
                            logger.error(f"❌ Rollback command failed: {command}")
                            logger.error(f"   Error: {result.stderr}")
                            success = False
                        else:
                            logger.info(f"✅ Rollback command succeeded: {command}")
                            
                    except Exception as e:
                        logger.error(f"❌ Exception during rollback: {e}")
                        success = False
        
        if success:
            logger.info("✅ Rollback completed successfully")
            if not dry_run:
                self._mark_phases_rolled_back()
        else:
            logger.error("❌ Rollback completed with errors")
        
        return success
    
    def _mark_phases_rolled_back(self) -> None:
        """Mark rolled back phases in state."""
        if not self.state:
            return
        
        for phase_state in self.state.phases.values():
            if phase_state.status == DeploymentStatus.COMPLETED.value:
                phase_state.status = DeploymentStatus.ROLLED_BACK.value
        
        self.state.status = "rolled_back"
        self._save_state()
    
    def cleanup_state_file(self) -> None:
        """Remove deployment state file."""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                logger.info(f"🗑️ Cleaned up state file: {self.state_file}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to cleanup state file: {e}")
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get deployment status summary."""
        if not self.state:
            return {"status": "no_deployment"}
        
        completed_phases = sum(1 for phase in self.state.phases.values() 
                             if phase.status == DeploymentStatus.COMPLETED.value)
        total_phases = len(self.state.phases)
        
        return {
            "deployment_id": self.state.deployment_id,
            "mode": self.state.mode,
            "status": self.state.status,
            "current_phase": self.state.current_phase,
            "progress": f"{completed_phases}/{total_phases}",
            "duration": self.state.total_duration,
            "started_at": self.state.started_at,
            "phases": {
                name: {
                    "status": phase.status,
                    "duration": phase.duration_seconds,
                    "error": phase.error_message
                } for name, phase in self.state.phases.items()
            }
        }


def create_deployment_id(mode: str) -> str:
    """Create unique deployment ID."""
    timestamp = int(time.time())
    return f"{mode}-{timestamp}"


# CLI interface for state management
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Deployment state management")
    parser.add_argument("--action", choices=["status", "rollback", "cleanup"], 
                       required=True, help="Action to perform")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Dry run for rollback")
    parser.add_argument("--state-file", default=".deployment_state.json",
                       help="State file path")
    
    args = parser.parse_args()
    
    manager = DeploymentStateManager(args.state_file)
    
    if args.action == "status":
        state = manager.load_state()
        if state:
            summary = manager.get_status_summary()
            print(json.dumps(summary, indent=2))
        else:
            print("No deployment state found")
    
    elif args.action == "rollback":
        state = manager.load_state()
        if state:
            success = manager.execute_rollback(dry_run=args.dry_run)
            exit(0 if success else 1)
        else:
            print("No deployment state found")
            exit(1)
    
    elif args.action == "cleanup":
        manager.cleanup_state_file()
        print("State file cleaned up")