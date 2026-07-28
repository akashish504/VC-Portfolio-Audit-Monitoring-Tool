"""
Company-scoped audit trail (``company_view_audit``) with pending file buffer.

- Real companies: rows in ``company_view_audit`` (``company_id`` = ``str(portfolio_companies.id)``).
- Placeholder / unassigned companies: file-scoped events append to ``files.pending_audit_log``.
- On tag (placeholder → real company): pending entries are promoted, then cleared.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CompanyViewAudit, Entity, File, PortfolioCompany
from src.exceptions.audit import CompanyAuditLoggingError
from src.services.company_audit_context import get_audit_actor_email

SYSTEM_ACTOR = "system"

_FINANCIAL_EDIT_SOURCE_LABELS: dict[str, str] = {
    "extracted": "As per AFS",
    "snowflake": "As per MIS",
}


def financial_edit_source_label(source: str) -> str:
    s = (source or "").strip().lower()
    return _FINANCIAL_EDIT_SOURCE_LABELS.get(s, s.replace("_", " ").title())


FINANCIAL_METRIC_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "pbt": "PBT",
    "pat": "PAT",
    "cash": "Cash",
    "debt": "Debt",
}

PORTFOLIO_FIELD_LABELS: dict[str, str] = {
    "name": "Company name",
    "review_stage": "Review stage",
    "review_cycle_id": "Review cycle",
    "contact_name": "Contact name",
    "contact_email_id": "Contact email",
    "audit_status": "Audit status",
    "scoped_in_for_audit": "Scoped in for audit",
    "exclusion_reason": "Exclusion reason",
    "fy_end": "FY end",
    "fy_end_date": "FY end date",
    "company_response": "Company response",
    "peak_xv_actionable": "Peak XV actionable",
}

ENTITY_FIELD_LABELS: dict[str, str] = {
    "name": "Entity name",
    "geolocation": "Geolocation",
    "entity_type": "Entity type",
    "review_cycle": "Review cycle",
    "status": "Status",
    "parent_entity_id": "Parent entity",
    "region": "Region",
    "is_parent": "Is parent",
}

ENTITY_ID_FIELDS = frozenset({"parent_entity_id", "entity_id"})


def is_unassigned_portfolio_company(company: PortfolioCompany | None) -> bool:
    if company is None:
        return False
    cid = (company.company_id or "").strip()
    return cid.startswith("sys-unassigned-")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe_value(value: Any) -> Any:
    """Coerce values stored in JSON audit ``meta`` columns."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [json_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    return str(value)


def json_safe_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: json_safe_value(v) for k, v in meta.items()}


def metric_values_differ(before: Any, after: Any) -> bool:
    if before is None and after is None:
        return False
    if before is None or after is None:
        return True
    try:
        return float(before) != float(after)
    except (TypeError, ValueError):
        return before != after


def format_audit_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return f"{float(value):,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, dict)):
        text = str(value)
        return text if len(text) <= 120 else f"{text[:117]}…"
    text = str(value).strip()
    return text if text else "—"


def humanize_field_name(field: str, *, entity_type: str = "portfolio_company") -> str:
    if entity_type == "entity":
        return ENTITY_FIELD_LABELS.get(field) or field.replace("_", " ").strip().title()
    return PORTFOLIO_FIELD_LABELS.get(field) or field.replace("_", " ").strip().title()


def field_change_action(*, label: str, before: Any, after: Any) -> str:
    return f"{label} updated from {format_audit_value(before)} → {format_audit_value(after)}"


def ensure_filename_in_action(file: File, action: str) -> str:
    """Ensure every file-scoped audit line names the file (not just its id)."""
    text = (action or "").strip()
    fname = (file.filename or "").strip()
    if not fname:
        return text or f"File activity (file id {file.id})"
    if f'"{fname}"' in text:
        return text
    return f'{text} — file "{fname}"' if text else f'File "{fname}" updated'


class CompanyAuditRecorder:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _actor(self, actor_email: Optional[str] = None) -> str:
        if actor_email:
            return actor_email.strip()
        ctx = get_audit_actor_email()
        return ctx if ctx else SYSTEM_ACTOR

    async def resolve_entity_label(
        self,
        entity_id: Optional[int],
        *,
        none_label: str = "Top level",
    ) -> str:
        if entity_id is None:
            return none_label
        ent = await self.db.get(Entity, entity_id)
        if ent and (ent.name or "").strip():
            return f'"{ent.name.strip()}"'
        return f"Unknown entity (id {entity_id})"

    async def _entity_field_change_action(
        self,
        *,
        field: str,
        before: Any,
        after: Any,
        subject_name: Optional[str],
    ) -> str:
        label = humanize_field_name(field, entity_type="entity")
        if field in ENTITY_ID_FIELDS:
            before_disp = await self.resolve_entity_label(
                before if isinstance(before, int) else None,
                none_label="Top level" if field == "parent_entity_id" else "—",
            )
            after_disp = await self.resolve_entity_label(
                after if isinstance(after, int) else None,
                none_label="Top level" if field == "parent_entity_id" else "—",
            )
            if field == "parent_entity_id" and subject_name:
                return (
                    f'Parent entity of "{subject_name}" updated from {before_disp} → {after_disp}'
                )
            return f"{label} updated from {before_disp} → {after_disp}"

        if subject_name and field != "name":
            return (
                f'{label} of "{subject_name}" updated from '
                f"{format_audit_value(before)} → {format_audit_value(after)}"
            )
        return field_change_action(label=label, before=before, after=after)

    async def _insert_company_row(
        self,
        *,
        portfolio_company_id: int,
        action: str,
        meta: dict[str, Any],
        user_id: str,
        occurred_at: Optional[str] = None,
    ) -> None:
        occurred = occurred_at or _utc_now_iso()
        payload = json_safe_meta(dict(meta))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        payload.setdefault("portfolio_company_id", portfolio_company_id)
        # ``company_view_audit.action`` is VARCHAR(255) in the database; long metric lines
        # (large amounts, long labels) must not fail the whole PATCH.
        action_stored = (action or "")[:255]
        row = CompanyViewAudit(
            id=str(uuid.uuid4()),
            user_id=user_id,
            company_id=str(portfolio_company_id),
            action=action_stored,
            meta=payload,
        )
        try:
            self.db.add(row)
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc

    async def _append_file_pending(
        self,
        file: File,
        *,
        action: str,
        meta: dict[str, Any],
        user_id: str,
    ) -> None:
        occurred = _utc_now_iso()
        payload = json_safe_meta(dict(meta))
        payload.setdefault("summary", action)
        payload.setdefault("occurred_at", occurred)
        payload.setdefault("file_id", file.id)
        entry = {
            "occurred_at": occurred,
            "user_id": user_id,
            "action": action,
            "meta": payload,
        }
        pending = list(file.pending_audit_log or [])
        pending.append(entry)
        file.pending_audit_log = pending
        try:
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc

    async def log_company(
        self,
        *,
        portfolio_company_id: int,
        action: str,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        company: PortfolioCompany | None = None,
    ) -> None:
        if company is None:
            company = await self.db.get(PortfolioCompany, portfolio_company_id)
        if is_unassigned_portfolio_company(company):
            return
        await self._insert_company_row(
            portfolio_company_id=portfolio_company_id,
            action=action,
            meta=meta or {},
            user_id=self._actor(actor_email),
        )

    async def log_file(
        self,
        file: File,
        *,
        action: str,
        meta: Optional[dict[str, Any]] = None,
        actor_email: Optional[str] = None,
        company: PortfolioCompany | None = None,
    ) -> None:
        action = ensure_filename_in_action(file, action)
        if company is None:
            company = await self.db.get(PortfolioCompany, file.portfolio_company_id)
        base_meta = dict(meta or {})
        base_meta.setdefault("file_id", file.id)
        base_meta.setdefault("filename", file.filename)
        user = self._actor(actor_email)
        if is_unassigned_portfolio_company(company):
            await self._append_file_pending(file, action=action, meta=base_meta, user_id=user)
            return
        await self._insert_company_row(
            portfolio_company_id=file.portfolio_company_id,
            action=action,
            meta=base_meta,
            user_id=user,
        )

    async def log_company_field_changes(
        self,
        *,
        portfolio_company_id: int,
        changes: dict[str, tuple[Any, Any]],
        entity_type: str = "portfolio_company",
        entity_id: Optional[int] = None,
        actor_email: Optional[str] = None,
        company: PortfolioCompany | None = None,
        edit_reason: Optional[str] = None,
    ) -> None:
        if not changes:
            return
        if company is None:
            company = await self.db.get(PortfolioCompany, portfolio_company_id)
        if is_unassigned_portfolio_company(company):
            return
        user = self._actor(actor_email)
        subject_name: Optional[str] = None
        if entity_type == "entity" and entity_id is not None:
            subject = await self.db.get(Entity, entity_id)
            if subject and (subject.name or "").strip():
                subject_name = subject.name.strip()
        for field, (before, after) in changes.items():
            if before == after:
                continue
            if entity_type == "entity":
                action = await self._entity_field_change_action(
                    field=field,
                    before=before,
                    after=after,
                    subject_name=subject_name,
                )
            else:
                label = humanize_field_name(field, entity_type=entity_type)
                action = field_change_action(label=label, before=before, after=after)
            meta: dict[str, Any] = {
                "entity_type": entity_type,
                "entity_id": entity_id or portfolio_company_id,
                "field": field,
                "before": before,
                "after": after,
            }
            er = (edit_reason or "").strip()
            if er:
                meta["edit_reason"] = er
            await self._insert_company_row(
                portfolio_company_id=portfolio_company_id,
                action=action,
                meta=meta,
                user_id=user,
            )

    async def log_financial_metric_change(
        self,
        *,
        portfolio_company_id: int,
        metric_key: str,
        before: Any,
        after: Any,
        source: str,
        entity_id: Optional[int] = None,
        row_id: Optional[int] = None,
        actor_email: Optional[str] = None,
        edit_reason: Optional[str] = None,
    ) -> None:
        label = FINANCIAL_METRIC_LABELS.get(metric_key, metric_key.replace("_", " ").title())
        source_label = financial_edit_source_label(source)
        action = f"{source_label} {label} updated from {format_audit_value(before)} → {format_audit_value(after)}"
        meta: dict[str, Any] = {
            "entity_type": "financial_data",
            "source": source,
            "metric": metric_key,
            "entity_id": entity_id,
            "row_id": row_id,
            "before": before,
            "after": after,
        }
        er = (edit_reason or "").strip()
        if er:
            meta["edit_reason"] = er
        await self.log_company(
            portfolio_company_id=portfolio_company_id,
            action=action,
            meta=meta,
            actor_email=actor_email,
        )

    async def promote_file_pending_on_tag(
        self,
        file: File,
        *,
        new_portfolio_company_id: int,
        entity_id: Optional[int],
        actor_email: Optional[str] = None,
    ) -> None:
        """Move ``pending_audit_log`` to ``company_view_audit`` when file is tagged to a real company."""
        pending = list(file.pending_audit_log or [])
        user = self._actor(actor_email)
        for entry in pending:
            if not isinstance(entry, dict):
                continue
            await self._insert_company_row(
                portfolio_company_id=new_portfolio_company_id,
                action=ensure_filename_in_action(
                    file, str(entry.get("action") or "File activity")
                ),
                meta=dict(entry.get("meta") or {}),
                user_id=str(entry.get("user_id") or user),
                occurred_at=str(entry.get("occurred_at") or _utc_now_iso()),
            )
        file.pending_audit_log = []
        entity_part = ""
        if entity_id is not None:
            ent_label = await self.resolve_entity_label(entity_id)
            entity_part = f" (entity {ent_label})"
        await self._insert_company_row(
            portfolio_company_id=new_portfolio_company_id,
            action=f'File "{file.filename}" tagged to this company{entity_part}',
            meta={
                "file_id": file.id,
                "filename": file.filename,
                "entity_id": entity_id,
                "event": "file.tagged",
            },
            user_id=user,
        )
        try:
            await self.db.flush()
        except Exception as exc:
            raise CompanyAuditLoggingError() from exc

    @staticmethod
    def snapshot_obj(obj: Any, fields: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields:
            if hasattr(obj, f):
                out[f] = getattr(obj, f)
        return out

    @staticmethod
    def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
        changes: dict[str, tuple[Any, Any]] = {}
        for key in after:
            if before.get(key) != after.get(key):
                changes[key] = (before.get(key), after.get(key))
        return changes
