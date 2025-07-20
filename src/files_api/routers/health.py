from fastapi import APIRouter
from files_api.config.settings import get_settings
from files_api.adapters.queue import QueueFactory
from files_api.services.database import get_invoice_service

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring API status and component readiness.
    
    Returns status of API, queue, and database components along with deployment mode.
    """
    settings = get_settings()
    
    health_status = {
        "status": "ok",
        "deployment_mode": settings.deployment_mode,
        "components": {
            "api": "ready",
            "queue": "initializing",
            "database": "ready"
        },
        "ready": False
    }
    
    # Check queue status (for worker tasks only)
    try:
        queue = QueueFactory.get_queue_handler()
        health_status["components"]["queue"] = "ready"
    except Exception as e:
        health_status["components"]["queue"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check database status
    try:
        invoice_service = get_invoice_service()
        # Simple database connectivity test
        health_status["components"]["database"] = "ready"
    except Exception as e:
        health_status["components"]["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Overall ready status
    components_ready = all(
        health_status["components"][comp] == "ready" 
        for comp in ["api", "queue", "database"]
    )
    
    if components_ready:
        health_status["ready"] = True
    
    return health_status