"""
Pydantic validators for input sanitization
"""
from pydantic import field_validator
from typing import Any, Optional
from .sanitizer import sanitize_text, sanitize_html


def sanitize_text_field(value: Any, strip_html: bool = True) -> Optional[str]:
    """
    Pydantic validator to sanitize text fields
    
    Usage:
        @field_validator('field_name')
        @classmethod
        def validate_field(cls, v):
            return sanitize_text_field(v, strip_html=True)
    """
    if value is None:
        return None
    
    if not isinstance(value, str):
        value = str(value)
    
    return sanitize_text(value, strip_html=strip_html)


def sanitize_html_field(value: Any) -> Optional[str]:
    """
    Pydantic validator to sanitize HTML fields
    
    Usage:
        @field_validator('field_name')
        @classmethod
        def validate_field(cls, v):
            return sanitize_html_field(v)
    """
    if value is None:
        return None
    
    if not isinstance(value, str):
        value = str(value)
    
    return sanitize_html(value)
