"""
Review cycle auto-provisioning: append next cycle yearly and clone company shells.

Cycle id convention: ``CY24-FY25`` (Jun 2024 → May 2025). Cron requires ≥2 existing cycles.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import CompanyViewAudit, PortfolioCompany, ReviewCycle, ReviewCycleViewAudit
from src.services.company_audit_recorder import is_unassigned_portfolio_company

logger = logging.getLogger(__name__)

CYCLE_ID_RE = re.compile(r"^CY(\d{2})-FY(\d{2})$", re.IGNORECASE)

CORE_METADATA_COLUMNS: tuple[str, ...] = (
    "company_id",
    "name",
    "contact_name",
    "contact_email_id",
    "contact_contact_id",
    "investment_stage",
    "investment_type",
    "partner_email",
    "prepcreator_audit_email",
    "reviewer_audit_email",
    "fund",
    "investment_lead",
    "company_stage",
    "geography",
    "ownership_pct",
    "cost",
    "fmv",
    "position_is_unique",
    "consolidated_ownership_pct",
    "consolidated_cost",
    "consolidated_fmv",
    "company_category_1",
    "company_category_2",
    "fy_end",
    "fy_end_date",
)


def parse_cycle_id(cycle_id: str) -> Optional[tuple[int, int]]:
    m = CYCLE_ID_RE.match((cycle_id or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def format_cycle_id(cy: int, fy: int) -> str:
    return f"CY{cy:02d}-FY{fy:02d}"


def format_cycle_display_name(cy: int, fy: int) -> str:
    return f"CY {cy:02d} - FY {fy:02d}"


def cycle_window_dates(cy: int, fy: int) -> tuple[datetime, datetime]:
    """CY24-FY25 → Jun 2024 through end of May 2025 (UTC)."""
    start = datetime(2000 + cy, 6, 1, tzinfo=timezone.utc)
    end = datetime(2000 + fy, 5, 31, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def cycle_sort_key(rc: ReviewCycle) -> datetime:
    if rc.starts_at is not None:
        dt = rc.starts_at
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    parsed = parse_cycle_id(rc.id)
    if parsed:
        cy, _fy = parsed
        return datetime(2000 + cy, 6, 1, tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _log_scheduler_audit(db: Session, *, action: str, review_cycle_id: Optional[str], meta: dict[str, Any]) -> None:
    occurred = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = dict(meta or {})
    payload.setdefault("summary", action)
    payload.setdefault("occurred_at", occurred)
    payload.setdefault("search_text", action)
    payload.setdefault("actor", "scheduler")
    if review_cycle_id:
        payload.setdefault("review_cycle_id", review_cycle_id)
    db.add(
        ReviewCycleViewAudit(
            id=str(uuid.uuid4()),
            user_id="scheduler",
            review_cycle_id=review_cycle_id,
            action=action[:255],
            meta=payload,
        )
    )


def _shell_from_source(source: PortfolioCompany, *, new_cycle_id: str) -> PortfolioCompany:
    data = {col: getattr(source, col) for col in CORE_METADATA_COLUMNS}
    return PortfolioCompany(
        **data,
        review_cycle_id=new_cycle_id,
        org_chart_file_id=None,
        scoped_in_for_audit=None,
        exclusion_reason=None,
        due_date=None,
        audit_status=None,
        auditor=None,
        tentative_completion_date=None,
        company_response=None,
        peak_xv_actionable=None,
        extra_data=dict(source.extra_data or {}),
    )


def provision_next_review_cycle(session: Session) -> dict[str, Any]:
    """
    Append the next review cycle and clone company shells from the immediately previous cycle.

    No-op when fewer than two cycles exist, when the next cycle already exists, or when the
    latest cycle id cannot be parsed into CYxx-FYyy.
    """
    cycles = session.execute(select(ReviewCycle)).scalars().all()
    if len(cycles) < 2:
        msg = f"Skipping review cycle provision: need at least 2 cycles, found {len(cycles)}"
        logger.info(msg)
        return {"status": "skipped", "reason": "insufficient_cycles", "cycle_count": len(cycles)}

    ordered = sorted(cycles, key=cycle_sort_key)
    previous = ordered[-1]
    parsed = parse_cycle_id(previous.id)
    if not parsed:
        msg = f"Skipping review cycle provision: cannot parse latest cycle id {previous.id!r}"
        logger.warning(msg)
        return {"status": "skipped", "reason": "unparseable_latest_cycle", "latest_cycle_id": previous.id}

    cy, fy = parsed
    next_cy, next_fy = cy + 1, fy + 1
    next_id = format_cycle_id(next_cy, next_fy)
    next_name = format_cycle_display_name(next_cy, next_fy)

    existing_next = session.get(ReviewCycle, next_id)
    if existing_next is not None:
        msg = f"Review cycle {next_id} already exists — idempotent skip"
        logger.info(msg)
        return {"status": "skipped", "reason": "already_exists", "review_cycle_id": next_id}

    starts_at, ends_at = cycle_window_dates(next_cy, next_fy)
    new_cycle = ReviewCycle(
        id=next_id,
        name=next_name,
        status="upcoming",
        starts_at=starts_at,
        ends_at=ends_at,
        meta={"label": next_name, "source": "scheduler", "cloned_from": previous.id},
    )
    session.add(new_cycle)
    session.flush()

    source_rows = (
        session.execute(
            select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == previous.id)
        )
        .scalars()
        .all()
    )

    cloned = 0
    skipped_unassigned = 0
    for row in source_rows:
        if is_unassigned_portfolio_company(row):
            skipped_unassigned += 1
            continue
        new_shell = _shell_from_source(row, new_cycle_id=next_id)
        session.add(new_shell)
        session.flush()
        # audit_status is intentionally reset to None on each new cycle shell
        if row.audit_status is not None:
            occurred = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            session.add(CompanyViewAudit(
                id=str(uuid.uuid4()),
                user_id="scheduler",
                company_id=str(new_shell.id),
                action=(
                    f"Audit status reset from \"{row.audit_status}\" → — "
                    f"(new cycle shell provisioned from {previous.id})"
                )[:255],
                meta={
                    "entity_type": "portfolio_company",
                    "entity_id": new_shell.id,
                    "field": "audit_status",
                    "before": row.audit_status,
                    "after": None,
                    "event": "portfolio_company.audit_status_reset_on_provision",
                    "source_cycle_id": previous.id,
                    "target_cycle_id": next_id,
                    "occurred_at": occurred,
                },
            ))
        cloned += 1

    action = (
        f'Review cycle "{next_name}" provisioned from "{previous.name or previous.id}" '
        f"({cloned} companies cloned)"
    )
    _log_scheduler_audit(
        session,
        action=action,
        review_cycle_id=next_id,
        meta={
            "event": "review_cycle.provisioned",
            "previous_cycle_id": previous.id,
            "companies_cloned": cloned,
            "skipped_unassigned": skipped_unassigned,
        },
    )
    session.commit()

    logger.info(
        "Provisioned review cycle %s (%s companies cloned from %s)",
        next_id,
        cloned,
        previous.id,
    )
    return {
        "status": "created",
        "review_cycle_id": next_id,
        "review_cycle_name": next_name,
        "previous_cycle_id": previous.id,
        "companies_cloned": cloned,
        "skipped_unassigned": skipped_unassigned,
    }
