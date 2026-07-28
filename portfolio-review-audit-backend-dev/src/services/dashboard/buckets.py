"""Shared dashboard helpers: FYE-month column ordering and status taxonomies.

Kept separate so both the dummy and DB providers share one source of truth for
column headers and the status row labels defined by the spec workbook.
"""
from __future__ import annotations

from typing import Optional

from src.services.fy_end import (
    months_for_review_cycle_id,
    months_for_review_cycle_name,
    parse_fy_end,
)

# Status rows for the "tentative" scoping matrix (spec sheet section i).
# NOTE: the workbook's "Co. (basis CIDs)" row is the per-month TOTAL that these
# rows partition — it is represented by ``column_totals``/``grand_total``, not a row.
TENTATIVE_STATUS_ROWS: tuple[str, ...] = (
    "Expected to complete within due date",
    "Expected to complete with overdue",
    "Completed",
    "Not applicable",
    "Others/Excluded",
)

# Status rows for the "actual" scoping matrix (spec sheet section ii).
ACTUAL_STATUS_ROWS: tuple[str, ...] = (
    "Completed within due date",
    "Completed with Overdue",
    "Due now",
    "Not yet due",
    "Overdue companies",
    "Not applicable",
    "Excluded",
)

# Full Jun→May cycle fallback when a cycle id/name cannot be parsed.
_DEFAULT_MONTHS: tuple[str, ...] = (
    "Jun-25", "Jul-25", "Aug-25", "Sep-25", "Oct-25", "Nov-25",
    "Dec-25", "Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26",
)


def months_for_cycle(review_cycle_id: Optional[str], review_cycle_name: Optional[str] = None) -> list[str]:
    """Ordered FYE-month column headers (``"Mmm-YY"``) for a review cycle.

    Tries the cycle id (``CY25-FY26``) then the display name (``CY 25 - FY 26``);
    falls back to the canonical Jun→May span so the matrix always has columns.
    """
    if review_cycle_id:
        months = months_for_review_cycle_id(review_cycle_id.strip())
        if months:
            return months
    if review_cycle_name:
        months = months_for_review_cycle_name(review_cycle_name.strip())
        if months:
            return months
    return list(_DEFAULT_MONTHS)


def empty_month_map(months: list[str]) -> dict[str, int]:
    return {m: 0 for m in months}


def order_month_labels(labels: list[str]) -> list[str]:
    """Sort ``"Mmm-YY"`` labels chronologically; unparseable ones go last."""
    def key(label: str):
        parsed = parse_fy_end(label)
        return (parsed[1], parsed[0]) if parsed else (9999, 99)

    return sorted(labels, key=key)
