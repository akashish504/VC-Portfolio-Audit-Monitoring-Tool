"""Tests for the centralized reconciliation service.

These cover the pure helpers (currency / FX-date parsing, diff math) plus
the orchestrated `reconcile_metric_values` flow via mocks. The deeper
integration paths (audit-file upload, Snowflake sync, manual entry) are
exercised through their own end-to-end scripts against a real DB.

Run with:  pytest tests/test_reconciliation_service.py
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_fy_end_date_iso_only():
    from src.services.reconciliation_service import _parse_fy_end_date

    assert _parse_fy_end_date("2025-03-31") == date(2025, 3, 31)
    assert _parse_fy_end_date(" 2025-03-31 ") == date(2025, 3, 31)


def test_parse_fy_end_date_rejects_other_formats():
    from src.services.reconciliation_service import _parse_fy_end_date

    assert _parse_fy_end_date("31-03-2025") is None
    assert _parse_fy_end_date("31 March 2025") is None
    assert _parse_fy_end_date(None) is None
    assert _parse_fy_end_date("") is None


def test_norm_ccy():
    from src.services.reconciliation_service import _norm_ccy

    assert _norm_ccy("inr") == "INR"
    assert _norm_ccy(" usd ") == "USD"
    assert _norm_ccy("US") is None
    assert _norm_ccy("US1") is None
    assert _norm_ccy(None) is None


def test_compute_diff_both_present():
    from src.services.reconciliation_service import _compute_diff

    abs_d, pct = _compute_diff(110.0, 100.0)
    assert abs_d == 10.0
    assert pct == pytest.approx(10.0)


def test_compute_diff_negative_extracted():
    from src.services.reconciliation_service import _compute_diff

    abs_d, pct = _compute_diff(90.0, 100.0)
    assert abs_d == 10.0
    assert pct == pytest.approx(-10.0)


def test_compute_diff_snowflake_zero_both_zero():
    from src.services.reconciliation_service import _compute_diff

    assert _compute_diff(0.0, 0.0) == (0.0, 0.0)


def test_compute_diff_snowflake_zero_extracted_nonzero():
    from src.services.reconciliation_service import _compute_diff

    abs_d, pct = _compute_diff(5.0, 0.0)
    assert abs_d == 5.0
    assert pct == 100.0


def test_compute_diff_one_side_missing():
    from src.services.reconciliation_service import _compute_diff

    assert _compute_diff(None, 100.0) == (None, None)
    assert _compute_diff(100.0, None) == (None, None)


# ---------------------------------------------------------------------------
# `_convert` — currency conversion gateway around fetch_historical_rate
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def fy_end_ts() -> datetime:
    return datetime.combine(date(2025, 3, 31), time(12, 0, 0))


def test_convert_amount_none_returns_none(fy_end_ts):
    from src.services.reconciliation_service import _convert

    result = _run(_convert(
        MagicMock(), amount=None, src_ccy="USD", tgt_ccy="INR", fy_end_dt=fy_end_ts
    ))
    assert result == (None, None, None)


def test_convert_src_equals_target_skips_fx(fy_end_ts):
    from src.services.reconciliation_service import _convert

    db = MagicMock()
    result = _run(_convert(
        db, amount=123.4, src_ccy="INR", tgt_ccy="INR", fy_end_dt=fy_end_ts
    ))
    assert result == (123.4, 1.0, None)


def test_convert_missing_source_currency_marks_incomplete(fy_end_ts):
    from src.services.reconciliation_service import _convert, STATUS_INCOMPLETE_NO_SOURCE

    result = _run(_convert(
        MagicMock(), amount=100.0, src_ccy=None, tgt_ccy="INR", fy_end_dt=fy_end_ts
    ))
    assert result == (None, None, STATUS_INCOMPLETE_NO_SOURCE)


def test_convert_uses_historical_rate_with_fy_end_timestamp(fy_end_ts):
    from src.services.reconciliation_service import _convert

    with patch(
        "src.services.reconciliation_service.fetch_historical_rate",
        new=AsyncMock(return_value=(83.5, "2025-03-31T12:00:00")),
    ) as mock_fx:
        amt, rate, err = _run(_convert(
            MagicMock(), amount=10.0, src_ccy="USD", tgt_ccy="INR", fy_end_dt=fy_end_ts
        ))
    assert rate == 83.5
    assert amt == round(10.0 * 83.5, 4)
    assert err is None
    # Verify the FX call was at fy_end_ts (12:00:00), not "now".
    _, kwargs = mock_fx.call_args
    assert kwargs["at"] == fy_end_ts
    assert kwargs["from_currency"] == "USD"
    assert kwargs["to_currency"] == "INR"


def test_convert_fx_unavailable_does_not_fall_back_to_live(fy_end_ts):
    from src.services.fx_service import FxConversionUnavailable
    from src.services.reconciliation_service import _convert, STATUS_INCOMPLETE_NO_FX

    with patch(
        "src.services.reconciliation_service.fetch_historical_rate",
        new=AsyncMock(side_effect=FxConversionUnavailable("USD", "INR", fy_end_ts, "no mock rate")),
    ):
        result = _run(_convert(
            MagicMock(), amount=10.0, src_ccy="USD", tgt_ccy="INR", fy_end_dt=fy_end_ts
        ))
    assert result == (None, None, STATUS_INCOMPLETE_NO_FX)


# ---------------------------------------------------------------------------
# Entity-status flip rule
# ---------------------------------------------------------------------------


def test_entity_status_flips_from_pending_review_on_breach():
    from src.services.reconciliation_service import (
        _maybe_update_entity_status,
        ENTITY_STATUS_IN_REVIEW,
        ENTITY_STATUS_DISCREPANCY_IDENTIFIED,
    )

    entity = SimpleNamespace(id=42, portfolio_company_id=7, status=ENTITY_STATUS_IN_REVIEW)
    db = MagicMock()
    db.get = AsyncMock(return_value=entity)
    db.add = MagicMock()

    with patch("src.services.reconciliation_service.CompanyAuditRecorder") as recorder_cls:
        recorder = recorder_cls.return_value
        recorder.log_company_field_changes = AsyncMock()
        _run(_maybe_update_entity_status(db, entity_id=42, breached=True))

    assert entity.status == ENTITY_STATUS_DISCREPANCY_IDENTIFIED
    db.add.assert_called_once_with(entity)


def test_entity_status_not_regressed_from_resolved():
    from src.services.reconciliation_service import _maybe_update_entity_status

    entity = SimpleNamespace(status="Resolved")
    db = MagicMock()
    db.get = AsyncMock(return_value=entity)
    db.add = MagicMock()

    _run(_maybe_update_entity_status(db, entity_id=42, breached=True))
    assert entity.status == "Resolved"
    db.add.assert_not_called()


def test_entity_status_not_touched_when_not_breached():
    from src.services.reconciliation_service import (
        _maybe_update_entity_status,
        ENTITY_STATUS_IN_REVIEW,
    )

    entity = SimpleNamespace(status=ENTITY_STATUS_IN_REVIEW)
    db = MagicMock()
    db.get = AsyncMock(return_value=entity)
    db.add = MagicMock()

    _run(_maybe_update_entity_status(db, entity_id=42, breached=False))
    assert entity.status == ENTITY_STATUS_IN_REVIEW
    db.add.assert_not_called()


def test_is_metric_comparable_mirrors_dashboard():
    from src.services.financial_reconciliation import is_metric_comparable

    assert is_metric_comparable(100.0, 102.0) is True
    assert is_metric_comparable(None, 100.0) is False
    assert is_metric_comparable(100.0, None) is False
    assert is_metric_comparable(0.0, 100.0) is False
    assert is_metric_comparable(100.0, 0.0) is False
    assert is_metric_comparable(0.0, 0.0) is False


@pytest.mark.parametrize(
    "metric_key,row_key",
    [
        ("revenue", "revenue"),
        ("revenue", "Revenue"),
        ("ebitda", "ebitda"),
        ("ebitda", "EBITDA"),
        ("pbt", "pbt"),
        ("pbt", "PBT"),
        ("pat", "pat"),
        ("pat", "PAT"),
        ("cash", "cash"),
        ("cash", "Cash"),
        ("debt", "debt"),
        ("debt", "Debt"),
    ],
)
def test_resolve_threshold_metric_key_accepts_all_recon_metrics(metric_key, row_key):
    from src.services.financial_reconciliation import resolve_threshold_metric_key

    assert resolve_threshold_metric_key(row_key) == metric_key
    assert resolve_threshold_metric_key(row_key, metric_key) == metric_key


@pytest.mark.parametrize("metric_key", ["revenue", "ebitda", "pbt", "pat", "cash", "debt"])
def test_metric_variance_breach_uses_configured_threshold_for_each_metric(metric_key):
    from src.services.financial_reconciliation import metric_variance_breach

    # 1.7% variance with a 10% configured threshold should not breach for any metric.
    assert metric_variance_breach(
        100.0,
        101.7,
        metric_key=metric_key,
        pct_by_metric={metric_key: 0.10},
        abs_by_metric={},
        pct_by_label={},
        abs_by_label={},
    ) is False


@pytest.mark.parametrize("metric_key", ["revenue", "ebitda", "pbt", "pat", "cash", "debt"])
def test_metric_variance_breach_defaults_to_half_percent_without_config(metric_key):
    from src.services.financial_reconciliation import metric_variance_breach

    # 2% variance breaches the built-in 0.5% default when no threshold is configured.
    assert metric_variance_breach(
        100.0,
        102.0,
        metric_key=metric_key,
        pct_by_metric={},
        abs_by_metric={},
        pct_by_label={},
        abs_by_label={},
    ) is True


def test_is_metric_comparable_treats_both_zero_as_not_comparable():
    from src.services.financial_reconciliation import is_metric_comparable

    assert is_metric_comparable(0.0, 0.0) is False


@pytest.mark.parametrize("metric_key", ["revenue", "ebitda", "pbt", "pat", "cash", "debt"])
def test_metric_variance_breach_ignores_one_sided_zero_for_each_metric(metric_key):
    from src.services.financial_reconciliation import metric_variance_breach

    assert metric_variance_breach(
        0.0,
        100.0,
        metric_key=metric_key,
        pct_by_metric={metric_key: 0.10},
        abs_by_metric={},
        pct_by_label={},
        abs_by_label={},
    ) is False
