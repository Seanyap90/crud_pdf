from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import logging
import pathlib
import os

from .routes import router, get_worker
from .worker.base import BaseWorker
from .worker.local_worker import LocalWorker
from .worker.state_machine import GatewayStateMachine
from .worker.config_state_machine import ConfigUpdateStateMachine
from database import local as db
from database import event_store
from .config import settings, update_settings

logger = logging.getLogger(__name__)

# Get the project root directory
# This should be the directory above the 'iot' module, regardless of how the app is run
ROOT_DIR = pathlib.Path(__file__).parent.parent.parent.absolute()

# Set the database path to be in the project root
DB_PATH = os.path.join(ROOT_DIR, "recycling.db")
logger.info(f"Setting database path to: {DB_PATH}")

# Update settings to use the root database path
update_settings(db_path=DB_PATH)

def create_app(worker_instance: BaseWorker = None) -> FastAPI:
    """Create and configure the FastAPI application
    
    Args:
        worker_instance: Optional worker instance for CLI compatibility
                         Not used for normal request processing
    
    Returns:
        FastAPI application instance
    """
    app = FastAPI(
        title="IoT Gateway Management API",
        description="API for managing IoT gateways with event sourcing and state machine",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, replace with specific origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Define a function to create a new worker for each request
    # This maintains statelessness in line with event sourcing principles
    async def create_request_worker() -> BaseWorker:
        """Create a new worker instance for each request"""
        # If worker_instance is provided by CLI, return that
        # This allows CLI to use a specific worker implementation
        if worker_instance is not None:
            return worker_instance
            
        # Otherwise create a fresh worker for this request
        worker = LocalWorker(db_path=DB_PATH)
        await worker.start()
        return worker
    
    # Override the dependency in routes.py
    app.dependency_overrides[get_worker] = create_request_worker

    # Include routes from routes.py
    app.include_router(router)

    # Set up events for application lifecycle
    @app.on_event("startup")
    async def startup_event():
        """Run when the application starts"""
        logger.info("Starting IoT Gateway Management API")
        
        # Initialize database
        try:
            logger.info(f"Initializing database at {DB_PATH}")
            # Initialize main database with all tables
            db.init_db(DB_PATH)
            # Initialize event store
            event_store.init_event_store(DB_PATH)
            # Initialize gateway tables
            GatewayStateMachine.initialize_gateway_tables(DB_PATH)
            # Initialize configuration tables
            ConfigUpdateStateMachine.initialize_config_tables(DB_PATH)
            logger.info("Database initialization completed")
        except Exception as e:
            logger.error(f"Error initializing database: {str(e)}")
            raise
        
        # Start the CLI-provided worker if present
        if worker_instance is not None:
            if not getattr(worker_instance, 'running', False):
                await worker_instance.start()
            logger.info(f"Started worker instance: {type(worker_instance).__name__}")
        
        logger.info("API started successfully")
    
    # Handle shutdown
    @app.on_event("shutdown")
    async def shutdown_event():
        """Run when the application is shutting down"""
        logger.info("Shutting down IoT Gateway Management API")
        
        # Stop the CLI-provided worker if present
        if worker_instance is not None:
            if getattr(worker_instance, 'running', False):
                await worker_instance.stop()
            logger.info(f"Stopped worker instance: {type(worker_instance).__name__}")

    return app

# Factory function for testing
def get_test_app() -> FastAPI:
    """Create a test instance of the application"""
    return create_app()