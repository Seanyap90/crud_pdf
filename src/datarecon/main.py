import logging
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

from datarecon.routes import router
from database.local import init_db

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create a FastAPI application for data reconciliation."""
    app = FastAPI(
        title="Data Reconciliation API",
        description="Reconcile vendor invoice weights against IoT-measured weights",
        version="v1",
        docs_url="/",
        generate_unique_id_function=_custom_generate_unique_id,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3002",
            "https://localhost:3002",
            "*",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info("Initializing database (mode-aware)")
    init_db()

    app.include_router(router, prefix="/v1", tags=["reconciliation"])

    return app


def _custom_generate_unique_id(route: APIRoute):
    return f"{route.tags[0]}-{route.name}"


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8002)
