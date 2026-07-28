"""Audit trail for review-cycle adjustments and parameter threshold saves."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    FinancialExtractionMappingViewAudit,
    ParameterThresholdViewAudit,
    ReviewCycleViewAudit,
    SnowflakePRFinancialMappingViewAudit,
)
from src.exceptions.audit import CompanyAuditLoggingError
from src.services.company_audit_context import get_audit_actor_email
from src.services.company_audit_recorder import (
    PORTFOLIO_FIELD_LABELS,
    format_audit_value,
    humanize_field_name,
    json_safe_meta,
)

SYSTEM_ACTOR = "system"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(actor_email: Optional[str] = None) -> str:
    if actor_email:
        return actor_email.strip()
    ctx = get_audit_actor_email()
    return ctx if ctx else SYSTEM_ACTOR


def cycle_field_label(field: str) -> str:
    return PORTFOLIO_FIELD_LABELS.get(field) or humanize_field_name(field, entity_type="portfolio_company")


def build_cycle_field_action(
    *,
    company_name: str,
    field: str,
    before: Any,
    after: Any,
    review_cycle_id: Optional[str] = None,
    cycle_name: Optional[str] = None,
) -> str:
    label = cycle_field_label(field)
    display = cycle_name or review_cycle_id
    cycle_part = f' (cycle {display})' if display else ""
    return (
        f'{label} for "{company_name}" updated from '
        f"{format_audit_value(before)} → {format_audit_value(after)}{cycle_part}"
    )


class ReviewCycleAuditRecorder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        *,
        action: str,
        review_cycle_id: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        search_text: Optional[str] = None,
    ) -> None:
        occurred = _utc_now_iso()
        payload = json_safe_meta(dict(meta or {}))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        if review_cycle_id:
            payload.setdefault("review_cycle_id", review_cycle_id)
        if search_text:
            payload.setdefault("search_text", search_text)
        else:
            payload.setdefault("search_text", action)
        row = ReviewCycleViewAudit(
            id=str(uuid.uuid4()),
            user_id=_actor(actor_email),
            review_cycle_id=review_cycle_id,
            action=(action or "")[:255],
            meta=payload,
        )
        try:
            self.db.add(row)
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc


class ParameterThresholdAuditRecorder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        *,
        action: str,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        search_text: Optional[str] = None,
    ) -> None:
        occurred = _utc_now_iso()
        payload = json_safe_meta(dict(meta or {}))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        payload.setdefault("search_text", search_text or action)
        row = ParameterThresholdViewAudit(
            id=str(uuid.uuid4()),
            user_id=_actor(actor_email),
            action=(action or "")[:255],
            meta=payload,
        )
        try:
            self.db.add(row)
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc


class FinancialMetricMappingAuditRecorder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        *,
        action: str,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        search_text: Optional[str] = None,
    ) -> None:
        occurred = _utc_now_iso()
        payload = json_safe_meta(dict(meta or {}))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        payload.setdefault("search_text", search_text or action)
        row = FinancialExtractionMappingViewAudit(
            id=str(uuid.uuid4()),
            user_id=_actor(actor_email),
            action=(action or "")[:255],
            meta=payload,
        )
        try:
            self.db.add(row)
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc


class SnowflakePRFinancialMappingAuditRecorder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        *,
        action: str,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        search_text: Optional[str] = None,
    ) -> None:
        occurred = _utc_now_iso()
        payload = json_safe_meta(dict(meta or {}))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        payload.setdefault("search_text", search_text or action)
        row = SnowflakePRFinancialMappingViewAudit(
            id=str(uuid.uuid4()),
            user_id=_actor(actor_email),
            action=(action or "")[:255],
            meta=payload,
        )
        try:
            self.db.add(row)
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc
