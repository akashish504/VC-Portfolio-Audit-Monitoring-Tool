"""Pure-unit tests for the audited-financial → MIS matching logic.

No DB required. Covers both of the user's tables: the P&L Year-2/Year-1 matrix
(Growth/Venture) and the cash/debt exact-quarter rule, plus the forward window,
most-recent tiebreak, legacy financial_year path, and abstention.
"""
from __future__ import annotations

from datetime import date

from src.services.snowflake_pr_match import (
    STAGE_GROWTH_VENTURE,
    STAGE_SURGE_SEED,
    YEAR_1,
    YEAR_2,
    MisRecord,
    forward_window_dates,
    select_balance_record,
    select_pnl_record,
)

# Representative FY-ends per audited period.
_FY = {
    "Jan-Mar": date(2024, 3, 31),
    "Apr-Jun": date(2024, 6, 30),
    "Jul-Sep": date(2024, 9, 30),
    "Oct-Dec": date(2024, 12, 31),
}


def _slot(fy: date, group: str, reporting: date):
    sel = select_pnl_record(fy, group, [MisRecord("r", reporting)])
    return None if sel is None else sel.year_slot


class TestPnlMatrixGrowthVenture:
    """Aligned quarter → Year 2; any later quarter in the window → Year 1."""

    def test_apr_jun(self):
        fy = _FY["Apr-Jun"]
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 6, 30)) == YEAR_2
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 9, 30)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 12, 31)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 3, 31)) == YEAR_1  # wrap → next Mar

    def test_jul_sep(self):
        fy = _FY["Jul-Sep"]
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 9, 30)) == YEAR_2
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 12, 31)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 3, 31)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 6, 30)) == YEAR_1

    def test_oct_dec(self):
        fy = _FY["Oct-Dec"]
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 12, 31)) == YEAR_2
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 3, 31)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 6, 30)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2025, 9, 30)) == YEAR_1

    def test_jan_mar_via_window(self):
        fy = _FY["Jan-Mar"]  # Mar FY-end
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 3, 31)) == YEAR_2
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 6, 30)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 9, 30)) == YEAR_1
        assert _slot(fy, STAGE_GROWTH_VENTURE, date(2024, 12, 31)) == YEAR_1


class TestSurgeSeed:
    def test_always_year_1_even_when_aligned(self):
        assert _slot(_FY["Apr-Jun"], STAGE_SURGE_SEED, date(2024, 6, 30)) == YEAR_1


class TestRecordSelection:
    def test_most_recent_picks_lagged_quarter(self):
        # Jun FY-end with both Jun and Sep submissions → Sep (most recent) → Year 1.
        fy = _FY["Apr-Jun"]
        sel = select_pnl_record(
            fy, STAGE_GROWTH_VENTURE,
            [MisRecord("a", date(2024, 6, 30)), MisRecord("b", date(2024, 9, 30))],
        )
        assert sel.record.row_id == "b"
        assert sel.year_slot == YEAR_1

    def test_prior_quarter_not_matched(self):
        # A March-2024 submission (before a Jun-2024 FY-end) is not in the window.
        sel = select_pnl_record(
            _FY["Apr-Jun"], STAGE_GROWTH_VENTURE, [MisRecord("a", date(2024, 3, 31))]
        )
        assert sel is None

    def test_legacy_financial_year_match(self):
        legacy_fy = date(2019, 3, 31)
        hit = select_pnl_record(
            legacy_fy, STAGE_GROWTH_VENTURE,
            [MisRecord("r", date(2019, 3, 31), financial_year=2019)],
        )
        assert hit is not None and hit.year_slot == YEAR_2
        miss = select_pnl_record(
            legacy_fy, STAGE_GROWTH_VENTURE,
            [MisRecord("r", date(2018, 3, 31), financial_year=2018)],
        )
        assert miss is None


class TestBalanceExactQuarter:
    def test_exact_date_match(self):
        rec = select_balance_record(_FY["Apr-Jun"], [MisRecord("a", date(2024, 6, 30))])
        assert rec is not None and rec.row_id == "a"

    def test_non_aligned_abstains(self):
        assert select_balance_record(_FY["Apr-Jun"], [MisRecord("a", date(2024, 9, 30))]) is None

    def test_off_quarter_fy_end_abstains(self):
        # 31-Jan FY-end: no quarter-end submission equals it → abstain.
        assert select_balance_record(date(2024, 1, 31), [MisRecord("a", date(2024, 3, 31))]) is None


class TestForwardWindowDates:
    def test_jun_window(self):
        assert forward_window_dates(date(2024, 6, 30)) == [
            date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31),
        ]

    def test_oct_window_wraps_year(self):
        assert forward_window_dates(date(2024, 12, 31)) == [
            date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
        ]
