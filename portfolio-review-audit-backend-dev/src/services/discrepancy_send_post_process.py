"""After a successful Discrepancy Template email send, update discrepancy statuses and entity lifecycle timestamps."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    DraftEmail,
    EmailHistory,
    EmailTemplate,
    Entity,
    FinancialMetricReconciliation,
    ManualReconciliationQuery,
    PortfolioCompany,
)
from src.schema.portfolio import (
    CompanyReviewStage,
    ReconciliationStatus,
)
from src.services.company_audit_context import get_audit_actor_email
from src.services.company_audit_recorder import CompanyAuditRecorder
from src.services.email_template_config import (
    ConfigurationError,
    get_discrepancy_template_id,
)
from src.services.financial_data_extraction_sync import resolve_financial_review_cycle

logger = logging.getLogger(__name__)

# Sending a discrepancy/query email moves only the affected entities to "Query sent".
ENTITY_STATUS_QUERY_SENT = CompanyReviewStage.QUERY_SENT.value


def is_open_reconciliation_status(status: Optional[str]) -> bool:
    s = (status or "").strip()
    return not s or s == ReconciliationStatus.OPEN.value


def status_after_discrepancy_send(*, enable: bool) -> str:
    return (
        ReconciliationStatus.SENT.value
        if enable
        else ReconciliationStatus.NOT_SENT.value
    )


def _open_status_clause(column):
    return or_(
        column == ReconciliationStatus.OPEN.value,
        column.is_(None),
        func.trim(column) == "",
    )


async def template_name_for_id(db: AsyncSession, template_id: Optional[str]) -> Optional[str]:
    if not template_id:
        return None
    row = (
        await db.execute(select(EmailTemplate.template_name).where(EmailTemplate.id == template_id))
    ).scalar_one_or_none()
    return (row or "").strip() or None


async def _get_discrepancy_template_id_safe(db: AsyncSession) -> Optional[str]:
    """Return configured discrepancy template ID, or None (with a warning) if config is missing."""
    try:
        return await get_discrepancy_template_id(db)
    except ConfigurationError as exc:
        logger.warning("discrepancy template ID not configured: %s", exc)
        return None


async def is_discrepancy_template_id(db: AsyncSession, template_id: Optional[str]) -> bool:
    if not template_id:
        return False
    configured_id = await _get_discrepancy_template_id_safe(db)
    if configured_id:
        return template_id == configured_id
    # ConfigTable missing — fall back to name match (legacy path, logs a warning above)
    name = await template_name_for_id(db, template_id)
    return name == "Discrepancy Template"


async def resolve_company_review_cycle(db: AsyncSession, portfolio_company_id: int) -> Optional[str]:
    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc is None:
        return None
    rc = (pc.review_cycle_id or "").strip()
    return rc[:128] if rc else None


async def entity_ids_in_review_cycle(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> set[int]:
    rc = review_cycle.strip()
    if not rc:
        return set()
    entity_ids = list(
        (
            await db.execute(
                select(Entity.id).where(Entity.portfolio_company_id == portfolio_company_id)
            )
        ).scalars().all()
    )
    matched: set[int] = set()
    for eid in entity_ids:
        resolved = await resolve_financial_review_cycle(
            db,
            portfolio_company_id=portfolio_company_id,
            entity_id=int(eid),
        )
        if (resolved or "").strip() == rc:
            matched.add(int(eid))
    return matched


async def count_sendable_discrepancies(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> int:
    entity_ids = await entity_ids_in_review_cycle(
        db,
        portfolio_company_id=portfolio_company_id,
        review_cycle=review_cycle,
    )

    fin_count = (
        await db.execute(
            select(func.count(FinancialMetricReconciliation.id)).where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.review_cycle == review_cycle,
                FinancialMetricReconciliation.enable.is_(True),
                _open_status_clause(FinancialMetricReconciliation.status),
            )
        )
    ).scalar_one()

    manual_count = 0
    if entity_ids:
        manual_count = (
            await db.execute(
                select(func.count(ManualReconciliationQuery.id)).where(
                    ManualReconciliationQuery.portfolio_company_id == portfolio_company_id,
                    ManualReconciliationQuery.entity_id.in_(entity_ids),
                    ManualReconciliationQuery.enable.is_(True),
                    _open_status_clause(ManualReconciliationQuery.status),
                )
            )
        ).scalar_one()

    return int(fin_count or 0) + int(manual_count or 0)


async def assert_can_send_discrepancy_draft(db: AsyncSession, draft: DraftEmail) -> None:
    if not await is_discrepancy_template_id(db, draft.template_id):
        return
    if not draft.portfolio_company_id:
        raise ValueError("Draft missing portfolio_company_id")
    pc_id = int(draft.portfolio_company_id)
    review_cycle = await resolve_company_review_cycle(db, pc_id)
    if not review_cycle:
        raise ValueError("Company has no review cycle set")
    if await count_sendable_discrepancies(db, portfolio_company_id=pc_id, review_cycle=review_cycle) == 0:
        raise ValueError(
            "Cannot send: no enabled open discrepancies for this review cycle (To Be Sent = Yes)"
        )


async def _affected_entity_ids_for_open_discrepancies(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> set[int]:
    """Return entity IDs that have at least one open discrepancy row."""
    fin_entity_ids = set(
        (
            await db.execute(
                select(FinancialMetricReconciliation.entity_id).where(
                    FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                    FinancialMetricReconciliation.review_cycle == review_cycle,
                    _open_status_clause(FinancialMetricReconciliation.status),
                ).distinct()
            )
        ).scalars().all()
    )

    manual_entity_ids = set(
        (
            await db.execute(
                select(ManualReconciliationQuery.entity_id).where(
                    ManualReconciliationQuery.portfolio_company_id == portfolio_company_id,
                    _open_status_clause(ManualReconciliationQuery.status),
                ).distinct()
            )
        ).scalars().all()
    )

    return {int(e) for e in (fin_entity_ids | manual_entity_ids)}


async def check_duplicate_discrepancy_send(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    discrepancy_template_id: str,
    newly_affected_entity_ids: set[int],
) -> None:
    """Raise ValueError if any of the newly_affected_entity_ids were already included in a prior discrepancy send."""
    existing = list(
        (
            await db.execute(
                select(EmailHistory).where(
                    EmailHistory.portfolio_company_id == portfolio_company_id,
                    EmailHistory.template_id == discrepancy_template_id,
                    EmailHistory.is_inbound.is_(False),
                    EmailHistory.affected_entity_ids.is_not(None),
                )
            )
        ).scalars().all()
    )

    already_sent_ids: set[int] = set()
    for record in existing:
        if record.affected_entity_ids:
            already_sent_ids.update(int(e) for e in record.affected_entity_ids)

    overlap = newly_affected_entity_ids & already_sent_ids
    if not overlap:
        return

    # Fetch entity names for the error message
    entities = list(
        (
            await db.execute(
                select(Entity.id, Entity.name).where(Entity.id.in_(overlap))
            )
        ).all()
    )
    id_to_name = {row.id: row.name for row in entities}
    overlap_display = ", ".join(
        f"{eid} ({id_to_name.get(eid, 'unknown')})" for eid in sorted(overlap)
    )
    raise ValueError(
        f"Discrepancy email already sent for the following entities: {overlap_display}. "
        "Each entity's open discrepancies may only be emailed once per thread."
    )


async def stamp_email_history_discrepancy(
    db: AsyncSession,
    *,
    email_history_id: str,
    template_id: str,
    affected_entity_ids: list[int],
) -> None:
    """Back-fill template_id and affected_entity_ids onto an EmailHistory row."""
    row = await db.get(EmailHistory, email_history_id)
    if row is None:
        logger.warning("EmailHistory id=%s not found; cannot stamp lifecycle fields", email_history_id)
        return
    row.template_id = template_id
    row.affected_entity_ids = affected_entity_ids
    db.add(row)


async def apply_discrepancy_send_post_process(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    template_id: Optional[str],
    affected_entity_ids: Optional[list[int]] = None,
    email_history_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> None:
    if not await is_discrepancy_template_id(db, template_id):
        return

    review_cycle = await resolve_company_review_cycle(db, portfolio_company_id)
    if not review_cycle:
        logger.warning(
            "discrepancy send post-process skipped: no review_cycle_id company_id=%s",
            portfolio_company_id,
        )
        return

    # Use the draft snapshot when provided; fall back to recomputing only as a safety net.
    if affected_entity_ids is not None:
        affected_ids = set(affected_entity_ids)
    else:
        affected_ids = await _affected_entity_ids_for_open_discrepancies(
            db,
            portfolio_company_id=portfolio_company_id,
            review_cycle=review_cycle,
        )

    fin_rows = list(
        (
            await db.execute(
                select(FinancialMetricReconciliation).where(
                    FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                    FinancialMetricReconciliation.review_cycle == review_cycle,
                    _open_status_clause(FinancialMetricReconciliation.status),
                )
            )
        ).scalars().all()
    )

    manual_rows: list[ManualReconciliationQuery] = []
    if affected_ids:
        manual_rows = list(
            (
                await db.execute(
                    select(ManualReconciliationQuery).where(
                        ManualReconciliationQuery.portfolio_company_id == portfolio_company_id,
                        ManualReconciliationQuery.entity_id.in_(affected_ids),
                        _open_status_clause(ManualReconciliationQuery.status),
                    )
                )
            ).scalars().all()
        )

    actor = actor_email or get_audit_actor_email()
    recorder = CompanyAuditRecorder(db)
    status_updates = 0

    for row in fin_rows:
        if not is_open_reconciliation_status(row.status):
            continue
        new_status = status_after_discrepancy_send(enable=bool(row.enable))
        if row.status == new_status:
            continue
        before_status = row.status
        row.status = new_status
        db.add(row)
        status_updates += 1
        await recorder.log_company(
            portfolio_company_id=portfolio_company_id,
            action=(
                f"Reconciliation row status updated from {before_status or '—'} \u2192 {new_status}"
                f" (metric: {row.metric_key or '—'}, row id: {row.id})"
            ),
            meta={
                "event": "financial_metric_reconciliation.status_changed",
                "entity_id": row.entity_id,
                "row_id": row.id,
                "metric_key": row.metric_key,
                "field": "status",
                "before": before_status,
                "after": new_status,
            },
            actor_email=actor,
        )

    for row in manual_rows:
        if not is_open_reconciliation_status(row.status):
            continue
        new_status = status_after_discrepancy_send(enable=bool(row.enable))
        if row.status == new_status:
            continue
        before_status = row.status
        row.status = new_status
        db.add(row)
        status_updates += 1
        await recorder.log_company(
            portfolio_company_id=portfolio_company_id,
            action=(
                f"Manual reconciliation query status updated from {before_status or '—'} \u2192 {new_status}"
                f" (row id: {row.id})"
            ),
            meta={
                "event": "manual_reconciliation_query.status_changed",
                "entity_id": row.entity_id,
                "row_id": row.id,
                "field": "status",
                "before": before_status,
                "after": new_status,
            },
            actor_email=actor,
        )

    # Stamp discrepancy_email_sent_at on only the affected entities
    now = datetime.now(timezone.utc)
    if affected_ids:
        affected_entities = list(
            (
                await db.execute(
                    select(Entity).where(Entity.id.in_(affected_ids))
                )
            ).scalars().all()
        )
        for ent in affected_entities:
            if ent.discrepancy_email_sent_at is None:
                ent.discrepancy_email_sent_at = now
                db.add(ent)

    # Move only the affected entities to "Query sent" (state lives on entities.status).
    if affected_ids:
        affected_entities_for_status = list(
            (
                await db.execute(select(Entity).where(Entity.id.in_(affected_ids)))
            ).scalars().all()
        )
        for ent in affected_entities_for_status:
            before_status = ent.status
            if before_status == ENTITY_STATUS_QUERY_SENT:
                continue
            ent.status = ENTITY_STATUS_QUERY_SENT
            db.add(ent)
            await recorder.log_company_field_changes(
                portfolio_company_id=portfolio_company_id,
                changes={"status": (before_status, ENTITY_STATUS_QUERY_SENT)},
                entity_type="entity",
                entity_id=ent.id,
                actor_email=actor,
            )

    # Back-fill template_id + affected_entity_ids onto the EmailHistory row
    if email_history_id and template_id and affected_ids:
        await stamp_email_history_discrepancy(
            db,
            email_history_id=email_history_id,
            template_id=template_id,
            affected_entity_ids=sorted(affected_ids),
        )

    await recorder.log_company(
        portfolio_company_id=portfolio_company_id,
        action=(
            f'Discrepancy query email sent — updated {status_updates} discrepancy row(s), '
            f'affected entities → "{ENTITY_STATUS_QUERY_SENT}": {sorted(affected_ids)}'
        ),
        meta={
            "event": "discrepancy_email.sent",
            "review_cycle": review_cycle,
            "status_updates": status_updates,
            "affected_entity_ids": sorted(affected_ids),
        },
        actor_email=actor,
    )
    await db.commit()
