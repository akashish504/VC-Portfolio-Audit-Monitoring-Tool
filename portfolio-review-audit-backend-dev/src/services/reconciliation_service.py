"""Centralized financial-metric reconciliation.

Normalizes extracted (AFS) and Snowflake (MIS) metric values to
``PortfolioCompany.currency`` using historical FX as of the company's
``fy_end_date`` at 12:00:00, stores comparison fields on
``FinancialMetricReconciliation``, and flips ``Entity.status`` from
"In review" to "Discrepancy identified" when the threshold is breached.

All metric-changing write paths (audit-file extraction, manual entry, Snowflake
sync, dashboard upload) must call :func:`reconcile_metric_values` after mutating
the underlying data so the reconciliation row stays consistent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.file_filters import exclude_org_chart_uploads_clause
from src.db.models import (
    Entity,
    File,
    FinancialDataSnowflake,
    FinancialMetricReconciliation,
    PortfolioCompany,
)
from src.schema.portfolio import CompanyReviewStage, EntityReviewStatus
from src.services.company_audit_recorder import SYSTEM_ACTOR, CompanyAuditRecorder
from src.services.financial_reconciliation import (
    RECON_METRIC_KEYS,
    fetch_canonical_snowflake_row,
    fetch_usd_to_inr_rate,
    is_metric_comparable,
    load_variance_threshold_maps,
    metric_variance_breach,
    resolve_mis_for_recon_row,
    row_variance_breach,
    snowflake_currency,
)
from src.services.fx_service import FxConversionUnavailable, fetch_historical_rate

logger = logging.getLogger(__name__)


# Entity status canonical values (mirror src/schema/portfolio.py EntityReviewStatus).
# State now lives on entities.status using the full review-stage vocabulary.
ENTITY_STATUS_IN_REVIEW = CompanyReviewStage.IN_REVIEW.value
ENTITY_STATUS_DISCREPANCY_IDENTIFIED = CompanyReviewStage.DISCREPANCY_IDENTIFIED.value


# discrepancy_status column values (informational, set on FinancialMetricReconciliation).
STATUS_WITHIN = "within_threshold"
STATUS_BREACHED = "breached_threshold"
STATUS_INCOMPLETE_NO_TARGET = "incomplete_no_target_currency"
STATUS_INCOMPLETE_NO_FX = "incomplete_no_fx"
STATUS_INCOMPLETE_NO_FY_END = "incomplete_no_fy_end"
STATUS_INCOMPLETE_NO_SOURCE = "incomplete_no_source_currency"
STATUS_INCOMPLETE_ONE_SIDED = "incomplete_one_sided"

_DEFAULT_PCT_THRESHOLD = 0.005  # mirror metric_variance_breach default

# entities.status values that metric-based auto-transitions are allowed to overwrite.
_METRIC_TRANSITION_ELIGIBLE = frozenset({
    CompanyReviewStage.IN_REVIEW.value,
    CompanyReviewStage.DISCREPANCY_IDENTIFIED.value,
    CompanyReviewStage.NO_DISCREPANCY_IDENTIFIED.value,
    CompanyReviewStage.NOT_COMPARABLE.value,
})

# Pre-review states that may advance to a metric-derived outcome — but ONLY once the
# entity actually has uploaded financials this cycle. This closes the gap where an
# entity that received financials (and shows a discrepancy) was never moved into review
# (e.g. file-attach didn't fire, ran out of order, or the entity sat in a dead-zone
# state). A genuinely not-applicable entity with no financials is never auto-changed.
_PRE_REVIEW_ON_FINANCIALS = frozenset({
    EntityReviewStatus.SCOPED_IN.value,
    EntityReviewStatus.FINANCIALS_TO_BE_RECEIVED.value,
    EntityReviewStatus.NOT_APPLICABLE.value,
})


async def _entity_has_uploaded_financials(
    db: AsyncSession, entity_id: int, review_cycle: str
) -> bool:
    """True when the entity has an uploaded audited-financials file (not an org-chart)
    for this cycle — the signal that its review has effectively started."""
    sub = select(File.id).where(
        File.entity_id == entity_id,
        File.review_cycle_id == review_cycle,
        exclude_org_chart_uploads_clause(),
    )
    return bool((await db.execute(select(exists(sub)))).scalar())


async def _update_entity_status_from_recon(
    db: AsyncSession,
    *,
    entity_id: int,
    review_cycle: str,
) -> None:
    """Set Entity.status based on live MIS vs AFS breach checks for this entity+cycle.

    Updates if the entity is in a metric-transition-eligible state, OR in a pre-review
    state once it actually has uploaded financials (so a received-but-stuck entity still
    advances). Never touches later workflow states (query/approved) or out-of-scope ones.

    Uses the same ``row_variance_breach`` / ``is_metric_comparable`` rules as the
    discrepancies dashboard so entity stage never advances on stale rows or partial
    data. Decision:
      - Any comparable row breaches thresholds         → Discrepancy identified
      - At least one comparable row, none breach       → No discrepancy identified
      - No comparable rows (all one-sided/incomplete)  → Not comparable
    """
    ent = await db.get(Entity, entity_id)
    if ent is None:
        return
    current = (ent.status or "").strip()
    eligible = current in _METRIC_TRANSITION_ELIGIBLE
    if not eligible and current in _PRE_REVIEW_ON_FINANCIALS:
        eligible = await _entity_has_uploaded_financials(db, entity_id, review_cycle)
    if not eligible:
        return

    recon_rows = (
        await db.execute(
            select(FinancialMetricReconciliation).where(
                FinancialMetricReconciliation.entity_id == entity_id,
                FinancialMetricReconciliation.review_cycle == review_cycle,
            )
        )
    ).scalars().all()

    if not recon_rows:
        return

    pct_by_metric, abs_by_metric, pct_by_label, abs_by_label = (
        await load_variance_threshold_maps(db)
    )
    usd_inr = await fetch_usd_to_inr_rate(db)
    sf = await fetch_canonical_snowflake_row(
        db,
        portfolio_company_id=ent.portfolio_company_id,
        review_cycle=review_cycle,
    )

    has_comparable = False
    any_incomplete = False
    any_breached = False

    for row in recon_rows:
        mis_amount, mis_currency = resolve_mis_for_recon_row(row, sf)
        if not is_metric_comparable(mis_amount, row.afs_amount):
            any_incomplete = True
            continue
        has_comparable = True
        if row_variance_breach(
            row,
            mis_amount=mis_amount,
            mis_currency=mis_currency,
            pct_by_metric=pct_by_metric,
            abs_by_metric=abs_by_metric,
            pct_by_label=pct_by_label,
            abs_by_label=abs_by_label,
            usd_to_inr_rate=usd_inr,
        ):
            any_breached = True

    if any_breached:
        new_status = CompanyReviewStage.DISCREPANCY_IDENTIFIED.value
    elif has_comparable:
        # At least one metric comparable and none breached → no discrepancy,
        # regardless of whether other metrics are incomplete (missing MIS or AFS side).
        new_status = CompanyReviewStage.NO_DISCREPANCY_IDENTIFIED.value
    elif any_incomplete:
        new_status = CompanyReviewStage.NOT_COMPARABLE.value
    else:
        return

    if (ent.status or "").strip() != new_status:
        before_status = ent.status
        ent.status = new_status
        db.add(ent)
        await CompanyAuditRecorder(db).log_company_field_changes(
            portfolio_company_id=ent.portfolio_company_id,
            changes={"status": (before_status, new_status)},
            entity_type="entity",
            entity_id=ent.id,
            actor_email=SYSTEM_ACTOR,
        )


def _norm_ccy(v: Optional[str]) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if len(s) == 3 and s.isalpha():
        return s
    return None


def _parse_fy_end_date(raw: Optional[str]) -> Optional[date]:
    """PortfolioCompany.fy_end_date is stored as 'YYYY-MM-DD' (spec)."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


async def _convert(
    db: AsyncSession,
    *,
    amount: Optional[float],
    src_ccy: Optional[str],
    tgt_ccy: str,
    fy_end_dt: Optional[datetime],
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Return (normalized_amount, fx_rate_used, error_status).

    - If src == tgt, rate = 1.0, no FX lookup.
    - If FX lookup fails, return (None, None, STATUS_INCOMPLETE_NO_FX) — never falls back to live FX.
    """
    if amount is None:
        return None, None, None
    if src_ccy is None:
        return None, None, STATUS_INCOMPLETE_NO_SOURCE
    if src_ccy == tgt_ccy:
        return float(amount), 1.0, None
    if fy_end_dt is None:
        return None, None, STATUS_INCOMPLETE_NO_FY_END
    try:
        rate, _ts = await fetch_historical_rate(
            db, from_currency=src_ccy, to_currency=tgt_ccy, at=fy_end_dt
        )
    except FxConversionUnavailable:
        logger.warning(
            "reconcile: historical FX unavailable %s->%s at %s",
            src_ccy, tgt_ccy, fy_end_dt.isoformat(),
        )
        return None, None, STATUS_INCOMPLETE_NO_FX
    except Exception:  # defensive: never let recon crash the caller
        logger.exception(
            "reconcile: unexpected FX failure %s->%s at %s",
            src_ccy, tgt_ccy, fy_end_dt.isoformat(),
        )
        return None, None, STATUS_INCOMPLETE_NO_FX
    return round(float(amount) * float(rate), 4), float(rate), None


def _compute_diff(
    extracted: Optional[float], snowflake: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """Return (absolute_difference, percentage_difference). Both None unless both inputs present."""
    if extracted is None or snowflake is None:
        return None, None
    abs_diff = abs(float(extracted) - float(snowflake))
    if float(snowflake) == 0.0:
        # If both zero, diff is 0 and within threshold; if SF is zero and AFS non-zero,
        # treat as 100% to trigger breach.
        if abs_diff == 0.0:
            return 0.0, 0.0
        return abs_diff, 100.0
    pct = ((float(extracted) - float(snowflake)) / abs(float(snowflake))) * 100.0
    return abs_diff, pct


async def _maybe_update_entity_status(
    db: AsyncSession, *, entity_id: int, breached: bool
) -> None:
    """Flip Entity.status from 'In review' to 'Discrepancy identified' on breach.

    Conservative: only mutates from the exact In review state — never regresses
    later workflow states.
    """
    if not breached:
        return
    ent = await db.get(Entity, entity_id)
    if ent is None:
        return
    current = (ent.status or "").strip()
    if current == ENTITY_STATUS_IN_REVIEW:
        before_status = ent.status
        ent.status = ENTITY_STATUS_DISCREPANCY_IDENTIFIED
        db.add(ent)
        await CompanyAuditRecorder(db).log_company_field_changes(
            portfolio_company_id=ent.portfolio_company_id,
            changes={"status": (before_status, ENTITY_STATUS_DISCREPANCY_IDENTIFIED)},
            entity_type="entity",
            entity_id=ent.id,
            actor_email=SYSTEM_ACTOR,
        )


async def reconcile_metric_values(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    entity_id: int,
    review_cycle: str,
    metric_key: str,
    frequency: Optional[str] = None,
) -> Optional[FinancialMetricReconciliation]:
    """Recompute normalization + comparison for a single metric row.

    Idempotent: looks up the existing ``FinancialMetricReconciliation`` row by the
    unique constraint ``(portfolio_company_id, entity_id, review_cycle, metric_key)``
    and updates it in place (creating one if absent). Never inserts duplicates.

    Returns the row, or ``None`` if it could not be located/created.
    """
    mk = (metric_key or "").strip().lower()
    rc = (review_cycle or "").strip()[:128]
    if not mk or not rc:
        return None

    # MIS-only sync must not create reconciliation rows for entities with no AFS file.
    from src.services.financial_data_extraction_sync import resolve_primary_afs_file

    if await resolve_primary_afs_file(db, entity_id=entity_id, review_cycle=rc) is None:
        return None

    # Load company + raw values
    company = await db.get(PortfolioCompany, portfolio_company_id)
    if company is None:
        return None
    target_ccy = _norm_ccy(getattr(company, "currency", None))
    fy_end_d = _parse_fy_end_date(getattr(company, "fy_end_date", None))

    row = await _get_or_create_row(
        db,
        portfolio_company_id=portfolio_company_id,
        entity_id=entity_id,
        review_cycle=rc,
        metric_key=mk,
        frequency=frequency,
    )
    if row is None:
        return None

    # Sources
    extracted_raw: Optional[float] = (
        None if row.afs_amount is None else float(row.afs_amount)
    )
    extracted_src = _norm_ccy(row.afs_currency)

    sf = await fetch_canonical_snowflake_row(
        db, portfolio_company_id=portfolio_company_id, review_cycle=rc
    )
    snowflake_raw: Optional[float] = None
    snowflake_src: Optional[str] = None
    if sf is not None:
        v = getattr(sf, mk, None)
        if v is not None:
            try:
                snowflake_raw = float(v)
            except (TypeError, ValueError):
                snowflake_raw = None
        snowflake_src = snowflake_currency(sf)

    if target_ccy is None:
        present_ccys = {
            c for c, amt in ((extracted_src, extracted_raw), (snowflake_src, snowflake_raw))
            if amt is not None and c
        }
        if len(present_ccys) == 1:
            target_ccy = next(iter(present_ccys))

    row.extracted_source_currency = extracted_src
    row.snowflake_source_currency = snowflake_src
    row.target_currency = target_ccy
    row.fx_date = fy_end_d
    row.last_reconciled_at = datetime.now(timezone.utc)

    # Early-exits with explicit incomplete states.
    if target_ccy is None:
        row.discrepancy_status = STATUS_INCOMPLETE_NO_TARGET
        row.extracted_value_normalized = None
        row.snowflake_value_normalized = None
        row.extracted_fx_rate = None
        row.snowflake_fx_rate = None
        row.absolute_difference = None
        row.percentage_difference = None
        row.is_within_threshold = None
        await db.flush()
        return row

    needs_fx = (
        (extracted_raw is not None and extracted_src is not None and extracted_src != target_ccy)
        or (snowflake_raw is not None and snowflake_src is not None and snowflake_src != target_ccy)
    )
    if needs_fx and fy_end_d is None:
        row.discrepancy_status = STATUS_INCOMPLETE_NO_FY_END
        row.extracted_value_normalized = None
        row.snowflake_value_normalized = None
        row.extracted_fx_rate = None
        row.snowflake_fx_rate = None
        row.absolute_difference = None
        row.percentage_difference = None
        row.is_within_threshold = None
        await db.flush()
        return row

    fy_end_ts = datetime.combine(fy_end_d, time(12, 0, 0)) if fy_end_d is not None else None

    ext_norm, ext_rate, ext_err = await _convert(
        db, amount=extracted_raw, src_ccy=extracted_src,
        tgt_ccy=target_ccy, fy_end_dt=fy_end_ts,
    )
    sf_norm, sf_rate, sf_err = await _convert(
        db, amount=snowflake_raw, src_ccy=snowflake_src,
        tgt_ccy=target_ccy, fy_end_dt=fy_end_ts,
    )

    row.extracted_value_normalized = ext_norm
    row.snowflake_value_normalized = sf_norm
    row.extracted_fx_rate = ext_rate
    row.snowflake_fx_rate = sf_rate

    # Threshold lookup (per-metric; mirror dashboard / enable-toggle rules)
    pct_by_metric, abs_by_metric, pct_by_label, abs_by_label = (
        await load_variance_threshold_maps(db)
    )
    from src.services.company_audit_recorder import FINANCIAL_METRIC_LABELS

    label = FINANCIAL_METRIC_LABELS.get(mk, mk.title())
    pct_thresh = pct_by_metric.get(mk, pct_by_label.get(label, _DEFAULT_PCT_THRESHOLD))
    row.threshold_value = float(pct_thresh)

    # Incomplete states (some piece missing)
    if ext_err is not None or sf_err is not None:
        row.discrepancy_status = ext_err or sf_err
        row.absolute_difference = None
        row.percentage_difference = None
        row.is_within_threshold = None
        await db.flush()
        return row

    if ext_norm is None or sf_norm is None or not is_metric_comparable(
        snowflake_raw, extracted_raw
    ):
        row.discrepancy_status = STATUS_INCOMPLETE_ONE_SIDED
        row.absolute_difference = None
        row.percentage_difference = None
        row.is_within_threshold = None
        await db.flush()
        return row

    abs_diff, pct_diff = _compute_diff(ext_norm, sf_norm)
    row.absolute_difference = abs_diff
    row.percentage_difference = pct_diff

    # Breach uses raw MIS/AFS amounts — same rules as the discrepancies dashboard.
    compare_ccy = (
        _norm_ccy(row.afs_currency)
        or snowflake_src
        or target_ccy
    )
    usd_inr = await fetch_usd_to_inr_rate(db)
    breached = metric_variance_breach(
        snowflake_raw,
        extracted_raw,
        metric_key=mk,
        pct_by_metric=pct_by_metric,
        abs_by_metric=abs_by_metric,
        pct_by_label=pct_by_label,
        abs_by_label=abs_by_label,
        currency=compare_ccy,
        usd_to_inr_rate=usd_inr,
    )
    row.is_within_threshold = not breached
    row.discrepancy_status = STATUS_BREACHED if breached else STATUS_WITHIN

    await db.flush()
    return row


async def _get_or_create_row(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    entity_id: int,
    review_cycle: str,
    metric_key: str,
    frequency: Optional[str],
) -> Optional[FinancialMetricReconciliation]:
    row = (
        await db.execute(
            select(FinancialMetricReconciliation).where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.entity_id == entity_id,
                FinancialMetricReconciliation.review_cycle == review_cycle,
                FinancialMetricReconciliation.metric_key == metric_key,
            )
        )
    ).scalars().first()
    if row is not None:
        if frequency is not None and not row.frequency:
            row.frequency = frequency
        return row
    row = FinancialMetricReconciliation(
        portfolio_company_id=portfolio_company_id,
        entity_id=entity_id,
        review_cycle=review_cycle,
        metric_key=metric_key,
        category="financial",
        type=metric_key,
        frequency=frequency,
        extra_data={},
    )
    db.add(row)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        row = (
            await db.execute(
                select(FinancialMetricReconciliation).where(
                    FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                    FinancialMetricReconciliation.entity_id == entity_id,
                    FinancialMetricReconciliation.review_cycle == review_cycle,
                    FinancialMetricReconciliation.metric_key == metric_key,
                )
            )
        ).scalars().first()
    return row


async def reconcile_all_metrics_for_entity_cycle(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    entity_id: int,
    review_cycle: str,
    frequency: Optional[str] = None,
) -> None:
    """Recompute reconciliation for every standard metric on one (entity, cycle)."""
    for mk in RECON_METRIC_KEYS:
        await reconcile_metric_values(
            db,
            portfolio_company_id=portfolio_company_id,
            entity_id=entity_id,
            review_cycle=review_cycle,
            metric_key=mk,
            frequency=frequency,
        )
    await _update_entity_status_from_recon(
        db,
        entity_id=entity_id,
        review_cycle=review_cycle,
    )


async def reconcile_all_metrics_for_company_cycle(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> None:
    """Recompute reconciliation for all (entity, metric) pairs in one (company, cycle).

    Called from Snowflake sync paths where one MIS write affects every entity's row.
    Stage update fires per-entity after all metrics for that entity are reconciled.
    """
    rc = (review_cycle or "").strip()[:128]
    if not rc:
        return
    from src.services.financial_data_extraction_sync import resolve_primary_afs_file

    entities = (
        await db.execute(
            select(Entity.id).where(Entity.portfolio_company_id == portfolio_company_id)
        )
    ).scalars().all()
    for eid in entities:
        if await resolve_primary_afs_file(db, entity_id=int(eid), review_cycle=rc) is None:
            continue
        await reconcile_all_metrics_for_entity_cycle(
            db,
            portfolio_company_id=portfolio_company_id,
            entity_id=int(eid),
            review_cycle=rc,
        )
