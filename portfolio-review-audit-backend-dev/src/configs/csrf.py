"""
CSRF Protection Configuration
"""
import os
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from fastapi import Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.configs.env import settings

class CsrfSettings(BaseModel):
    """CSRF protection settings"""
    secret_key: str = settings.CSRF_SECRET_KEY
    cookie_samesite: str = "none"
    cookie_secure: bool = True


# CSRF Protect instance
csrf_protect = CsrfProtect()


def configure_csrf(app):
    """Configure CSRF protection for the FastAPI app"""
    
    # Configure CSRF protection settings
    @csrf_protect.load_config
    def get_csrf_config():
        return CsrfSettings()
    
    # Add exception handler for CSRF errors
    @app.exception_handler(CsrfProtectError)
    async def csrf_protect_exception_handler(request: Request, exc: CsrfProtectError):
        raise HTTPException(
            status_code=403,
            detail={"message": "CSRF token validation failed"},
        )
    
    return csrf_protect


def is_csrf_exempt_request(request: Request, exempt_paths: list = None) -> bool:
    """
    Check if a request should be exempt from CSRF protection
    
    Args:
        request: FastAPI request object
        exempt_paths: List of paths to exempt
        
    Returns:
        bool: True if request should be exempt
    """
    # Safe HTTP methods are always exempt
    # if request.method in ["GET", "HEAD", "OPTIONS"]:
    #     return True
    
    # Check if path is in exempt list
    if exempt_paths and request.url.path in exempt_paths:
        return True
    
    return False 