"""
Integration and unit tests for the `abs` flag on financial metric mapping terms.

Layers:
  * Unit tests for evaluate_metric_with_breakdown / evaluate_metric_from_terms
    with abs flag — always run (no DB needed).
  * Integration tests that persist a mapping with abs=True to the real ConfigTable
    via an async session, then reload and re-evaluate — auto-skipped when DB is
    not reachable.

Run:
    PYTHONPATH=. pytest tests/test_financial_metric_mapping_abs.py -v
"""
from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# DB reachability guard
# ---------------------------------------------------------------------------


def _db_reachable() -> bool:
    if os.environ.get("SKIP_DB_TESTS"):
        return False
    try:
        from sqlalchemy import text
        from src.db.session import sync_engine

        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_skip_no_db = pytest.mark.skipif(
    not _db_reachable(), reason="Configured Postgres DB not reachable; skipping integration tests"
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Shared extracted data fixture
# ---------------------------------------------------------------------------

# A minimal extracted tree with one negative and one positive numeric leaf.
_EXTRACTED = {
    "profit_and_loss": {
        "revenue": {
            "revenue_from_operations": -5000.0,   # stored as negative
        },
        "ebitda": 1200.0,
    },
    "balance_sheet": {
        "liabilities": {
            "non_current_liabilities": {
                "financial_liabilities": {
                    "borrowings": -8000.0,         # stored as negative
                }
            },
            "current_liabilities": {
                "financial_liabilities": {
                    "borrowings": 2000.0,
                }
            },
        }
    },
}

_REVENUE_PATH = "profit_and_loss.revenue.revenue_from_operations"
_EBITDA_PATH = "profit_and_loss.ebitda"
_NC_BORROW_PATH = "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
_CUR_BORROW_PATH = "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings"


# ---------------------------------------------------------------------------
# Unit: evaluate_metric_with_breakdown — abs flag behaviour
# ---------------------------------------------------------------------------


class TestEvaluateMetricWithBreakdownAbs:
    """Pure unit tests — no DB required."""

    def test_abs_false_uses_raw_value(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        terms = [{"path": _REVENUE_PATH, "sign": "+", "abs": False}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == -5000.0
        assert result["terms"][0]["raw_value"] == -5000.0
        assert result["terms"][0]["contribution"] == -5000.0
        assert result["terms"][0]["abs"] is False

    def test_abs_true_uses_absolute_value(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        terms = [{"path": _REVENUE_PATH, "sign": "+", "abs": True}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        # raw_value is still the original; contribution is abs(raw) * sign_mult
        assert result["terms"][0]["raw_value"] == -5000.0
        assert result["terms"][0]["contribution"] == 5000.0
        assert result["total"] == 5000.0
        assert result["terms"][0]["abs"] is True

    def test_abs_true_with_minus_sign(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        # sign='-' and abs=True: contribution = -abs(raw)
        terms = [{"path": _REVENUE_PATH, "sign": "-", "abs": True}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["terms"][0]["contribution"] == -5000.0
        assert result["total"] == -5000.0

    def test_abs_false_missing_field_defaults_to_zero(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        terms = [{"path": "does.not.exist", "sign": "+", "abs": False}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == 0.0
        assert result["terms"][0]["source"] == "default_zero"

    def test_abs_true_missing_field_still_zero(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        terms = [{"path": "does.not.exist", "sign": "+", "abs": True}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == 0.0
        assert result["terms"][0]["source"] == "default_zero"

    def test_mixed_abs_and_non_abs_terms(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        # -5000 (abs=True, sign=+) => +5000
        # +1200 (abs=False, sign=+) => +1200
        terms = [
            {"path": _REVENUE_PATH, "sign": "+", "abs": True},
            {"path": _EBITDA_PATH, "sign": "+", "abs": False},
        ]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == pytest.approx(6200.0)
        assert result["terms"][0]["contribution"] == pytest.approx(5000.0)
        assert result["terms"][1]["contribution"] == pytest.approx(1200.0)

    def test_abs_positive_value_is_unchanged(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        # ebitda is positive; abs should not change the result
        terms = [{"path": _EBITDA_PATH, "sign": "+", "abs": True}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == pytest.approx(1200.0)
        assert result["terms"][0]["contribution"] == pytest.approx(1200.0)

    def test_abs_missing_defaults_to_false(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        # Terms without the abs key at all should behave as abs=False
        terms = [{"path": _REVENUE_PATH, "sign": "+"}]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == -5000.0
        assert result["terms"][0]["abs"] is False

    def test_multi_term_sum_with_abs_on_negative_borrowings(self):
        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        # Debt: NC borrowings stored as -8000, abs=True → contributes +8000
        #       CUR borrowings stored as +2000, abs=False → contributes +2000
        terms = [
            {"path": _NC_BORROW_PATH, "sign": "+", "abs": True},
            {"path": _CUR_BORROW_PATH, "sign": "+", "abs": False},
        ]
        result = evaluate_metric_with_breakdown(_EXTRACTED, terms)

        assert result["total"] == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# Unit: evaluate_metric_from_terms — abs propagates through compat wrapper
# ---------------------------------------------------------------------------


class TestEvaluateMetricFromTermsAbs:
    """Ensures the backward-compat wrapper passes abs through correctly."""

    def test_abs_true_propagates(self):
        from src.services.financial_metric_mapping import evaluate_metric_from_terms

        terms = [{"path": _REVENUE_PATH, "sign": "+", "abs": True}]
        result = evaluate_metric_from_terms(_EXTRACTED, terms)

        assert result == pytest.approx(5000.0)

    def test_abs_false_propagates(self):
        from src.services.financial_metric_mapping import evaluate_metric_from_terms

        terms = [{"path": _REVENUE_PATH, "sign": "+", "abs": False}]
        result = evaluate_metric_from_terms(_EXTRACTED, terms)

        assert result == pytest.approx(-5000.0)

    def test_no_ocr_value_returns_none(self):
        from src.services.financial_metric_mapping import evaluate_metric_from_terms

        terms = [{"path": "no.such.path", "sign": "+", "abs": True}]
        result = evaluate_metric_from_terms(_EXTRACTED, terms)

        assert result is None


# ---------------------------------------------------------------------------
# Unit: coerce_metric_terms — abs extraction
# ---------------------------------------------------------------------------


class TestCoerceMetricTermsAbs:
    def test_abs_true_extracted(self):
        from src.services.financial_metric_mapping import coerce_metric_terms

        raw = [{"path": "a.b.c", "sign": "+", "abs": True}]
        result = coerce_metric_terms(raw)

        assert result == [("a.b.c", "+", True)]

    def test_abs_false_extracted(self):
        from src.services.financial_metric_mapping import coerce_metric_terms

        raw = [{"path": "a.b.c", "sign": "-", "abs": False}]
        result = coerce_metric_terms(raw)

        assert result == [("a.b.c", "-", False)]

    def test_abs_missing_defaults_false(self):
        from src.services.financial_metric_mapping import coerce_metric_terms

        raw = [{"path": "a.b.c", "sign": "+"}]
        result = coerce_metric_terms(raw)

        assert result == [("a.b.c", "+", False)]

    def test_abs_truthy_coerced(self):
        from src.services.financial_metric_mapping import coerce_metric_terms

        # Anything truthy stored in DB is treated as True
        raw = [{"path": "a.b.c", "sign": "+", "abs": 1}]
        result = coerce_metric_terms(raw)

        assert result[0][2] is True


# ---------------------------------------------------------------------------
# Unit: audit diff helpers include abs
# ---------------------------------------------------------------------------


class TestAuditDiffAbs:
    def test_formula_for_audit_shows_abs_tag(self):
        from src.services.financial_metric_mapping import format_formula_for_audit

        terms = [
            {"path": "p.a", "sign": "+", "abs": True},
            {"path": "p.b", "sign": "-", "abs": False},
        ]
        result = format_formula_for_audit(terms)

        assert "[abs]" in result
        assert "p.a" in result
        assert "p.b" in result
        # p.b has abs=False, should not have [abs] suffix
        assert "p.b [abs]" not in result

    def test_stable_repr_differs_when_abs_changes(self):
        from src.services.financial_metric_mapping import _stable_terms_repr

        without_abs = [{"path": "x.y", "sign": "+", "abs": False}]
        with_abs = [{"path": "x.y", "sign": "+", "abs": True}]

        assert _stable_terms_repr(without_abs) != _stable_terms_repr(with_abs)

    def test_diff_lines_reports_abs_change(self):
        from src.services.financial_metric_mapping import financial_metric_mapping_audit_diff_lines

        before = {"revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": False}],
                  "ebitda": [], "pbt": [], "pat": [], "cash": [], "debt": []}
        after  = {"revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": True}],
                  "ebitda": [], "pbt": [], "pat": [], "cash": [], "debt": []}

        lines = financial_metric_mapping_audit_diff_lines(before, after)

        assert len(lines) == 1
        assert "Revenue" in lines[0]
        assert "[abs]" in lines[0]


# ---------------------------------------------------------------------------
# Integration: persist and reload from ConfigTable, then evaluate
# ---------------------------------------------------------------------------


@_skip_no_db
class TestFinancialMetricMappingAbsIntegration:
    """
    Uses a real async DB session to persist a mapping with abs=True,
    reload it, and verify the evaluation result changes correctly.
    All writes are rolled back via async_db.rollback() — nothing persists.
    """

    def test_persist_abs_true_then_reload_and_evaluate(self):
        async def _body():
            from sqlalchemy import text, delete
            from src.db.session import async_session as _async_session
            from src.services.financial_metric_mapping import (
                FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
                load_financial_metric_mapping_config,
                persist_financial_metric_mapping,
            )
            from src.db.models import ConfigTable

            async with _async_session() as db:
                await db.execute(text('SET search_path TO "portfolioauditreview", public'))
                await db.execute(
                    delete(ConfigTable).where(ConfigTable.key == FINANCIAL_METRIC_MAPPING_CONFIG_KEY)
                )
                await db.flush()

                mapping = {
                    "revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": True}],
                    "ebitda": [],
                    "pbt": [],
                    "pat": [],
                    "cash": [],
                    "debt": [],
                }
                await persist_financial_metric_mapping(db, mapping=mapping)
                await db.flush()
                loaded = await load_financial_metric_mapping_config(db)
                await db.rollback()
                return loaded

        loaded = _run(_body())

        revenue_terms = loaded["revenue"]
        assert len(revenue_terms) == 1
        assert revenue_terms[0]["path"] == _REVENUE_PATH
        assert revenue_terms[0]["abs"] is True

        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        result = evaluate_metric_with_breakdown(_EXTRACTED, revenue_terms)
        assert result["total"] == pytest.approx(5000.0)
        assert result["terms"][0]["raw_value"] == pytest.approx(-5000.0)
        assert result["terms"][0]["contribution"] == pytest.approx(5000.0)

    def test_persist_abs_false_reload_preserves_sign(self):
        async def _body():
            from sqlalchemy import text, delete
            from src.db.session import async_session as _async_session
            from src.services.financial_metric_mapping import (
                FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
                load_financial_metric_mapping_config,
                persist_financial_metric_mapping,
            )
            from src.db.models import ConfigTable

            async with _async_session() as db:
                await db.execute(text('SET search_path TO "portfolioauditreview", public'))
                await db.execute(
                    delete(ConfigTable).where(ConfigTable.key == FINANCIAL_METRIC_MAPPING_CONFIG_KEY)
                )
                await db.flush()

                mapping = {
                    "revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": False}],
                    "ebitda": [],
                    "pbt": [],
                    "pat": [],
                    "cash": [],
                    "debt": [],
                }
                await persist_financial_metric_mapping(db, mapping=mapping)
                await db.flush()
                loaded = await load_financial_metric_mapping_config(db)
                await db.rollback()
                return loaded

        loaded = _run(_body())

        revenue_terms = loaded["revenue"]
        assert revenue_terms[0]["abs"] is False

        from src.services.financial_metric_mapping import evaluate_metric_with_breakdown

        result = evaluate_metric_with_breakdown(_EXTRACTED, revenue_terms)
        assert result["total"] == pytest.approx(-5000.0)

    def test_persist_abs_missing_defaults_to_false_on_reload(self):
        """Existing terms without abs field in DB should reload as abs=False."""

        async def _body():
            from sqlalchemy import text, delete
            from src.db.session import async_session as _async_session
            from src.services.financial_metric_mapping import (
                FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
                load_financial_metric_mapping_config,
            )
            from src.db.models import ConfigTable

            async with _async_session() as db:
                await db.execute(text('SET search_path TO "portfolioauditreview", public'))
                await db.execute(
                    delete(ConfigTable).where(ConfigTable.key == FINANCIAL_METRIC_MAPPING_CONFIG_KEY)
                )
                await db.flush()

                old_style_value = {
                    "metrics": {
                        "revenue": [{"path": _REVENUE_PATH, "sign": "+"}],
                        "ebitda": [],
                        "pbt": [],
                        "pat": [],
                        "cash": [],
                        "debt": [],
                    }
                }
                db.add(
                    ConfigTable(
                        key=FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
                        value=old_style_value,
                        description="legacy seed without abs",
                    )
                )
                await db.flush()
                loaded = await load_financial_metric_mapping_config(db)
                await db.rollback()
                return loaded

        loaded = _run(_body())

        revenue_terms = loaded["revenue"]
        assert len(revenue_terms) == 1
        assert revenue_terms[0].get("abs") is False

    def test_abs_change_produces_audit_diff_line(self):
        """Flipping abs=False → abs=True on one term generates a diff line for that metric."""
        from src.services.financial_metric_mapping import financial_metric_mapping_audit_diff_lines

        before = {
            "revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": False}],
            "ebitda": [], "pbt": [], "pat": [], "cash": [], "debt": [],
        }
        after = {
            "revenue": [{"path": _REVENUE_PATH, "sign": "+", "abs": True}],
            "ebitda": [], "pbt": [], "pat": [], "cash": [], "debt": [],
        }

        lines = financial_metric_mapping_audit_diff_lines(before, after)

        assert len(lines) == 1, f"Expected 1 diff line, got: {lines}"
        assert "Revenue" in lines[0]
        assert "[abs]" in lines[0]
