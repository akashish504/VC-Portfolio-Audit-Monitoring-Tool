"""
Shared utilities for manipulation-phase sync scripts.

Review-cycle resolution:
  Given a raw date (reporting_date) or a raw label string, find the ReviewCycle
  whose starts_at / ends_at window contains that date, then look up the
  PortfolioCompany matching company_id + that review cycle.

Matching semantics follow the requirement:
  - Match by (company_id, review_cycle_id).
  - If no ReviewCycle contains the date, log and skip.
  - If multiple PortfolioCompany rows match after applying both keys, surface as
    a sync warning and skip.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import PortfolioCompany, ReviewCycle
from src.services.fy_end import (
    MONTH_ABBR,
    derive_cycle_id_from_fy_end,
    format_fy_end,
    fy_end_last_day,
    normalize_fy_end,
)

_ABBR_TO_MONTH_NUM = {abbr.lower(): i + 1 for i, abbr in enumerate(MONTH_ABBR)}
from src.services.review_cycle_provisioning import format_cycle_display_name, parse_cycle_id

# Matches "Dec 24" or "dec 24" → normalise to "Dec-24" before passing to normalize_fy_end
_FYE_SPACE_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{2})$")

logger = logging.getLogger(__name__)


def _to_date(v: object) -> Optional[date]:
    """Coerce date / datetime / None to ``date``."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def resolve_review_cycle_for_date(
    db: Session,
    reporting_date: object,
) -> Optional[ReviewCycle]:
    """
    Find the ReviewCycle whose starts_at <= reporting_date <= ends_at.

    Returns None when:
    - reporting_date is None / unparseable
    - no ReviewCycle window contains the date
    - multiple cycles match (ambiguous — logged as warning)
    """
    d = _to_date(reporting_date)
    if d is None:
        return None

    cycles = db.execute(select(ReviewCycle)).scalars().all()
    matches = []
    for rc in cycles:
        start = _to_date(rc.starts_at)
        end = _to_date(rc.ends_at)
        if start is not None and end is not None:
            if start <= d <= end:
                matches.append(rc)
        elif start is not None and end is None:
            if d >= start:
                matches.append(rc)
        elif start is None and end is not None:
            if d <= end:
                matches.append(rc)

    if len(matches) == 0:
        return None
    if len(matches) > 1:
        logger.warning(
            "sync_utils: reporting_date=%s matches multiple review cycles %s — skipping",
            d,
            [rc.id for rc in matches],
        )
        return None
    return matches[0]


def resolve_portfolio_company(
    db: Session,
    company_id_str: str,
    review_cycle_id: str,
    context: str = "",
) -> Optional[PortfolioCompany]:
    """
    Find the single PortfolioCompany for (company_id, review_cycle_id).

    Returns None when:
    - no row found (logged as debug)
    - multiple rows found (logged as warning — ambiguous)
    """
    rows = (
        db.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id_str,
                PortfolioCompany.review_cycle_id == review_cycle_id,
            )
        )
        .scalars()
        .all()
    )
    if len(rows) == 0:
        logger.debug(
            "sync_utils: no PortfolioCompany for company_id=%s cycle=%s [%s]",
            company_id_str,
            review_cycle_id,
            context,
        )
        return None
    if len(rows) > 1:
        logger.warning(
            "sync_utils: ambiguous — %d PortfolioCompany rows for company_id=%s cycle=%s [%s] — skipping",
            len(rows),
            company_id_str,
            review_cycle_id,
            context,
        )
        return None
    return rows[0]


def normalize_fye_raw(raw: Optional[str]) -> Optional[str]:
    """Coerce raw fye strings like "Dec 24" or "dec 24" to "Dec-24" then validate."""
    if not raw:
        return None
    s = str(raw).strip()
    m = _FYE_SPACE_RE.match(s)
    if m:
        s = f"{m.group(1)}-{m.group(2)}"
    return normalize_fy_end(s)


def _fy_end_year_from_reporting_date(month: int, reporting_date: date) -> int:
    """Year of the most recent FY-end (``month``) on-or-before ``reporting_date``.

    Anchors a year-less FY-end to the audited FY-end it actually supports, using
    the same forward-window convention as ``snowflake_pr_match``: a lagged Q2/Q3/Q4
    submission still belongs to the *trailing* FY-end, NOT the reporting date's
    calendar year. e.g. month=3 (Mar) + 30-Sep-2025 → 2025 (Mar-25); month=12 (Dec)
    + 30-Sep-2025 → 2024 (Dec-24).

    Compared at MONTH granularity (not exact day): a submission dated in or after
    the FY-end month belongs to that year's FY-end, an earlier month to the prior
    year's. This is robust to non-month-end / off-quarter reporting dates — e.g.
    30-Dec, a mid-month date, or 28-Feb in a leap year — which a day-exact compare
    would push into the wrong fiscal year.
    """
    if month <= reporting_date.month:
        return reporting_date.year
    return reporting_date.year - 1


def normalize_fye_raw_with_date(
    raw: Optional[str],
    reporting_date: object = None,
) -> Optional[str]:
    """Like :func:`normalize_fye_raw`, but recovers year-less FY-ends from a date.

    The MIS ``fye`` column also stores values without a concrete year — "Mar FY",
    "MMM fy", "MMM-FY" (see ``PRSubmissionDataRaw.fye``). The plain parser returns
    None for those, so the row is dropped and never reconciled. When a
    ``reporting_date`` is present we take the month from the label and the year
    from :func:`_fy_end_year_from_reporting_date`, yielding canonical "Mmm-YY".

    Already-parseable values are unchanged (the plain parser runs first), and the
    result is None only when nothing can be resolved — so passing
    ``reporting_date=None`` reproduces :func:`normalize_fye_raw` exactly.
    """
    plain = normalize_fye_raw(raw)
    if plain:
        return plain
    rd = _to_date(reporting_date)
    if rd is None or not raw:
        return None
    m = re.match(r"^\s*([A-Za-z]{3})", str(raw))
    if not m:
        return None
    month = _ABBR_TO_MONTH_NUM.get(m.group(1).lower())
    if not month:
        return None
    return format_fy_end(month=month, year=_fy_end_year_from_reporting_date(month, rd))


def resolve_review_cycle_for_fy_end(
    db: Session,
    fye_raw: Optional[str],
    reporting_date: object = None,
) -> Optional[ReviewCycle]:
    """
    Sync mirror of ``fy_end.resolve_review_cycle_id_for_fy_end``.

    Resolves a ReviewCycle from a normalised FY-end string using:
      1. starts_at/ends_at window overlap with the FY-end last day.
         Multiple matches → choose the newest (latest starts_at).
      2. Exact PK match on the derived CYxx-FYyy label.
      3. Name match on format_cycle_display_name(cy, fy).

    Returns None (instead of raising) when nothing resolves — callers skip the row.

    ``reporting_date`` (optional) lets a year-less fye like "Mar FY" resolve via
    :func:`normalize_fye_raw_with_date`; omitting it preserves the prior behaviour.
    """
    normalized = normalize_fye_raw_with_date(fye_raw, reporting_date)
    if not normalized:
        return None

    last = fy_end_last_day(normalized)
    end_of_day = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=timezone.utc)
    start_of_day = datetime(last.year, last.month, last.day, tzinfo=timezone.utc)

    rows = (
        db.execute(
            select(ReviewCycle).where(
                ReviewCycle.starts_at <= end_of_day,
                ReviewCycle.ends_at >= start_of_day,
            )
        )
        .scalars()
        .all()
    )
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        rows_sorted = sorted(
            rows,
            key=lambda r: r.starts_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return rows_sorted[0]

    derived = derive_cycle_id_from_fy_end(normalized)
    if not derived:
        return None

    existing = db.get(ReviewCycle, derived)
    if existing is not None:
        return existing

    parsed = parse_cycle_id(derived)
    if parsed:
        cy, fy = parsed
        display_name = format_cycle_display_name(cy, fy)
        name_rows = (
            db.execute(select(ReviewCycle).where(ReviewCycle.name == display_name))
            .scalars()
            .all()
        )
        if len(name_rows) == 1:
            return name_rows[0]
        if len(name_rows) > 1:
            name_rows_sorted = sorted(
                name_rows,
                key=lambda r: r.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return name_rows_sorted[0]

    return None


def resolve_company_for_name_and_date(
    db: Session,
    entity_name: str,
    reporting_date: object,
    context: str = "",
) -> Optional[PortfolioCompany]:
    """
    Find PortfolioCompany by (name, review_cycle_id derived from reporting_date).

    Used by contact-name and investor sync which match on entity name not company_id.
    Falls back to most-recent-cycle row when reporting_date is None.
    """
    rc = resolve_review_cycle_for_date(db, reporting_date)
    if rc is not None:
        rows = (
            db.execute(
                select(PortfolioCompany).where(
                    PortfolioCompany.name == entity_name,
                    PortfolioCompany.review_cycle_id == rc.id,
                )
            )
            .scalars()
            .all()
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            logger.warning(
                "sync_utils: ambiguous — %d PortfolioCompany rows for name=%s cycle=%s [%s] — skipping",
                len(rows),
                entity_name,
                rc.id,
                context,
            )
            return None
        logger.debug(
            "sync_utils: no PortfolioCompany for name=%s cycle=%s [%s]",
            entity_name,
            rc.id,
            context,
        )
        return None

    # No cycle resolved — fall back to the most recent row for this name.
    row = (
        db.execute(
            select(PortfolioCompany)
            .where(PortfolioCompany.name == entity_name)
            .order_by(PortfolioCompany.id.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        logger.debug(
            "sync_utils: no PortfolioCompany found for name=%s [%s]",
            entity_name,
            context,
        )
    return row
