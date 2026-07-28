from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AppException(Exception):
    """Base application exception."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail

async def app_exception_handler(request: Request, exc: AppException):
    """Handle custom application exceptions."""
    logger.error(f"AppException: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle Pydantic validation errors."""
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"]
        })
    logger.error(f"Validation error: {errors}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation failed", "errors": errors}
    )

async def integrity_error_handler(request: Request, exc: IntegrityError):
    """Handle database integrity errors."""
    logger.error(f"Database integrity error: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Database integrity error - possibly duplicate entry"}
    )

async def generic_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions."""
    logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"}
    )
