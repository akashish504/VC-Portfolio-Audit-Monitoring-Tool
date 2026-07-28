"""
Yearly review-cycle rollover (runs ~3:00 AM IST on 1 June).

Determines source/target cycles from the current IST date, then copies *metadata only*
from the just-previous cycle into the new cycle:

  * PortfolioCompany — name, contact, email, fund, investment_lead; review_stage="Not applicable".
  * Entity — full entity tree (parent/child), workflow fields reset.
  * Org chart — PortfolioCompany.org_chart_file_id + the linked File row (cloned for the
    new cycle so each cycle owns its own file row; storage_uri preserved).
  * PortfolioCompanyMetadata — every source-cycle row, keyed by
    (fund, deal_id, strategy, review_cycle_id). Identity/classification/financial
    columns copy verbatim; audit status/stage/scoping columns reset to defaults.

Idempotent: re-running updates existing target-cycle rows in place rather than
duplicating. Uses one transaction; partial failures roll back.

Cycle id convention (kept consistent with review_cycle_provisioning): ``CYxx-FYyy``
covering Jun 1 of CY through May 31 of FY (IST/UTC). On 1 June 2026 IST the target
cycle is CY26-FY27 and the source is CY25-FY26.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

import pytz
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    CompanyViewAudit,
    Entity,
    File,
    PortfolioCompany,
    PortfolioCompanyMetadata,
    ReviewCycle,
    ReviewCycleViewAudit,
)
from src.services.company_audit_recorder import is_unassigned_portfolio_company
from src.services.review_cycle_provisioning import (
    CYCLE_ID_RE,
    cycle_window_dates,
    format_cycle_display_name,
    format_cycle_id,
    parse_cycle_id,
)

logger = logging.getLogger(__name__)

TZ_IST = pytz.timezone("Asia/Kolkata")

# Audit state lives on entities now. Rolled-over entities start fresh at "Not applicable".
ROLLOVER_ENTITY_STATUS = "Not applicable"

# Only these PortfolioCompany columns carry over to the new cycle.
PORTFOLIO_COMPANY_METADATA_FIELDS: tuple[str, ...] = (
    "name",
    "contact_name",
    "contact_email_id",
    "contact_contact_id",
    "partner_email",
    "fund",
    "investment_lead",
)

# Entity identity/metadata fields copied to the new cycle.  Workflow/lifecycle
# fields (status, email-lifecycle timestamps) are intentionally NOT copied — the
# new cycle starts clean.
ENTITY_METADATA_FIELDS: tuple[str, ...] = (
    "name",
    "geolocation",
    "entity_type",
    "region",
    "is_parent",
)

# PortfolioCompanyMetadata rollover.  Natural key = (fund, deal_id, strategy,
# review_cycle_id); all source-cycle rows are carried into the new cycle.
# Identity / classification / financial columns copy verbatim; audit
# status / stage / scoping columns reset to defaults so the new cycle starts clean.
PCM_COPY_FIELDS: tuple[str, ...] = (
    "fund",
    "deal_id",
    "deal_id_for_analysis",
    "deal_id_for_analysis_and_strategy",
    "deal_name",
    "strategy",
    "il_main",
    "sector_l1",
    "sector_l2",
    "geo_l1",
    "geo_l2",
    "cost",
    "distributed",
    "proceeds",
    "fmv",
    "ownership",
    "unique_by_company_id",
    "unique_by_company_id_strategy",
    "consolidated_cost",
    "consolidated_fmv",
    "fy_end",
    "comments",
)

# Audit workflow columns reset on the new cycle (NOT copied from source).
PCM_RESET_DEFAULTS: dict[str, Any] = {
    "scoping_for_audit": False,
    "reason_for_exclusion": None,
    "deal_level_stage_1": None,
    "deal_level_stage_2": None,
    "auditor": None,
    "category_of_auditor": None,
    "tentative_audit_completion_date": None,
    "category": None,
    "py_audit_status": None,
}


# ---------------------------------------------------------------------------
# Date / cycle resolution
# ---------------------------------------------------------------------------


def ist_today(now: Optional[datetime] = None) -> date:
    """Return today's date in IST.  ``now`` override is for tests."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TZ_IST).date()


def resolve_rollover_cycle_ids(today_ist: date) -> tuple[str, str]:
    """
    Given an IST date, return ``(source_cycle_id, target_cycle_id)``.

    The "current" review-cycle window runs Jun 1 → May 31.  On/after Jun 1 the
    current cycle becomes the *new* target; the cycle that just ended is the
    source.  Before Jun 1 the not-yet-ended cycle is still active.
    """
    year = today_ist.year
    if today_ist >= date(year, 6, 1):
        target_cy = year % 100
    else:
        target_cy = (year - 1) % 100
    target_fy = (target_cy + 1) % 100
    source_cy = (target_cy - 1) % 100
    source_fy = target_cy
    return format_cycle_id(source_cy, source_fy), format_cycle_id(target_cy, target_fy)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_fields(source: Any, target: Any, fields: tuple[str, ...]) -> None:
    for f in fields:
        setattr(target, f, getattr(source, f))


def _ensure_review_cycle(session: Session, cycle_id: str, *, source_cycle_id: str) -> ReviewCycle:
    """Fetch the target ReviewCycle row, creating it if missing."""
    existing = session.get(ReviewCycle, cycle_id)
    if existing is not None:
        return existing

    parsed = parse_cycle_id(cycle_id)
    if parsed is None:
        raise ValueError(f"Cannot derive window for non-standard cycle id {cycle_id!r}")
    cy, fy = parsed
    starts_at, ends_at = cycle_window_dates(cy, fy)
    name = format_cycle_display_name(cy, fy)
    rc = ReviewCycle(
        id=cycle_id,
        name=name,
        status="upcoming",
        starts_at=starts_at,
        ends_at=ends_at,
        meta={
            "label": name,
            "source": "rollover_scheduler",
            "cloned_from": source_cycle_id,
        },
    )
    session.add(rc)
    session.flush()
    return rc


def _clone_org_chart_file(session: Session, source_file: File, *, new_company_id: int) -> File:
    """Clone an org-chart File row for the target cycle (shares storage_uri)."""
    new_file = File(
        portfolio_company_id=new_company_id,
        entity_id=None,
        review_cycle_id=None,  # filled by caller if needed
        filename=source_file.filename,
        content_type=source_file.content_type,
        storage_uri=source_file.storage_uri,
        size_bytes=source_file.size_bytes,
        status=source_file.status,
        tags=list(source_file.tags or []),
        pending_audit_log=[],
        entity_detached_acknowledged=False,
    )
    session.add(new_file)
    session.flush()
    return new_file


def _rollover_company_metadata(
    session: Session,
    *,
    source_cycle_id: str,
    target_cycle_id: str,
) -> tuple[int, int]:
    """
    Copy every PortfolioCompanyMetadata row from the source cycle into the target
    cycle.  Upsert keyed on the (fund, deal_id, strategy, review_cycle_id) unique
    constraint: update the matching target row in place, otherwise insert.

    Identity/classification/financial columns copy verbatim; audit
    status/stage/scoping columns reset to ``PCM_RESET_DEFAULTS``.  Returns
    ``(created, updated)``.
    """
    source_rows = (
        session.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.review_cycle_id == source_cycle_id
            )
        )
        .scalars()
        .all()
    )

    # Preload existing target-cycle rows once, keyed on the natural key, instead
    # of a SELECT per source row.
    existing_by_key: dict[tuple[Any, Any, Any], PortfolioCompanyMetadata] = {}
    for row in (
        session.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.review_cycle_id == target_cycle_id
            )
        )
        .scalars()
        .all()
    ):
        existing_by_key.setdefault((row.fund, row.deal_id, row.strategy), row)

    created = 0
    updated = 0
    for src in source_rows:
        existing = existing_by_key.get((src.fund, src.deal_id, src.strategy))
        if existing is not None:
            _copy_fields(src, existing, PCM_COPY_FIELDS)
            for field, default in PCM_RESET_DEFAULTS.items():
                setattr(existing, field, default)
            updated += 1
        else:
            row = PortfolioCompanyMetadata(
                review_cycle_id=target_cycle_id,
                **{f: getattr(src, f) for f in PCM_COPY_FIELDS},
                **PCM_RESET_DEFAULTS,
            )
            session.add(row)
            created += 1

    session.flush()
    return created, updated


def _log_audit(
    session: Session,
    *,
    action: str,
    review_cycle_id: Optional[str],
    meta: dict[str, Any],
) -> None:
    occurred = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = dict(meta or {})
    payload.setdefault("summary", action)
    payload.setdefault("occurred_at", occurred)
    payload.setdefault("search_text", action)
    payload.setdefault("actor", "scheduler")
    if review_cycle_id:
        payload.setdefault("review_cycle_id", review_cycle_id)
    session.add(
        ReviewCycleViewAudit(
            id=str(uuid.uuid4()),
            user_id="scheduler",
            review_cycle_id=review_cycle_id,
            action=action[:255],
            meta=payload,
        )
    )


# ---------------------------------------------------------------------------
# Core rollover
# ---------------------------------------------------------------------------


def rollover_review_cycle(
    session: Session,
    *,
    now: Optional[datetime] = None,
    source_cycle_id: Optional[str] = None,
    target_cycle_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Roll over from the previous review cycle to the new one.

    Either pass ``source_cycle_id`` / ``target_cycle_id`` explicitly (useful for
    tests and manual reruns) or let the function derive them from the current
    IST date.

    Idempotent: re-runs update existing target-cycle rows in place.
    """
    today = ist_today(now)
    if source_cycle_id is None or target_cycle_id is None:
        derived_source, derived_target = resolve_rollover_cycle_ids(today)
        source_cycle_id = source_cycle_id or derived_source
        target_cycle_id = target_cycle_id or derived_target

    logger.info(
        "rollover: ist_today=%s source=%s target=%s",
        today.isoformat(),
        source_cycle_id,
        target_cycle_id,
    )

    if not CYCLE_ID_RE.match(source_cycle_id) or not CYCLE_ID_RE.match(target_cycle_id):
        raise ValueError(
            f"Non-standard cycle ids: source={source_cycle_id!r} target={target_cycle_id!r}"
        )

    source_cycle = session.get(ReviewCycle, source_cycle_id)
    if source_cycle is None:
        msg = f"Source review cycle {source_cycle_id} not found"
        logger.warning(msg)
        return {
            "status": "skipped",
            "reason": "source_cycle_missing",
            "source_cycle_id": source_cycle_id,
            "target_cycle_id": target_cycle_id,
        }

    target_cycle = _ensure_review_cycle(session, target_cycle_id, source_cycle_id=source_cycle_id)
    target_created = target_cycle.meta and target_cycle.meta.get("cloned_from") == source_cycle_id

    # --- pull source data ---------------------------------------------------
    source_companies = (
        session.execute(
            select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == source_cycle_id)
        )
        .scalars()
        .all()
    )

    # Preload all existing target-cycle companies once (one query instead of a
    # SELECT per source company — critical over a remote DB).  Grouped by
    # company_id so the >1 ambiguity guard is preserved.
    existing_targets_by_company_id: dict[str, list[PortfolioCompany]] = defaultdict(list)
    for row in (
        session.execute(
            select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == target_cycle_id)
        )
        .scalars()
        .all()
    ):
        existing_targets_by_company_id[row.company_id].append(row)

    companies_created = 0
    companies_updated = 0
    entities_created = 0
    entities_updated = 0
    org_charts_created = 0
    org_charts_updated = 0
    skipped_unassigned = 0
    errors: list[str] = []

    # --- Phase 1: upsert PortfolioCompany rows, flushed once as a batch ------
    pairs: list[tuple[PortfolioCompany, PortfolioCompany]] = []  # (source, target)
    for src_company in source_companies:
        if is_unassigned_portfolio_company(src_company):
            skipped_unassigned += 1
            continue

        # 1. PortfolioCompany ------------------------------------------------
        target_matches = existing_targets_by_company_id.get(src_company.company_id, [])
        if len(target_matches) > 1:
            msg = (
                f"Ambiguous target rows for company_id={src_company.company_id} "
                f"cycle={target_cycle_id}: {len(target_matches)} matches"
            )
            logger.error(msg)
            errors.append(msg)
            continue

        if target_matches:
            tgt_company = target_matches[0]
            _copy_fields(src_company, tgt_company, PORTFOLIO_COMPANY_METADATA_FIELDS)
            companies_updated += 1
        else:
            tgt_company = PortfolioCompany(
                company_id=src_company.company_id,
                review_cycle_id=target_cycle_id,
                extra_data={},
                **{f: getattr(src_company, f) for f in PORTFOLIO_COMPANY_METADATA_FIELDS},
            )
            session.add(tgt_company)
            companies_created += 1
        pairs.append((src_company, tgt_company))

    session.flush()  # batch-assigns ids to all newly-created companies at once

    # --- Bulk-preload entities + org-chart files for every company ----------
    src_company_ids = [s.id for s, _ in pairs]
    tgt_company_ids = [t.id for _, t in pairs]

    src_entities_by_company: dict[int, list[Entity]] = defaultdict(list)
    if src_company_ids:
        for e in (
            session.execute(select(Entity).where(Entity.portfolio_company_id.in_(src_company_ids)))
            .scalars()
            .all()
        ):
            src_entities_by_company[e.portfolio_company_id].append(e)

    tgt_entities_by_company: dict[int, list[Entity]] = defaultdict(list)
    if tgt_company_ids:
        for e in (
            session.execute(select(Entity).where(Entity.portfolio_company_id.in_(tgt_company_ids)))
            .scalars()
            .all()
        ):
            tgt_entities_by_company[e.portfolio_company_id].append(e)

    src_file_ids = {s.org_chart_file_id for s, _ in pairs if s.org_chart_file_id}
    src_files_by_id: dict[int, File] = {}
    if src_file_ids:
        src_files_by_id = {
            f.id: f
            for f in session.execute(select(File).where(File.id.in_(src_file_ids))).scalars().all()
        }
    tgt_file_ids = {t.org_chart_file_id for _, t in pairs if t.org_chart_file_id}
    tgt_files_by_id: dict[int, File] = {}
    if tgt_file_ids:
        tgt_files_by_id = {
            f.id: f
            for f in session.execute(select(File).where(File.id.in_(tgt_file_ids))).scalars().all()
        }

    # --- Phase 2: entities + org charts per company (in-memory lookups) ------
    for src_company, tgt_company in pairs:
        # 2. Entities (preserve parent/child via id remapping) --------------
        src_entities = src_entities_by_company.get(src_company.id, [])

        existing_by_name: dict[str, Entity] = {}
        for e in tgt_entities_by_company.get(tgt_company.id, []):
            existing_by_name.setdefault(e.name, e)

        id_map: dict[int, Entity] = {}  # source.id -> target Entity
        # First pass: upsert entities without parent link.
        for src_e in src_entities:
            tgt_e = existing_by_name.get(src_e.name)
            if tgt_e is None:
                tgt_e = Entity(
                    portfolio_company_id=tgt_company.id,
                    review_cycle=target_cycle_id,
                    extra_data={},
                )
                session.add(tgt_e)
                entities_created += 1
            else:
                entities_updated += 1
            _copy_fields(src_e, tgt_e, ENTITY_METADATA_FIELDS)
            tgt_e.review_cycle = target_cycle_id
            # Workflow / lifecycle fields explicitly reset on the new cycle.
            # status is the audit state of record; new cycle starts at "Not applicable".
            before_status = tgt_e.status
            tgt_e.status = ROLLOVER_ENTITY_STATUS
            tgt_e.discrepancy_email_sent_at = None
            tgt_e.reminder_1_sent_at = None
            tgt_e.reminder_2_sent_at = None
            tgt_e.first_reply_received_at = None
            tgt_e.resolved_at = None
            tgt_e.parent_entity_id = None
            session.flush()
            id_map[src_e.id] = tgt_e
            if before_status != ROLLOVER_ENTITY_STATUS:
                occurred = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                session.add(CompanyViewAudit(
                    id=str(uuid.uuid4()),
                    user_id="scheduler",
                    company_id=str(tgt_company.id),
                    action=(
                        f'Status of "{tgt_e.name or tgt_e.id}" updated from '
                        f"{before_status or '—'} \u2192 {ROLLOVER_ENTITY_STATUS} (cycle rollover)"
                    )[:255],
                    meta={
                        "entity_type": "entity",
                        "entity_id": tgt_e.id,
                        "field": "status",
                        "before": before_status,
                        "after": ROLLOVER_ENTITY_STATUS,
                        "event": "entity.status_rollover_reset",
                        "source_cycle_id": source_cycle_id,
                        "target_cycle_id": target_cycle_id,
                        "occurred_at": occurred,
                    },
                ))

        # Second pass: wire parent_entity_id using the id_map.
        for src_e in src_entities:
            if src_e.parent_entity_id is None:
                continue
            parent_tgt = id_map.get(src_e.parent_entity_id)
            if parent_tgt is None:
                # Parent missing (e.g. cross-company link); skip silently.
                continue
            id_map[src_e.id].parent_entity_id = parent_tgt.id

        # 3. Org chart (PortfolioCompany.org_chart_file_id → File) ---------
        if src_company.org_chart_file_id:
            src_file = src_files_by_id.get(src_company.org_chart_file_id)
            if src_file is not None:
                if tgt_company.org_chart_file_id:
                    existing_file = tgt_files_by_id.get(tgt_company.org_chart_file_id)
                    if existing_file is not None:
                        existing_file.filename = src_file.filename
                        existing_file.content_type = src_file.content_type
                        existing_file.storage_uri = src_file.storage_uri
                        existing_file.size_bytes = src_file.size_bytes
                        existing_file.tags = list(src_file.tags or [])
                        existing_file.status = src_file.status
                        existing_file.review_cycle_id = target_cycle_id
                        org_charts_updated += 1
                    else:
                        new_file = _clone_org_chart_file(
                            session, src_file, new_company_id=tgt_company.id
                        )
                        new_file.review_cycle_id = target_cycle_id
                        tgt_company.org_chart_file_id = new_file.id
                        org_charts_created += 1
                else:
                    new_file = _clone_org_chart_file(
                        session, src_file, new_company_id=tgt_company.id
                    )
                    new_file.review_cycle_id = target_cycle_id
                    tgt_company.org_chart_file_id = new_file.id
                    org_charts_created += 1

    session.flush()

    # --- PortfolioCompanyMetadata (keyed by fund/deal_id/strategy, not company) ---
    metadata_created, metadata_updated = _rollover_company_metadata(
        session,
        source_cycle_id=source_cycle_id,
        target_cycle_id=target_cycle_id,
    )

    summary = {
        "status": "ok",
        "ist_today": today.isoformat(),
        "source_cycle_id": source_cycle_id,
        "target_cycle_id": target_cycle_id,
        "target_cycle_created": bool(target_created),
        "companies_created": companies_created,
        "companies_updated": companies_updated,
        "entities_created": entities_created,
        "entities_updated": entities_updated,
        "org_charts_created": org_charts_created,
        "org_charts_updated": org_charts_updated,
        "metadata_created": metadata_created,
        "metadata_updated": metadata_updated,
        "skipped_unassigned": skipped_unassigned,
        "errors": errors,
    }
    if errors:
        summary["status"] = "partial"

    _log_audit(
        session,
        action=(
            f"Review cycle rollover {source_cycle_id} → {target_cycle_id} "
            f"(companies +{companies_created}/~{companies_updated}, "
            f"entities +{entities_created}/~{entities_updated}, "
            f"org_charts +{org_charts_created}/~{org_charts_updated}, "
            f"metadata +{metadata_created}/~{metadata_updated})"
        ),
        review_cycle_id=target_cycle_id,
        meta={"event": "review_cycle.rollover", **summary},
    )

    session.commit()

    logger.info("rollover finished: %s", summary)
    return summary
