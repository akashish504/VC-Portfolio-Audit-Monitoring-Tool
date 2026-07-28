"""FY end (Mmm-YY) helpers and review-cycle month options."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ReviewCycle
from src.services.review_cycle_provisioning import format_cycle_display_name, format_cycle_id, parse_cycle_id

MONTH_ABBR: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_ABBR_TO_MONTH = {name.lower(): i + 1 for i, name in enumerate(MONTH_ABBR)}

FY_END_RE = re.compile(r"^([A-Za-z]{3})-(\d{2})$")
_LEGACY_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def format_fy_end(*, month: int, year: int) -> str:
    if month < 1 or month > 12:
        raise ValueError(f"invalid month: {month}")
    return f"{MONTH_ABBR[month - 1]}-{year % 100:02d}"


def normalize_fy_end(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = FY_END_RE.match(s)
    if not m:
        return None
    abbr = m.group(1).lower()
    month = _ABBR_TO_MONTH.get(abbr)
    if not month:
        return None
    yy = int(m.group(2))
    year = 2000 + yy if yy <= 99 else yy
    return format_fy_end(month=month, year=year)


def parse_fy_end(raw: Optional[str]) -> Optional[tuple[int, int]]:
    normalized = normalize_fy_end(raw)
    if not normalized:
        return None
    m = FY_END_RE.match(normalized)
    if not m:
        return None
    month = _ABBR_TO_MONTH[m.group(1).lower()]
    yy = int(m.group(2))
    return month, 2000 + yy


# Full month names → 1-12 (the 3-letter abbreviations already live in _ABBR_TO_MONTH).
_FULL_MONTH_TO_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_FY_ISO_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_FY_NUMERIC_DMY_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$")
_FY_DAY_MONTH_YEAR_RE = re.compile(r"^\d{1,2}[\s\-./]+([A-Za-z]{3,9})[\s\-./]+(\d{2,4})$")
_FY_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]{3,9})[\s\-./]*(\d{2,4})$")


def _month_name_to_num(token: str) -> Optional[int]:
    t = (token or "").strip().lower()
    if t in _FULL_MONTH_TO_NUM:
        return _FULL_MONTH_TO_NUM[t]
    return _ABBR_TO_MONTH.get(t[:3])


def _year_4digit(num: int) -> Optional[int]:
    if 0 <= num <= 99:
        return 2000 + num
    if 1900 <= num <= 2999:
        return num
    return None


def coerce_fy_end_token(raw: Optional[str]) -> Optional[str]:
    """Best-effort: turn a free-typed period field into canonical 'Mmm-YY', or None.

    Handles month-bearing formats — 'Mar-24', 'Mar-2024', 'Mar 2024', 'Mar.24',
    "Mar'24" / 'Mar’24', 'March-24', 'March 2024', '31-Mar-2024', '31 March 2024',
    '31/03/2024', '2024-03-31'.

    Deliberately returns None for year-only / 'FYxx' inputs: without a month the fiscal
    year-end is ambiguous (PeakXV holds both Mar- and Dec-year-end companies) and must
    never be guessed.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Fast path: already canonical (Mmm-YY).
    canon = normalize_fy_end(s)
    if canon:
        return canon

    # Normalise smart apostrophe → straight, then treat apostrophes as separators
    # ("Mar'24" → "Mar 24"), and collapse whitespace.
    s = s.replace("’", "'").replace("'", " ")
    s = re.sub(r"\s+", " ", s).strip()

    # ISO date: 2024-03-31
    m = _FY_ISO_RE.match(s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return format_fy_end(month=month, year=year)

    # Day-Month-Year with a month name: 31-Mar-2024 / 31 March 2024
    m = _FY_DAY_MONTH_YEAR_RE.match(s)
    if m:
        month = _month_name_to_num(m.group(1))
        year = _year_4digit(int(m.group(2)))
        if month and year:
            return format_fy_end(month=month, year=year)

    # Numeric Day-Month-Year: 31/03/2024 (day-first convention; US m/d falls through)
    m = _FY_NUMERIC_DMY_RE.match(s)
    if m:
        month = int(m.group(2))
        year = _year_4digit(int(m.group(3)))
        if 1 <= month <= 12 and year:
            return format_fy_end(month=month, year=year)

    # Month-Year: Mar-24 / Mar 2024 / March-2024 / Mar.24
    m = _FY_MONTH_YEAR_RE.match(s)
    if m:
        month = _month_name_to_num(m.group(1))
        year = _year_4digit(int(m.group(2)))
        if month and year:
            return format_fy_end(month=month, year=year)

    return None


def fy_end_from_legacy_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _LEGACY_DATE_RE.match(s)
    if m:
        month = int(m.group(1))
        year = int(m.group(3))
        if 1 <= month <= 12:
            return format_fy_end(month=month, year=year)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return format_fy_end(month=dt.month, year=dt.year)
    except ValueError:
        return None


def resolve_company_fy_end(*, fy_end: Optional[str], fy_end_date: Optional[str]) -> Optional[str]:
    normalized = normalize_fy_end(fy_end)
    if normalized:
        return normalized
    return fy_end_from_legacy_date(fy_end_date)


def fy_end_to_fy_end_date(fy_end: str) -> str:
    parsed = parse_fy_end(fy_end)
    if not parsed:
        raise ValueError(f"invalid fy_end: {fy_end!r}")
    month, year = parsed
    last_day = calendar.monthrange(year, month)[1]
    return f"{month:02d}/{last_day:02d}/{year}"


def _months_from_cy_fy(cy: int, fy: int) -> list[str]:
    start_year, start_month = 2000 + cy, 6
    end_year, end_month = 2000 + fy, 5
    out: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        out.append(format_fy_end(month=month, year=year))
        if month == 12:
            month = 1
            year += 1
        else:
            month += 1
    return out


def months_for_review_cycle_id(cycle_id: str) -> list[str]:
    """Return valid FY end strings for a formatted cycle ID like 'CY24-FY25'."""
    parsed = parse_cycle_id((cycle_id or "").strip())
    if not parsed:
        return []
    cy, fy = parsed
    return _months_from_cy_fy(cy, fy)


def coerce_fy_end(
    raw: Optional[str],
    *,
    review_cycle_id: Optional[str] = None,
    valid_months: Optional[list[str]] = None,
) -> Optional[str]:
    """Best-effort canonical FY end (``Mmm-YY``) for dashboard aggregation.

    Master-scoping data is sometimes stored without a concrete year — e.g. the
    placeholder ``"Mar FY"`` or a bare month ``"Mar"`` — meaning "the FY-end
    month, year implied by the review cycle". The year is derived from the
    cycle's Jun→May window so the value lines up with the cycle's month columns
    (e.g. in ``CY25-FY26``: ``"Mar FY"`` → ``Mar-26``, ``"Sep FY"`` → ``Sep-25``).

    - Already canonical (``Mmm-YY``) → returned unchanged.
    - Month-only / ``"Mon FY"`` placeholder → matched to the cycle month.
    - Blank or unrecognisable → ``None``.

    Pass ``valid_months`` (the cycle's canonical columns, e.g. from
    ``months_for_cycle``) to reuse the exact column derivation; otherwise the
    cycle months are derived from ``review_cycle_id``.
    """
    normalized = normalize_fy_end(raw)
    if normalized:
        return normalized
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"^([A-Za-z]{3})", s)
    if not m or m.group(1).lower() not in _ABBR_TO_MONTH:
        return None
    abbr = m.group(1).lower()
    months = (
        valid_months if valid_months is not None
        else months_for_review_cycle_id(review_cycle_id or "")
    )
    for label in months:
        if label[:3].lower() == abbr:
            return label
    return None


# Matches display names like "CY 24 - FY 25" or "CY24-FY25" tolerantly.
_DISPLAY_NAME_RE = re.compile(r"CY\s*(\d{2})\s*[-–]\s*FY\s*(\d{2})", re.IGNORECASE)


def months_for_review_cycle_name(name: str) -> list[str]:
    """Return valid FY end strings by parsing a human-readable cycle name.

    Handles both "CY 24 - FY 25" (production) and "CY24-FY25" (local dev).
    """
    m = _DISPLAY_NAME_RE.search((name or "").strip())
    if not m:
        return []
    cy, fy = int(m.group(1)), int(m.group(2))
    return _months_from_cy_fy(cy, fy)


def validate_fy_end_for_review_cycle(fy_end: str, review_cycle_id: str) -> bool:
    normalized = normalize_fy_end(fy_end)
    if not normalized:
        return False
    allowed = months_for_review_cycle_id(review_cycle_id)
    return normalized in allowed


def derive_cycle_id_from_fy_end(fy_end: str) -> Optional[str]:
    """Map FY end month to CYxx-FYyy when no review_cycles row matches (Jun–May convention)."""
    parsed = parse_fy_end(fy_end)
    if not parsed:
        return None
    month, year = parsed
    cy = year - 2000 if month >= 6 else year - 2000 - 1
    fy = cy + 1
    return format_cycle_id(cy, fy)


def fy_end_last_day(fy_end: str) -> date:
    parsed = parse_fy_end(fy_end)
    if not parsed:
        raise ValueError(f"invalid fy_end: {fy_end!r}")
    month, year = parsed
    return date(year, month, calendar.monthrange(year, month)[1])


async def resolve_review_cycle_id_for_fy_end(db: AsyncSession, fy_end: str) -> str:
    normalized = normalize_fy_end(fy_end)
    if not normalized:
        raise ValueError(f"invalid fy_end: {fy_end!r}")
    last = fy_end_last_day(normalized)
    end_of_day = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=timezone.utc)
    start_of_day = datetime(last.year, last.month, last.day, tzinfo=timezone.utc)

    # 1. Best match: cycles with starts_at/ends_at populated (local dev environment).
    rows = (
        await db.execute(
            select(ReviewCycle).where(
                ReviewCycle.starts_at <= end_of_day,
                ReviewCycle.ends_at >= start_of_day,
            )
        )
    ).scalars().all()
    if len(rows) == 1:
        return rows[0].id
    if len(rows) > 1:
        rows.sort(key=lambda r: r.starts_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return rows[0].id

    # Derive the expected CYxx-FYyy label from the FY end month.
    derived = derive_cycle_id_from_fy_end(normalized)
    if not derived:
        raise ValueError(f"cannot resolve review cycle for fy_end {normalized!r}")

    # 2. Exact PK match — works when PKs are formatted strings (local dev).
    existing = await db.get(ReviewCycle, derived)
    if existing is not None:
        return existing.id

    # 3. Name-based match — works in production where PKs are UUIDs but the
    #    `name` column stores values like "CY 24 - FY 25".
    parsed = parse_cycle_id(derived)
    if parsed:
        cy, fy = parsed
        display_name = format_cycle_display_name(cy, fy)
        name_rows = (
            await db.execute(select(ReviewCycle).where(ReviewCycle.name == display_name))
        ).scalars().all()
        if len(name_rows) == 1:
            return name_rows[0].id
        if len(name_rows) > 1:
            name_rows.sort(
                key=lambda r: r.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return name_rows[0].id

    # 4. Last resort: return the derived string (never matches a UUID PK, but
    #    prevents a hard 422 error — caller can still display the label).
    return derived


def apply_fy_end_fields(*, fy_end: str) -> tuple[str, str]:
    normalized = normalize_fy_end(fy_end)
    if not normalized:
        raise ValueError(f"invalid fy_end: {fy_end!r}")
    return normalized, fy_end_to_fy_end_date(normalized)
