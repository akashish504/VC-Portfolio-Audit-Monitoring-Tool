"""Audit logging failures — business transactions must roll back."""
from __future__ import annotations

from fastapi import status

from src.exceptions.base import BaseAppException

AUDIT_LOGGING_FAILURE_MESSAGE = (
    "Audit logging failed. No changes were saved. Please contact an administrator."
)


class CompanyAuditLoggingError(BaseAppException):
    def __init__(self, message: str = AUDIT_LOGGING_FAILURE_MESSAGE):
        super().__init__(
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="audit_logging_failed",
        )
