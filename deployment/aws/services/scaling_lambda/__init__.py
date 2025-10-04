"""
Lambda Scaling Functions for ECS Auto Scaling

This module provides EventBridge-triggered Lambda functions for ECS scaling:
- scale_out_handler: Start stopped instances and set ASG desired capacity to 2
- scale_in_handler: Stop instances but keep desired capacity at 2 (no terminate)
- lambda_scaling_deploy: Deploy both functions with proper IAM roles

Architecture:
- AMI-based deployment with pre-loaded models at /opt/vlm-models
- Stop/start instance scaling (preserves state, faster than terminate/create)
- EventBridge rules trigger scaling functions (configured manually on console)
- Auto Scaling Groups: min=0, max=2, desired=2
"""