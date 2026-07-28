"""
Base exceptions for the application
"""
from fastapi import status
from typing import Any, Optional

class BaseAppException(Exception):
    """
    Base exception for all application exceptions
    """
    def __init__(
        self, 
        message: str, 
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: Any = None,
        error_code: str = "bad_request"
    ):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.error_code = error_code
        super().__init__(self.message)

class NotFoundException(BaseAppException):
    """
    Exception for not found entities
    """
    def __init__(self, message: str = "Entity not found", data: Any = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            data=data,
            error_code="not_found"
        )

class UnauthorizedException(BaseAppException):
    """
    Exception for unauthorized access
    """
    def __init__(self, message: str = "Unauthorized", data: Any = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            data=data,
            error_code="unauthorized"
        ) 