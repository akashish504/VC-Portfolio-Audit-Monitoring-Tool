"""
Exception handlers for the application
"""
import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from src.exceptions.base import BaseAppException

logger = logging.getLogger(__name__)


async def app_exception_handler(request: Request, exc: BaseAppException):
    """
    Handle application exceptions
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "message": exc.message,
            "data": exc.data,
            "error_code": exc.error_code
        }
    )


async def global_exception_handler(request: Request, exc: Exception):
    """
    Handle all other exceptions — do not expose exception text to clients.
    """
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "message": "Internal server error",
            "data": None,
            "error_code": "internal_error",
        },
    )
