"""Request-scoped actor email for company audit logging (matches /user/get-user)."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_audit_actor_email: ContextVar[Optional[str]] = ContextVar("audit_actor_email", default=None)


def set_audit_actor_email(email: Optional[str]) -> None:
    _audit_actor_email.set((email or "").strip() or None)


def get_audit_actor_email() -> Optional[str]:
    return _audit_actor_email.get()


def clear_audit_actor_email() -> None:
    _audit_actor_email.set(None)
