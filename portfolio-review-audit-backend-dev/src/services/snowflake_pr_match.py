"""
Audited-Financial → MIS (PR submission) matching logic.

This module is the single source of truth for the two-step rule that decides,
for a given company's *audited financial year-end*, which MIS submission row(s)
feed each canonical metric — and, for the P&L metrics, whether to read the
``yr_1`` (prior FY) or ``yr_2`` (current FY) column.

It is deliberately PURE: no DB, no I/O, no imports of live application code.
The sync engine builds :class:`MisRecord` candidates from ``PRSubmissionDataRaw``
rows and calls the two ``select_*`` functions; nothing here mutates state.

Why two metric families behave differently
-------------------------------------------
* **P&L (flow): revenue, ebitda, pbt, pat** — figures measured *over* the fiscal
  year. The MIS stores two annual columns (``yr_1`` = prior FY, ``yr_2`` = current
  FY). We take the freshest submission in a forward window and then pick the year
  column that corresponds to the audited year (the matrix below).
* **Balance sheet (stock): cash, debt** — a position *as of* a single date. Only
  comparable to the MIS figure reported *as of the same date*. So cash/debt match
  the submission whose reporting date equals the audited FY-end exactly; if there
  is none, we ABSTAIN (returning ``None``) rather than compare across dates — that
  is what prevents phantom variances.

Step 2 — P&L year-column matrix (Growth / Venture)
--------------------------------------------------
Rows = audited FY-end quarter; columns = matched MIS submission quarter::

    Audited Period   Mar    Jun    Sep    Dec
    Jan – Mar        Yr2    Yr1    Yr1    Yr1
    Apr – Jun         —     Yr2    Yr1    Yr1
    Jul – Sep         —      —     Yr2    Yr1
    Oct – Dec         —      —      —     Yr2

The rule is binary: the MIS quarter that *aligns* with the audited FY-end quarter
→ **Year 2**; any *later* quarter reachable in the forward window → **Year 1**.
**Surge / Seed** always uses **Year 1**, regardless of quarter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

# Stage-group ids (mirror src.services.snowflake_pr_financial_mapping; duplicated
# here only as literals so this module stays import-free / pure).
STAGE_SURGE_SEED = "surge_seed"
STAGE_GROWTH_VENTURE = "growth_venture"

# Year-slot tokens returned to the caller; the caller maps these to the
# configured formula set (e.g. "pnl_aligned" vs "pnl_lagged").
YEAR_1 = "yr_1"
YEAR_2 = "yr_2"

# Legacy annual-storage cutoff: audited dates before this use financial_year
# matching, not the quarterly forward window.
LEGACY_CUTOFF = date(2020, 11, 1)


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------


def fy_end_quarter_month(month: int) -> int:
    """Map an FY-end month (1-12) to its audited-period quarter-end month.

    Jan/Feb/Mar → 3, Apr/May/Jun → 6, Jul/Aug/Sep → 9, Oct/Nov/Dec → 12.
    Mirrors the existing ``_FYE_MONTH_TO_BUCKET`` bucketing.
    """
    if month in (1, 2, 3):
        return 3
    if month in (4, 5, 6):
        return 6
    if month in (7, 8, 9):
        return 9
    return 12


def calendar_quarter_month(d: date) -> int:
    """Quarter-end month (3/6/9/12) that a (reporting) date falls in."""
    return fy_end_quarter_month(d.month)


def forward_window(afs_quarter: int) -> list[int]:
    """The four quarter-end months from ``afs_quarter`` forward, inclusive.

    e.g. 6 (Apr–Jun) → [6, 9, 12, 3]; 12 (Oct–Dec) → [12, 3, 6, 9].
    The aligned quarter is always first; later quarters follow in order.
    """
    order = [3, 6, 9, 12]
    start = order.index(afs_quarter)
    return [order[(start + i) % 4] for i in range(4)]


def quarter_end_date(year: int, quarter_month: int) -> date:
    """Last calendar day of the given quarter-end month."""
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}[quarter_month]
    return date(year, quarter_month, last_day)


def forward_window_dates(fy_end: date) -> list[date]:
    """The four quarter-end *dates* from the audited FY-end quarter forward.

    Unlike :func:`forward_window` (months only), this is anchored in time: a
    submission in the same quarter-*month* but an earlier/later year is NOT in
    the window. e.g. a 30-Jun-2024 FY-end →
    [30-Jun-2024, 30-Sep-2024, 31-Dec-2024, 31-Mar-2025]. A 31-Mar-2024
    submission (the *prior* March) is correctly excluded.
    """
    afs_q = fy_end_quarter_month(fy_end.month)
    months = forward_window(afs_q)
    out: list[date] = []
    year = fy_end.year
    prev: Optional[int] = None
    for qm in months:
        if prev is not None and qm < prev:  # wrapped past December → next year
            year += 1
        out.append(quarter_end_date(year, qm))
        prev = qm
    return out


# ---------------------------------------------------------------------------
# Candidate record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MisRecord:
    """One MIS submission candidate (built from a PRSubmissionDataRaw row)."""

    row_id: str
    reporting_date: Optional[date]
    # Fiscal year that this submission's annual columns are anchored on, when
    # known (``year_of_year_1``). Used only for the non-fatal year-stamp check.
    year_of_year_1: Optional[int] = None
    # Legacy/annual financial-year tag (year of the annual figure), when present.
    financial_year: Optional[int] = None


@dataclass(frozen=True)
class PnlSelection:
    """Result of selecting the P&L source row + year column."""

    record: MisRecord
    year_slot: str            # YEAR_1 or YEAR_2
    aligned: bool             # MIS quarter == audited FY-end quarter
    reason: str
    year_stamp_ok: Optional[bool] = None  # None = couldn't check


# ---------------------------------------------------------------------------
# Step 1 + Step 2 — P&L (revenue / ebitda / pbt / pat)
# ---------------------------------------------------------------------------


def afs_fiscal_year(fy_end: date) -> int:
    """The fiscal year an audited FY-end belongs to (year of the end date)."""
    return fy_end.year


def _year_stamp_ok(record: MisRecord, year_slot: str, afs_fy: int) -> Optional[bool]:
    """Cross-check the picked year column against the record's year stamp.

    Returns True/False when ``year_of_year_1`` is known, else None. Non-fatal:
    the caller logs/flags on False rather than silently emitting a wrong year.
    Assumes ``yr_2`` is the fiscal year after ``yr_1``.
    """
    base = record.year_of_year_1
    if base is None:
        return None
    implied = base if year_slot == YEAR_1 else base + 1
    return implied == afs_fy


def select_pnl_record(
    fy_end: date,
    stage_group: str,
    candidates: list[MisRecord],
) -> Optional[PnlSelection]:
    """Pick the MIS record + year column for the P&L metrics.

    Returns ``None`` (abstain) when no candidate falls in the forward window /
    financial-year match — the caller then leaves revenue/ebitda/pbt/pat unset.
    """
    afs_q = fy_end_quarter_month(fy_end.month)
    afs_fy = afs_fiscal_year(fy_end)
    # Pre-Nov-2020 legacy data was stored annually → match by financial_year.
    # Everything from Nov-2020 on (incl. Jan–Mar) uses the quarter-end forward
    # window: per the matrix, a Mar FY-end's March submission aligns (Year 2) and
    # later quarters lag (Year 1) — identical in shape to the other quarters.
    use_annual = fy_end < LEGACY_CUTOFF

    if use_annual:
        matches = [c for c in candidates if c.financial_year == afs_fy]
        # Fall back to any candidate carrying a reporting_date in that FY when no
        # explicit financial_year tag is present.
        if not matches:
            matches = [
                c
                for c in candidates
                if c.reporting_date is not None and c.reporting_date.year == afs_fy
            ]
    else:
        window = set(forward_window_dates(fy_end))
        matches = [c for c in candidates if c.reporting_date in window]

    if not matches:
        return None

    # Most recent submission wins (re-statements supersede earlier ones).
    chosen = max(
        matches,
        key=lambda c: (c.reporting_date or date.min, c.row_id),
    )

    aligned = (
        chosen.reporting_date is not None
        and calendar_quarter_month(chosen.reporting_date) == afs_q
    )

    if stage_group == STAGE_SURGE_SEED:
        year_slot = YEAR_1
        reason = "surge/seed → always Year 1"
    else:
        year_slot = YEAR_2 if aligned else YEAR_1
        reason = (
            "growth/venture → aligned quarter → Year 2"
            if aligned
            else "growth/venture → lagged quarter → Year 1"
        )

    return PnlSelection(
        record=chosen,
        year_slot=year_slot,
        aligned=aligned,
        reason=reason,
        year_stamp_ok=_year_stamp_ok(chosen, year_slot, afs_fy),
    )


# ---------------------------------------------------------------------------
# Step 1 — Balance sheet (cash / debt)
# ---------------------------------------------------------------------------


def select_balance_record(
    fy_end: date,
    candidates: list[MisRecord],
) -> Optional[MisRecord]:
    """Pick the MIS record for cash/debt — exact balance-sheet date only.

    A balance is a position as of a date; it is only comparable to the MIS
    figure reported as of the SAME date. We therefore require
    ``reporting_date == fy_end`` exactly. When no submission lands on the
    audited FY-end date (e.g. an off-quarter FY-end, or the quarter wasn't
    submitted), we return ``None`` so the caller leaves cash/debt unset rather
    than comparing balances at different dates.
    """
    matches = [c for c in candidates if c.reporting_date == fy_end]
    if not matches:
        return None
    # If the same date was submitted more than once, the latest row wins.
    return max(matches, key=lambda c: c.row_id)
