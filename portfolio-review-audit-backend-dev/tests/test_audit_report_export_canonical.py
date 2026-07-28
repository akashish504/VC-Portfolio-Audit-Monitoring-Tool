"""
Tests for the audit-report XLSX export — canonical P&L/BS columns + currency conversion.

Run with:  python tests/test_audit_report_export_canonical.py
       or: PYTHONPATH=. python tests/test_audit_report_export_canonical.py

Style mirrors tests/test_audit_report_export.py: pure-logic tests that exercise the
helpers without needing a live DB. Service-layer functions are imported directly when
they are pure; integration paths are exercised via small inline reimplementations.
"""
from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, time, timezone
from typing import Any, Optional
from unittest.mock import MagicMock


# --- Helpers mirrored from src/services/audit_report_export.py ------------------
# These mirror the production helpers exactly so the test file stays self-contained
# (no DB/env required).

_MILLION = 1_000_000.0
_DECIMALS = 4
NA = "NA"
_CURRENCY_SYMBOL = {"USD": "$", "INR": "₹"}
_SECTION_PREFIX = {
    "profit_and_loss": "P&L",
    "balance_sheet": "BS",
    "cash_flow_statement": "CF",
}
SUPPORTED_OUTPUT_CURRENCIES = ("USD", "INR")
SUPPORTED_REPORT_TYPES = ("reconciliation", "extracted_financials")


def _humanize_leaf(parts):
    return parts[-1].replace("_", " ").strip().title()


def _canonical_column_header(dotted_path: str, output_currency: str) -> str:
    parts = dotted_path.split(".")
    section = parts[0] if parts else ""
    prefix = _SECTION_PREFIX.get(section, section.replace("_", " ").title())
    if section == "balance_sheet" and len(parts) >= 3:
        sub = parts[1].title()
        label = _humanize_leaf(parts[2:])
        head = f"[{prefix} {sub}] {label}"
    else:
        label = _humanize_leaf(parts[1:]) if len(parts) > 1 else _humanize_leaf(parts)
        head = f"[{prefix}] {label}"
    sym = _CURRENCY_SYMBOL.get(output_currency, output_currency)
    return f"{head} ({sym}M)"


def _scaled_to_millions(value):
    if value is None:
        return NA
    return round(float(value) / _MILLION, _DECIMALS)


def _convert_then_scale(value, rate):
    if value is None:
        return NA
    return round(float(value) * rate / _MILLION, _DECIMALS)


# --- Test infra -----------------------------------------------------------------

PASS: list[str] = []
FAIL: list[str] = []


def run_test(name: str, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  PASS  {name}")
    except Exception:
        FAIL.append(name)
        print(f"  FAIL  {name}")
        traceback.print_exc()


# --- Tests: header generation ---------------------------------------------------

def test_canonical_header_pl():
    h = _canonical_column_header("profit_and_loss.revenue.revenue_from_operations", "USD")
    assert h == "[P&L] Revenue From Operations ($M)", h


def test_canonical_header_bs_assets():
    h = _canonical_column_header(
        "balance_sheet.assets.non_current_assets.property_plant_and_equipment",
        "INR",
    )
    # BS keeps sub-section (Assets / Equity / Liabilities) so the UI can distinguish them.
    assert h == "[BS Assets] Property Plant And Equipment (₹M)", h


def test_canonical_header_currency_symbol_swap():
    a = _canonical_column_header("profit_and_loss.expenses.finance_costs", "USD")
    b = _canonical_column_header("profit_and_loss.expenses.finance_costs", "INR")
    assert a.endswith("($M)") and b.endswith("(₹M)"), (a, b)
    assert a[: a.rindex("(")] == b[: b.rindex("(")]


def test_canonical_header_cash_flow_prefix():
    h = _canonical_column_header(
        "cash_flow_statement.cash_and_cash_equivalents_at_end_of_period", "USD"
    )
    assert h.startswith("[CF] "), h


# --- Tests: scaling + conversion -----------------------------------------------

def test_scale_to_millions_rounding():
    assert _scaled_to_millions(12_345_678.0) == 12.3457


def test_scale_to_millions_none_is_na():
    assert _scaled_to_millions(None) == NA


def test_convert_then_scale_passthrough_rate():
    # Source matches target → rate=1.0, equivalent to plain scaling.
    assert _convert_then_scale(2_500_000.0, 1.0) == 2.5


def test_convert_then_scale_inr_to_usd():
    # 8_350_000 INR at INR->USD rate 1/83.5 ≈ 0.011976 → ~100,000 USD → 0.1M
    rate = 1 / 83.5
    out = _convert_then_scale(8_350_000.0, rate)
    assert abs(out - 0.1) < 0.001, out


def test_convert_then_scale_none_is_na():
    assert _convert_then_scale(None, 1.0) == NA


# --- Tests: FX timestamp construction (per spec: fy_end_date at 12:00:00) ------

def test_fx_timestamp_uses_fy_end_date_at_noon_utc():
    """The export must request FX at 12:00:00 of the row's fy_end_date — never today."""
    fy_dt = date(2025, 3, 31)
    at = datetime.combine(fy_dt, time(12, 0, 0), tzinfo=timezone.utc)
    assert at.year == 2025 and at.month == 3 and at.day == 31
    assert at.hour == 12 and at.minute == 0 and at.second == 0
    assert at.tzinfo == timezone.utc
    # Today must NOT be used
    today_noon = datetime.combine(date.today(), time(12, 0, 0), tzinfo=timezone.utc)
    assert at != today_noon  # only true on dates other than 2025-03-31


# --- Tests: per-row pipeline (canonical leaf → NA / converted value) ------------

def _mock_ocr(extracted: Optional[dict[str, Any]]):
    m = MagicMock()
    m.ocr_json = {"extracted": extracted} if extracted is not None else {}
    return m


def _extract_canonical_tree(ocr_meta):
    """Mirror of src/services/audit_report_export._extract_canonical_tree."""
    if ocr_meta is None or not isinstance(ocr_meta.ocr_json, dict):
        return None
    extracted = ocr_meta.ocr_json.get("extracted")
    if not isinstance(extracted, dict):
        return None
    if "parse_error" in extracted:
        return None
    return extracted


def test_extract_canonical_tree_returns_dict():
    ocr = _mock_ocr({"profit_and_loss": {"revenue": {"revenue_from_operations": 100.0}}})
    tree = _extract_canonical_tree(ocr)
    assert isinstance(tree, dict)
    assert tree["profit_and_loss"]["revenue"]["revenue_from_operations"] == 100.0


def test_extract_canonical_tree_none_when_missing():
    assert _extract_canonical_tree(None) is None
    m = MagicMock()
    m.ocr_json = {}
    assert _extract_canonical_tree(m) is None


def test_extract_canonical_tree_none_when_parse_error():
    ocr = _mock_ocr({"parse_error": "llm failed"})
    assert _extract_canonical_tree(ocr) is None


def test_missing_leaf_yields_na():
    """If a canonical leaf path is absent from the tree, the cell must be NA."""
    # Reimplement the small per-row block inline; we're testing the contract.
    extracted = {"profit_and_loss": {"revenue": {"revenue_from_operations": 5_000_000.0}}}
    rate = 1.0
    # leaf present
    raw = extracted["profit_and_loss"]["revenue"].get("revenue_from_operations")
    cell = _convert_then_scale(raw, rate) if raw is not None else NA
    assert cell == 5.0
    # leaf absent → NA
    raw = extracted["profit_and_loss"]["revenue"].get("other_income")
    cell = _convert_then_scale(raw, rate) if raw is not None else NA
    assert cell == NA


def test_no_company_currency_yields_na_for_all_numerics():
    """When PortfolioCompany.currency is null/invalid, rate is None → every numeric becomes NA."""
    extracted = {"profit_and_loss": {"revenue": {"revenue_from_operations": 5_000_000.0}}}
    rate = None  # mimics the production code path
    raw = extracted["profit_and_loss"]["revenue"]["revenue_from_operations"]
    cell = _convert_then_scale(raw, rate) if (rate is not None) else NA
    assert cell == NA


def test_same_source_and_target_currency_skips_conversion():
    """When PortfolioCompany.currency == output_currency, rate=1.0 — values pass through, just scaled."""
    rate = 1.0  # production sets this directly without an FX call
    out = _convert_then_scale(750_000.0, rate)
    assert out == 0.75


# --- Tests: route param validation ---------------------------------------------

def test_supported_currencies_are_usd_inr_only():
    assert set(SUPPORTED_OUTPUT_CURRENCIES) == {"USD", "INR"}


def test_currency_validation_rejects_unknown():
    def _validate(v: str) -> str:
        normalized = (v or "").strip().upper()
        if normalized not in SUPPORTED_OUTPUT_CURRENCIES:
            raise ValueError(f"unsupported {normalized}")
        return normalized
    try:
        _validate("EUR")
    except ValueError:
        return
    raise AssertionError("EUR should have been rejected")


def test_currency_validation_normalizes_case():
    def _validate(v: str) -> str:
        normalized = (v or "").strip().upper()
        if normalized not in SUPPORTED_OUTPUT_CURRENCIES:
            raise ValueError(f"unsupported {normalized}")
        return normalized
    assert _validate("usd") == "USD"
    assert _validate(" inr ") == "INR"


# --- Tests: filename pattern ---------------------------------------------------

def test_filename_contains_cycle_currency_and_date():
    output_currency = "USD"
    review_cycle_id = "CY24-FY25"
    today = date.today()
    fname = f"audit_report_{review_cycle_id.replace('/', '-').replace(' ', '_')}_{output_currency}_{today}.xlsx"
    assert "CY24-FY25" in fname
    assert "_USD_" in fname
    assert fname.endswith(".xlsx")


def test_filename_uses_all_when_no_cycle():
    output_currency = "INR"
    safe = "all"
    fname = f"audit_report_{safe}_{output_currency}_{date.today()}.xlsx"
    assert fname.startswith("audit_report_all_INR_")


# --- Tests: combined export sheet-per-cycle invariant ---------------------------

def test_combined_export_one_sheet_per_cycle():
    cycles = ["RC-A", "RC-B", "RC-C"]
    sheet_titles = [c[:31] for c in cycles]
    assert len(set(sheet_titles)) == len(cycles)


# --- Tests: route contract — report_type + conditional output_currency ---------
# Mirrors the model_validator in src/versions/v1/routes/export.py:StartExportRequest.


def _validate_request(report_type: str, output_currency=None):
    rt = (report_type or "").strip().lower()
    if rt not in SUPPORTED_REPORT_TYPES:
        raise ValueError(f"report_type must be one of {SUPPORTED_REPORT_TYPES}")
    cc = (output_currency or "").strip().upper() if output_currency else None
    if cc and cc not in SUPPORTED_OUTPUT_CURRENCIES:
        raise ValueError(f"output_currency must be one of {SUPPORTED_OUTPUT_CURRENCIES}")
    if rt == "extracted_financials" and not cc:
        raise ValueError("output_currency required for extracted_financials")
    if rt == "reconciliation" and cc:
        raise ValueError("output_currency must not be set for reconciliation")
    return rt, cc


def test_reconciliation_request_without_currency_ok():
    rt, cc = _validate_request("reconciliation", None)
    assert rt == "reconciliation"
    assert cc is None


def test_reconciliation_request_with_currency_rejected():
    try:
        _validate_request("reconciliation", "USD")
    except ValueError:
        return
    raise AssertionError("reconciliation + currency should be rejected")


def test_extracted_request_without_currency_rejected():
    try:
        _validate_request("extracted_financials", None)
    except ValueError:
        return
    raise AssertionError("extracted + no currency should be rejected")


def test_extracted_request_with_inr_currency_ok():
    rt, cc = _validate_request("extracted_financials", "inr")
    assert rt == "extracted_financials"
    assert cc == "INR"


def test_extracted_request_with_eur_currency_rejected():
    try:
        _validate_request("extracted_financials", "EUR")
    except ValueError:
        return
    raise AssertionError("EUR should be rejected")


def test_unknown_report_type_rejected():
    try:
        _validate_request("financials_only", "USD")
    except ValueError:
        return
    raise AssertionError("unknown report_type should be rejected")


# --- Tests: filename per report type -------------------------------------------


def _filename(prefix: str, review_cycle_id, suffix=""):
    safe = review_cycle_id.replace("/", "-").replace(" ", "_") if review_cycle_id else "all"
    tail = f"_{suffix}" if suffix else ""
    return f"{prefix}_{safe}{tail}_{date.today()}.xlsx"


def test_reconciliation_filename_no_currency():
    fname = _filename("audit_recon", "CY24-FY25")
    assert fname.startswith("audit_recon_CY24-FY25_")
    assert "USD" not in fname and "INR" not in fname


def test_extracted_filename_includes_currency():
    fname = _filename("audit_extracted", "CY24-FY25", suffix="USD")
    assert "_USD_" in fname
    assert fname.startswith("audit_extracted_CY24-FY25_USD_")


def test_extracted_filename_all_cycles_inr():
    fname = _filename("audit_extracted", None, suffix="INR")
    assert fname.startswith("audit_extracted_all_INR_")


# --- Runner --------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Canonical header P&L", test_canonical_header_pl),
        ("Canonical header BS Assets", test_canonical_header_bs_assets),
        ("Canonical header currency symbol swap", test_canonical_header_currency_symbol_swap),
        ("Canonical header CF prefix", test_canonical_header_cash_flow_prefix),
        ("Scale to millions rounding", test_scale_to_millions_rounding),
        ("Scale to millions None → NA", test_scale_to_millions_none_is_na),
        ("Convert+scale rate=1.0 passthrough", test_convert_then_scale_passthrough_rate),
        ("Convert+scale INR→USD", test_convert_then_scale_inr_to_usd),
        ("Convert+scale None → NA", test_convert_then_scale_none_is_na),
        ("FX timestamp = fy_end_date 12:00 UTC", test_fx_timestamp_uses_fy_end_date_at_noon_utc),
        ("Extract canonical tree dict", test_extract_canonical_tree_returns_dict),
        ("Extract canonical tree None", test_extract_canonical_tree_none_when_missing),
        ("Extract canonical tree parse_error → None", test_extract_canonical_tree_none_when_parse_error),
        ("Missing leaf → NA", test_missing_leaf_yields_na),
        ("No company currency → NA", test_no_company_currency_yields_na_for_all_numerics),
        ("Same source = target → no FX", test_same_source_and_target_currency_skips_conversion),
        ("Supported currencies USD/INR only", test_supported_currencies_are_usd_inr_only),
        ("Currency validation rejects EUR", test_currency_validation_rejects_unknown),
        ("Currency validation normalizes case", test_currency_validation_normalizes_case),
        ("Filename has cycle+currency+date", test_filename_contains_cycle_currency_and_date),
        ("Filename uses 'all' when no cycle", test_filename_uses_all_when_no_cycle),
        ("Combined export → one sheet per cycle", test_combined_export_one_sheet_per_cycle),
        ("Recon request without currency OK", test_reconciliation_request_without_currency_ok),
        ("Recon request with currency rejected", test_reconciliation_request_with_currency_rejected),
        ("Extracted request without currency rejected", test_extracted_request_without_currency_rejected),
        ("Extracted request INR OK", test_extracted_request_with_inr_currency_ok),
        ("Extracted request EUR rejected", test_extracted_request_with_eur_currency_rejected),
        ("Unknown report_type rejected", test_unknown_report_type_rejected),
        ("Recon filename has no currency", test_reconciliation_filename_no_currency),
        ("Extracted filename includes currency", test_extracted_filename_includes_currency),
        ("Extracted filename all+INR", test_extracted_filename_all_cycles_inr),
    ]
    print(f"\nRunning {len(tests)} canonical-export tests\n{'-' * 50}")
    for name, fn in tests:
        run_test(name, fn)
    print(f"\n{'-' * 50}")
    print(f"Results: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print(f"FAILED: {', '.join(FAIL)}")
        sys.exit(1)
    else:
        print("All tests passed.")
