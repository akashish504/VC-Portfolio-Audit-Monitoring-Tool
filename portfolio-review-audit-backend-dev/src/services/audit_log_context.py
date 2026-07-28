"""Request-scoped audit routing: cycle adjustments vs company-detail (Option C)."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

REVIEW_CYCLE_ADJUSTMENTS = "review_cycle_adjustments"

_audit_log_context: ContextVar[Optional[str]] = ContextVar("audit_log_context", default=None)


def set_audit_log_context(value: Optional[str]) -> None:
    _audit_log_context.set((value or "").strip() or None)


def get_audit_log_context() -> Optional[str]:
    return _audit_log_context.get()


def clear_audit_log_context() -> None:
    _audit_log_context.set(None)


def is_review_cycle_adjustments_context() -> bool:
    return get_audit_log_context() == REVIEW_CYCLE_ADJUSTMENTS
