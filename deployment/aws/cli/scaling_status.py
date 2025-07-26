#!/usr/bin/env python3
"""
CLI tool for checking ECS auto-scaling status.
"""
import argparse
import json
import logging
from typing import Dict, Any

from deployment.aws.infrastructure.ecs_scaling_manager import get_scaling_manager
from src.files_api.config.settings import get_settings

logger = logging.getLogger(__name__)


def get_scaling_status(service_name: str = None, detailed: bool = False) -> Dict[str, Any]:
    """Get scaling status for services."""
    try:
        scaling_manager = get_scaling_manager()
        
        if service_name:
            # Get status for specific service
            autoscaler = scaling_manager.get_autoscaler(service_name)
            if not autoscaler:
                return {
                    "error": f"No auto-scaler found for service: {service_name}",
                    "available_services": list(scaling_manager.autoscalers.keys())
                }
            
            status = autoscaler.get_scaling_status()
            if detailed:
                # Add additional details
                config = scaling_manager.scaling_configs.get(service_name, {})
                status['configuration'] = config
                
            return {service_name: status}
        else:
            # Get status for all services
            all_status = scaling_manager.get_all_scaling_status()
            
            if not detailed:
                # Simplified view
                simplified = {}
                for svc_name, svc_status in all_status.items():
                    simplified[svc_name] = {
                        "status": svc_status.get("status"),
                        "current_capacity": svc_status.get("current_capacity"),
                        "desired_capacity": svc_status.get("desired_capacity"),
                        "min_capacity": svc_status.get("min_capacity"),
                        "max_capacity": svc_status.get("max_capacity")
                    }
                return simplified
            
            return all_status
            
    except Exception as e:
        logger.error(f"Failed to get scaling status: {e}")
        return {"error": str(e)}


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Check ECS auto-scaling status")
    parser.add_argument("--service", help="Specific service name to check")
    parser.add_argument("--detailed", action="store_true", help="Show detailed information")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')
    
    # Get scaling status
    status = get_scaling_status(args.service, args.detailed)
    
    if args.json:
        print(json.dumps(status, indent=2, default=str))
    else:
        # Pretty print
        if "error" in status:
            print(f"❌ Error: {status['error']}")
            if "available_services" in status:
                print(f"Available services: {', '.join(status['available_services'])}")
            return 1
        
        print("🔧 ECS Auto-scaling Status")
        print("=" * 50)
        
        for service_name, service_status in status.items():
            print(f"\n📊 Service: {service_name}")
            
            if service_status.get("status") == "active":
                print(f"   Status: ✅ Active")
                print(f"   Capacity: {service_status.get('current_capacity', 'N/A')}/{service_status.get('desired_capacity', 'N/A')} (current/desired)")
                print(f"   Range: {service_status.get('min_capacity', 'N/A')}-{service_status.get('max_capacity', 'N/A')} (min-max)")
                
                if args.detailed and "configuration" in service_status:
                    config = service_status["configuration"]
                    print(f"   Queue URL: {config.get('queue_url', 'N/A')}")
                    print(f"   Resource ID: {service_status.get('resource_id', 'N/A')}")
                    
            elif service_status.get("status") == "error":
                print(f"   Status: ❌ Error")
                print(f"   Error: {service_status.get('error', 'Unknown error')}")
            else:
                print(f"   Status: ⚠️ {service_status.get('status', 'Unknown')}")
        
        print("\n" + "=" * 50)
    
    return 0


if __name__ == "__main__":
    exit(main())