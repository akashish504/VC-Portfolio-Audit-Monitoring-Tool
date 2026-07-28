"""
Financial metric reconciliation: live MIS from Snowflake, variance / breach helpers, email/export.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import Integer, Select, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import FinancialDataSnowflake, FinancialMetricReconciliation, ParameterThreshold

logger = logging.getLogger(__name__)


RECON_METRIC_KEYS: tuple[str, ...] = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")

_LABEL_TO_METRIC_KEY: dict[str, str] = {
    "revenue": "revenue",
    "ebitda": "ebitda",
    "pbt": "pbt",
    "pat": "pat",
    "cash": "cash",
    "debt": "debt",
}


def resolve_threshold_metric_key(
    row_key: str,
    value_metric_key: Any = None,
) -> Optional[str]:
    """Map a ParameterThreshold row key to a reconciliation metric slug.

    Accepts all forms used in settings / the UI:
      - snake_case row keys: ``revenue``, ``debt``
      - display labels: ``Revenue``, ``Debt``, ``EBITDA``
      - explicit ``value.metric_key`` when present
    """
    if isinstance(value_metric_key, str) and value_metric_key.strip():
        mk = value_metric_key.strip().lower()
        if mk != "currency" and mk in RECON_METRIC_KEYS:
            return mk
    slug = (row_key or "").strip().lower().replace(" ", "_")
    if slug in RECON_METRIC_KEYS:
        return slug
    return _LABEL_TO_METRIC_KEY.get((row_key or "").strip().lower())


def _snowflake_payload(row: FinancialDataSnowflake) -> dict[str, Any]:
    p = row.payload
    return dict(p) if isinstance(p, dict) else {}


def _snowflake_metric_value(row: FinancialDataSnowflake, metric: str) -> Any:
    v = getattr(row, metric, None)
    if v is not None:
        return v
    return _snowflake_payload(row).get(metric)


def _snowflake_company_id(row: FinancialDataSnowflake) -> Optional[int]:
    raw = getattr(row, "portfolio_company_id", None)
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    payload = _snowflake_payload(row)
    pc = payload.get("portfolio_company_id")
    if pc is None:
        return None
    try:
        return int(pc)
    except (TypeError, ValueError):
        return None


def snowflake_currency(row: FinancialDataSnowflake) -> Optional[str]:
    merged = _snowflake_payload(row)
    currency = (getattr(row, "currency", None) or merged.get("currency") or "") or ""
    cur = currency.strip().upper()[:8]
    return cur or None


def _coerce_optional_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_metric_comparable(mis_val: Any, afs_val: Any) -> bool:
    """True when MIS and AFS can be compared — mirrors the discrepancies dashboard.

    Missing values and one-sided zeros (e.g. MIS=0 with AFS≠0) are treated as not
    comparable so they never drive entity stage transitions.
    """
    if mis_val is None or afs_val is None:
        return False
    try:
        mis_f = float(mis_val)
        afs_f = float(afs_val)
    except (TypeError, ValueError):
        return False
    # Treat zero as not comparable — matches the dashboard, which labels MIS=0 rows
    # as "Not Comparable" and excludes them from entity stage decisions.
    if mis_f == 0.0 or afs_f == 0.0:
        return False
    return True


def resolve_mis_for_recon_row(
    row: FinancialMetricReconciliation,
    sf: Optional[FinancialDataSnowflake],
) -> tuple[Optional[float], Optional[str]]:
    """MIS amounts always come from company-level ``FinancialDataSnowflake``, not recon columns."""
    if sf is None:
        return None, None
    mk = (row.metric_key or "").strip().lower()
    return _coerce_optional_float(_snowflake_metric_value(sf, mk)), snowflake_currency(sf)


def _pick_canonical_snowflake(
    rows: list[FinancialDataSnowflake],
    *,
    portfolio_company_id: int,
    review_cycle: str,
) -> Optional[FinancialDataSnowflake]:
    rc = review_cycle.strip()
    candidates = [r for r in rows if _snowflake_company_id(r) == portfolio_company_id]
    if not candidates:
        return None
    exact = [r for r in candidates if (r.review_cycle or "").strip() == rc]
    pool = exact if exact else [r for r in candidates if not (r.review_cycle or "").strip()]
    if not pool:
        return None
    return max(pool, key=lambda r: (r.review_cycle is not None, r.id or 0))


async def fetch_canonical_snowflake_map(
    db: AsyncSession,
    keys: set[tuple[int, str]],
) -> dict[tuple[int, str], Optional[FinancialDataSnowflake]]:
    """Batch-resolve canonical company-level Snowflake MIS rows for (portfolio_company_id, review_cycle)."""
    if not keys:
        return {}
    pc_ids = {pc_id for pc_id, _ in keys}
    payload_company = FinancialDataSnowflake.payload["portfolio_company_id"].as_string()
    company_match = or_(
        FinancialDataSnowflake.portfolio_company_id.in_(pc_ids),
        cast(payload_company, Integer).in_(pc_ids),
    )
    stmt = select(FinancialDataSnowflake).where(
        company_match,
        FinancialDataSnowflake.entity_id.is_(None),
    )
    all_rows = list((await db.execute(stmt)).scalars().all())
    return {
        key: _pick_canonical_snowflake(
            all_rows,
            portfolio_company_id=key[0],
            review_cycle=key[1],
        )
        for key in keys
    }


async def load_variance_threshold_maps(db: AsyncSession) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
]:
    """Mirror ``PortfolioService.generate_discrepancies`` threshold extraction."""
    from src.services.company_audit_recorder import FINANCIAL_METRIC_LABELS

    thresh_rows = (await db.execute(select(ParameterThreshold))).scalars().all()
    pct_by_metric: dict[str, float] = {}
    abs_by_metric: dict[str, float] = {}
    pct_by_label: dict[str, float] = {}
    abs_by_label: dict[str, float] = {}
    for r in thresh_rows:
        label = (r.key or "").strip()
        if not label:
            continue
        val = r.value or {}
        mk = val.get("metric_key")
        if isinstance(mk, str) and mk.strip().lower() == "currency":
            continue
        if label.lower() == "currency":
            continue
        pct = val.get("percent_threshold")
        abs_t = val.get("absolute_threshold")
        pct_f = float(pct) if isinstance(pct, (int, float)) else None
        abs_f = float(abs_t) if isinstance(abs_t, (int, float)) else None
        alias = resolve_threshold_metric_key(label, mk)
        if alias:
            if pct_f is not None:
                pct_by_metric[alias] = pct_f
            if abs_f is not None:
                abs_by_metric[alias] = abs_f
        if pct_f is not None:
            pct_by_label[label] = pct_f
            if alias and alias in FINANCIAL_METRIC_LABELS:
                pct_by_label[FINANCIAL_METRIC_LABELS[alias]] = pct_f
        if abs_f is not None:
            abs_by_label[label] = abs_f
            if alias and alias in FINANCIAL_METRIC_LABELS:
                abs_by_label[FINANCIAL_METRIC_LABELS[alias]] = abs_f
    return pct_by_metric, abs_by_metric, pct_by_label, abs_by_label


def metric_variance_breach(
    mis_val: Any,
    afs_val: Any,
    *,
    metric_key: str,
    pct_by_metric: dict[str, float],
    abs_by_metric: dict[str, float],
    pct_by_label: dict[str, float],
    abs_by_label: dict[str, float],
    currency: Optional[str] = None,
    usd_to_inr_rate: Optional[float] = None,
) -> bool:
    """True when MIS vs AFS exceeds configured percent (and optionally absolute) threshold.

    Absolute thresholds are stored in USD.  When ``currency`` is INR the threshold is
    scaled to INR using ``usd_to_inr_rate`` (fetched via :func:`fetch_usd_to_inr_rate`)
    before comparing so that a 50 M USD threshold is not applied raw to ₹-denominated amounts.
    """
    if not is_metric_comparable(mis_val, afs_val):
        return False
    try:
        rep_f = float(mis_val)
        ext_f = float(afs_val)
    except (TypeError, ValueError):
        return False

    mk = metric_key.strip().lower()
    from src.services.company_audit_recorder import FINANCIAL_METRIC_LABELS

    label = FINANCIAL_METRIC_LABELS.get(mk, mk.title())

    abs_diff = abs(rep_f - ext_f)
    if abs_diff <= 1e-9:
        return False
    pct = 0.0 if rep_f == 0 else abs_diff / abs(rep_f)
    pct_thresh = pct_by_metric.get(mk, pct_by_label.get(label, 0.005))
    abs_thresh = abs_by_metric.get(mk, abs_by_label.get(label))

    # Absolute thresholds are stored in full USD.
    # When amounts are in INR, convert the threshold to INR via live FX rate before comparing.
    effective_abs_thresh = abs_thresh
    cur = (currency or "").strip().upper()
    if (
        abs_thresh is not None
        and cur == "INR"
        and usd_to_inr_rate is not None
        and usd_to_inr_rate > 0
    ):
        effective_abs_thresh = abs_thresh * usd_to_inr_rate

    flagged = pct > pct_thresh
    if effective_abs_thresh is not None and effective_abs_thresh > 0:
        flagged = flagged or (abs_diff > effective_abs_thresh)
    return bool(flagged)


def row_variance_breach(
    row: FinancialMetricReconciliation,
    *,
    mis_amount: Optional[Any] = None,
    mis_currency: Optional[str] = None,
    pct_by_metric: dict[str, float],
    abs_by_metric: dict[str, float],
    pct_by_label: dict[str, float],
    abs_by_label: dict[str, float],
    usd_to_inr_rate: Optional[float] = None,
) -> bool:
    mk = (row.metric_key or "").strip().lower()
    mis_val = mis_amount
    mis_cur = mis_currency
    currency = (row.afs_currency or mis_cur or "").strip().upper() or None
    return metric_variance_breach(
        mis_val,
        row.afs_amount,
        metric_key=mk,
        pct_by_metric=pct_by_metric,
        abs_by_metric=abs_by_metric,
        pct_by_label=pct_by_label,
        abs_by_label=abs_by_label,
        currency=currency,
        usd_to_inr_rate=usd_to_inr_rate,
    )


async def thresholds_for_company(db: AsyncSession) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
]:
    return await load_variance_threshold_maps(db)


async def fetch_usd_to_inr_rate(db: Optional[AsyncSession] = None) -> Optional[float]:
    """Fetch the latest stored month-end USD → INR rate.

    Reads from ``fx_monthly_rate`` via FxService when ``db`` is supplied; falls
    back to mock tables otherwise. Returns ``None`` on any failure so callers fall
    back to no-conversion gracefully. Absolute thresholds are stored in USD;
    callers should scale them before comparing INR-denominated amounts.
    """
    try:
        from src.services.fx_service import FxService  # local import — avoids circular deps

        rates = await FxService.fetch_rates("USD", db=db)
        rate, _ = FxService.conversion_rate(rates, "INR")
        return float(rate)
    except Exception:
        logger.warning("fetch_usd_to_inr_rate: could not obtain USD→INR rate", exc_info=True)
        return None


def reconciliation_row_can_set_enable_true(
    row: FinancialMetricReconciliation,
    *,
    mis_amount: Optional[Any] = None,
    mis_currency: Optional[str] = None,
    pct_by_metric: dict[str, float],
    abs_by_metric: dict[str, float],
    pct_by_label: dict[str, float],
    abs_by_label: dict[str, float],
    usd_to_inr_rate: Optional[float] = None,
) -> bool:
    """Rules: enabling is only legitimate when MIS/AFS comparable and thresholds breach."""
    mis_val = mis_amount
    if mis_val is None or row.afs_amount is None:
        return False
    return row_variance_breach(
        row,
        mis_amount=mis_val,
        mis_currency=mis_currency,
        pct_by_metric=pct_by_metric,
        abs_by_metric=abs_by_metric,
        pct_by_label=pct_by_label,
        abs_by_label=abs_by_label,
        usd_to_inr_rate=usd_to_inr_rate,
    )


def reconciliation_row_qualifies_email_html(
    row: FinancialMetricReconciliation,
    *,
    mis_amount: Optional[Any] = None,
    mis_currency: Optional[str] = None,
    pct_by_metric: dict[str, float],
    abs_by_metric: dict[str, float],
    pct_by_label: dict[str, float],
    abs_by_label: dict[str, float],
    usd_to_inr_rate: Optional[float] = None,
) -> bool:
    mis_val = mis_amount
    if mis_val is None or row.afs_amount is None:
        return False
    st = (row.status or "").strip().lower()
    if st != "open":
        return False
    if not row.enable:
        return False
    return row_variance_breach(
        row,
        mis_amount=mis_val,
        mis_currency=mis_currency,
        pct_by_metric=pct_by_metric,
        abs_by_metric=abs_by_metric,
        pct_by_label=pct_by_label,
        abs_by_label=abs_by_label,
        usd_to_inr_rate=usd_to_inr_rate,
    )


def _canonical_mis_stmt(portfolio_company_id: int, review_cycle: Optional[str]) -> Select[Any]:
    payload_company = FinancialDataSnowflake.payload["portfolio_company_id"].as_string()
    company_match = or_(
        FinancialDataSnowflake.portfolio_company_id == portfolio_company_id,
        cast(payload_company, Integer) == portfolio_company_id,
    )
    stmt = select(FinancialDataSnowflake).where(
        company_match,
        FinancialDataSnowflake.entity_id.is_(None),
    )
    rc = (review_cycle or "").strip()
    if rc:
        stmt = stmt.where(
            or_(
                FinancialDataSnowflake.review_cycle == rc,
                FinancialDataSnowflake.review_cycle.is_(None),
            )
        )
    return stmt.order_by(
        FinancialDataSnowflake.review_cycle.desc().nulls_last(),
        FinancialDataSnowflake.id.desc(),
    ).limit(1)


async def fetch_canonical_snowflake_row(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: Optional[str],
) -> Optional[FinancialDataSnowflake]:
    row = (
        (
            await db.execute(
                _canonical_mis_stmt(portfolio_company_id, review_cycle),
            )
        )
        .scalars()
        .first()
    )
    return row


async def stamp_mis_for_company_review_cycle(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: Optional[str],
) -> None:
    """No-op: MIS is resolved live from ``FinancialDataSnowflake`` at API read time."""
    _ = (db, portfolio_company_id, review_cycle)


async def stamp_mis_all_cycles_for_company(db: AsyncSession, *, portfolio_company_id: int) -> None:
    """No-op: MIS is resolved live from ``FinancialDataSnowflake`` at API read time."""
    _ = (db, portfolio_company_id)
