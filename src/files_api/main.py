from textwrap import dedent
import logging
import pydantic
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

from files_api.errors import (
    handle_broad_exceptions,
    handle_pydantic_validation_errors,
)
from files_api.routers.files import router as files_router
from files_api.routers.invoices import router as invoices_router
from files_api.routers.health import router as health_router
from files_api.config.settings import Settings
from fastapi import Depends
from database.local import init_db

# Set up logging
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a FastAPI application."""
    settings = settings or Settings()

    """Create a FastAPI application."""
    settings = settings or Settings()

    app = FastAPI(
        title="Files API",
        summary="Store vendor files",
        version="v1",  # a fancier version would read the semver from pkg metadata
        description=dedent(
            """\
        ![Maintained by](https://img.shields.io/badge/Maintained%20by-MLOps%20Club-05998B?style=for-the-badge)

        | Helpful Links | Notes |
        | --- | --- |
        | [FastAPI Documentation](https://fastapi.tiangolo.com/) | |
        | [Learn to make "badges"](https://shields.io/) | Example: <img alt="Awesome Badge" src="https://img.shields.io/badge/Awesome-😎-blueviolet?style=for-the-badge"> |
        """
        ),
        docs_url="/",  # its easier to find the docs when they live on the base url
        generate_unique_id_function=custom_generate_unique_id,
    )

    # Add CORS middleware with expanded settings to work with frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],  # Allow your frontend
        allow_credentials=True,
        allow_methods=["*"],  # Allow all methods
        allow_headers=["*"],  # Allow all headers
    )
    app.state.settings = settings
    logger.info("creating db")
    init_db()

    app.include_router(files_router, prefix="/v1", tags=["files"])
    app.include_router(invoices_router, prefix="/v1", tags=["invoices"])
    app.include_router(health_router, tags=["health"])

    app.add_exception_handler(
        exc_class_or_status_code=pydantic.ValidationError,
        handler=handle_pydantic_validation_errors,
    )
    app.middleware("http")(handle_broad_exceptions)

    return app


def custom_generate_unique_id(route: APIRoute):
    """
    Generate prettier `operationId`s in the OpenAPI schema.

    These become the function names in generated client SDKs.
    """
    return f"{route.tags[0]}-{route.name}"


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)