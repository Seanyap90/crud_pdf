import pydantic
from fastapi import (
    Request,
    status,
)
from fastapi.responses import JSONResponse


# fastapi docs on middlewares: https://fastapi.tiangolo.com/tutorial/middleware/
async def handle_broad_exceptions(request: Request, call_next):
    """Handle any exception that goes unhandled by a more specific exception handler."""
    try:
        return await call_next(request)
    except Exception:  # pylint: disable=broad-except
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )


# fastapi docs on error handlers: https://fastapi.tiangolo.com/tutorial/handling-errors/
# pylint: disable=unused-argument
async def handle_pydantic_validation_errors(request: Request, exc: pydantic.ValidationError) -> JSONResponse:
    errors = exc.errors()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": [
                {
                    "msg": error["msg"],
                    "input": error["input"],
                }
                for error in errors
            ]
        },
    )