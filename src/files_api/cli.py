# cli.py
import click
import os
import asyncio
from files_api.vlm.rag import Worker
from files_api.msg_queue import QueueFactory
from files_api.config import config

@click.group()
def cli():
    """CLI commands for FastAPI and Worker management"""
    pass

@cli.command()
@click.option("--mode", 
              type=click.Choice(["local-mock", "local", "hybrid", "cloud"]), 
              default="local-mock")

def worker(mode):
    """Start the worker in specified mode"""
    print(f"Starting worker in {mode} mode...")
    
    # Set queue type in config
    os.environ["QUEUE_TYPE"] = mode
    config.QUEUE_TYPE = mode  # Update config directly
    
    queue = QueueFactory.get_queue_handler()
    print(queue)
    worker = Worker(queue)
    
    try:
        asyncio.run(worker.listen_for_tasks())
    except KeyboardInterrupt:
        worker.stop()

if __name__ == "__main__":
    cli()