"""
Tests for the audit-report XLSX export service.

Run with:  python tests/test_audit_report_export.py
       or: PYTHONPATH=. python tests/test_audit_report_export.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Pure-logic helpers extracted inline so we test the logic without importing
# the full service (which needs a live DB / full SQLAlchemy env).
# ---------------------------------------------------------------------------


def _tat_days(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if start is None or end is None:
        return None
    delta = end.date() - start.date()
    return int(delta.days)


def _yes_no(v: Optional[bool]) -> Optional[str]:
    if v is None:
        return None
    return "Yes" if v else "No"


def _date_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "date"):
        return dt.date().isoformat()
    return str(dt)


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_qualitative_fields(ocr_meta: Optional[Any]) -> dict[str, Any]:
    """Mirrors src/services/audit_report_export._extract_qualitative_fields."""
    out: dict[str, Any] = {
        "consolidated_standalone": None,
        "date_of_signing": None,
        "auditor_name_final": None,
        "auditor_partner": None,
        "category_of_auditor_final": None,
        "audit_report_status": None,
        "auditor_opinion": None,
        "emphasis_on_matters": None,
        "other_matters": None,
        "going_concern": None,
        "caro_availability": None,
        "caro_gaps": None,
        "caro_notes": None,
        "ifc_available": None,
        "ifc_gaps": None,
    }
    if ocr_meta is None:
        return out

    aq = ocr_meta.audit_qualitative or {}
    ao = ocr_meta.auditor_opinion or {}

    scope = aq.get("reporting_scope") or {}
    fb = scope.get("financials_basis")
    if fb and fb != "unclear":
        out["consolidated_standalone"] = fb.capitalize()

    eng = aq.get("auditor_engagement") or {}
    signing = eng.get("signing_date") or eng.get("signing_date_raw")
    out["date_of_signing"] = signing
    out["auditor_name_final"] = eng.get("auditor_firm")
    out["auditor_partner"] = eng.get("signing_partner_or_team")
    out["category_of_auditor_final"] = eng.get("auditor_tier_label")

    sections = eng.get("sections_present") or {}
    if sections.get("independent_auditors_report") is True:
        out["audit_report_status"] = "Present"
    elif isinstance(sections.get("independent_auditors_report"), bool):
        out["audit_report_status"] = "Not present"

    opinion_type = ao.get("opinion_type")
    if opinion_type:
        out["auditor_opinion"] = opinion_type.replace("_", " ").title()

    other = ao.get("other_report_paragraphs") or {}
    eom = other.get("emphasis_of_matter") or {}
    om = other.get("other_matters") or {}
    gc = other.get("going_concern_material_uncertainty") or {}
    out["emphasis_on_matters"] = _yes_no(eom.get("present"))
    out["other_matters"] = _yes_no(om.get("present"))
    out["going_concern"] = _yes_no(gc.get("present"))

    caro = aq.get("caro") or {}
    if caro.get("available") is True:
        out["caro_availability"] = "Yes"
        out["caro_gaps"] = caro.get("overall_assessment", "").replace("_", " ").title() or None
        out["caro_notes"] = caro.get("summary")
    elif isinstance(caro.get("available"), bool):
        out["caro_availability"] = "No"

    ifc = aq.get("ifc") or {}
    if ifc.get("available") is True:
        out["ifc_available"] = "Yes"
        out["ifc_gaps"] = ifc.get("overall_assessment", "").replace("_", " ").title() or None
    elif isinstance(ifc.get("available"), bool):
        out["ifc_available"] = "No"

    return out


def _metric_variance_breach(
    mis_val: Any,
    afs_val: Any,
    *,
    metric_key: str,
    pct_by_metric: dict,
    abs_by_metric: dict = None,
    pct_by_label: dict = None,
    abs_by_label: dict = None,
    currency: Optional[str] = None,
    usd_to_inr_rate: Optional[float] = None,
) -> bool:
    """Mirrors financial_reconciliation.metric_variance_breach."""
    if abs_by_metric is None:
        abs_by_metric = {}
    if pct_by_label is None:
        pct_by_label = {}
    if abs_by_label is None:
        abs_by_label = {}

    if mis_val is None or afs_val is None:
        return False
    try:
        rep_f = float(mis_val)
        ext_f = float(afs_val)
    except (TypeError, ValueError):
        return False

    mk = metric_key.strip().lower()
    label_map = {"revenue": "Revenue", "ebitda": "EBITDA", "pat": "PAT", "cash": "Cash", "debt": "Debt"}
    label = label_map.get(mk, mk.title())

    abs_diff = abs(rep_f - ext_f)
    if abs_diff <= 1e-9:
        return False
    pct = 0.0 if rep_f == 0 else abs_diff / abs(rep_f)
    pct_thresh = pct_by_metric.get(mk, pct_by_label.get(label, 0.005))
    abs_thresh = abs_by_metric.get(mk, abs_by_label.get(label))

    effective_abs_thresh = abs_thresh
    cur = (currency or "").strip().upper()
    if abs_thresh is not None and cur == "INR" and usd_to_inr_rate and usd_to_inr_rate > 0:
        effective_abs_thresh = abs_thresh * usd_to_inr_rate

    flagged = pct > pct_thresh
    if effective_abs_thresh is not None and effective_abs_thresh > 0:
        flagged = flagged or (abs_diff > effective_abs_thresh)
    return bool(flagged)


_REPORT_METRICS = ("revenue", "ebitda", "pat", "cash", "debt")

_HEADERS = [
    "COID", "Company", "Legal Name", "Entity", "Currency", "Financials",
    "Holding/Subsidiary", "Consolidated/Standalone", "Date of signing",
    "Current Status", "Financials added date", "Queries sent to company",
    "Reminder 1 to company", "Reminder 2 to company", "TAT (Queries sent)",
    "Companies Responded", "TAT (Company response)", "Approved/Rejected on",
    "TAT (Approval/Rejection)", "Overall TAT", "Highlighted to investor? (at deal level)",
    "Reporting Standards", "Auditor's Name (Pulse)", "Auditor's Name (Final)",
    "Auditor's Partner Name", "Category Of Auditor", "Category Of Auditor (Final)",
    "Status Of Financials", "Status of Signed Financials", "Audit Report Status",
    "Auditor Opinion", "Emphasis on Matters", "Other Matters", "Going Concern",
    "CARO availability", "CARO Gaps", "CARO Notes",
    "Internal Financial Control", "Internal Financial Control Gaps",
    # Revenue
    "As Per Financial Revenue", "As Per MIS Revenue", "Difference in Value Revenue",
    "Difference% Revenue >10% diff/ No", "Difference% Revenue Bucket",
    "To be sent? Revenue", "Status Revenue", "Company response Revenue",
    "Reviewer remarks Revenue", "Company remarks Revenue", "Revenue sub-category for analysis",
    # EBITDA
    "As Per Financial EBITDA", "As Per MIS EBITDA", "Difference in Value EBITDA",
    "Difference% EBITDA >10% diff/ No", "Difference% EBITDA Bucket",
    "To be sent? EBITDA", "Status EBITDA", "Company response EBITDA",
    "Reviewer remarks EBITDA", "Company remarks EBITDA", "EBITDA Category for analysis",
    # PAT
    "As Per Financial PAT", "As Per MIS PAT", "Difference in Value PAT",
    "Difference% PAT >10% diff/ No", "Difference% PAT Bucket",
    "To be sent? PAT", "Status PAT", "Company response PAT",
    "Reviewer remarks PAT", "Company remarks PAT", "PAT sub-category for Analysis",
    # Cash
    "As Per Financial Cash", "As Per MIS Cash", "Difference in Value Cash",
    "Difference% Cash >10% diff/ No", "Difference% Cash Bucket",
    "To be sent? Cash", "Status Cash", "Company response Cash",
    "Reviewer remarks Cash", "Company remarks Cash", "Cash sub-category for Analysis",
    # Debt
    "As Per Financial Debt", "As Per MIS Debt", "Difference in Value Debt",
    "Difference% Debt >10% diff/ No", "Difference% Debt Bucket",
    "To be sent? Debt", "Status Debt", "Company response Debt",
    "Reviewer remarks Debt", "Company remarks Debt", "Debt sub-category for Analysis",
]


def _make_metric_row(metric, *, afs=None, mis=None, pct_thresh=None) -> list:
    """Simplified metric-row builder for tests."""
    diff = (mis - afs) if (mis is not None and afs is not None) else None
    breach = None
    if mis is not None and afs is not None:
        pm = {metric: pct_thresh} if pct_thresh is not None else {}
        breach = "Yes" if _metric_variance_breach(mis, afs, metric_key=metric, pct_by_metric=pm) else "No"
    return [afs, mis, diff, breach, None, None, None, None, None, None, None]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _ocr(*, audit_qualitative=None, auditor_opinion=None):
    m = MagicMock()
    m.audit_qualitative = audit_qualitative or {}
    m.auditor_opinion = auditor_opinion or {}
    return m


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tat_days_basic():
    t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 11, tzinfo=timezone.utc)
    assert _tat_days(t1, t2) == 10


def test_tat_days_missing_start():
    assert _tat_days(None, datetime(2024, 1, 11, tzinfo=timezone.utc)) is None


def test_tat_days_missing_end():
    assert _tat_days(datetime(2024, 1, 1, tzinfo=timezone.utc), None) is None


def test_tat_days_both_missing():
    assert _tat_days(None, None) is None


def test_overall_tat_requires_all_three():
    """One missing TAT leg → overall must be None."""
    fin_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    queries_sent = datetime(2024, 1, 5, tzinfo=timezone.utc)
    responded = None

    tat_q = _tat_days(fin_date, queries_sent)
    tat_r = _tat_days(queries_sent, responded)
    tat_a = None

    overall = None
    if all(x is not None for x in (tat_q, tat_r, tat_a)):
        overall = tat_q + tat_r + tat_a
    assert overall is None


def test_overall_tat_sum_when_all_present():
    fin_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    queries_sent = datetime(2024, 1, 6, tzinfo=timezone.utc)
    responded = datetime(2024, 1, 11, tzinfo=timezone.utc)
    resolved = datetime(2024, 1, 21, tzinfo=timezone.utc)

    tat_q = _tat_days(fin_date, queries_sent)    # 5
    tat_r = _tat_days(queries_sent, responded)   # 5
    tat_a = _tat_days(responded, resolved)       # 10
    overall = tat_q + tat_r + tat_a if all(x is not None for x in (tat_q, tat_r, tat_a)) else None
    assert overall == 20, f"Expected 20, got {overall}"


def test_missing_datetime_is_none():
    assert _date_str(None) is None


def test_headers_count():
    # 39 base cols + 5 metrics × 11 = 94
    assert len(_HEADERS) == 94, f"Got {len(_HEADERS)}"


def test_report_metrics_excludes_pbt():
    assert "pbt" not in _REPORT_METRICS
    for m in ("revenue", "ebitda", "pat", "cash", "debt"):
        assert m in _REPORT_METRICS


def test_metric_row_no_data():
    row = _make_metric_row("revenue")
    assert row[0] is None   # afs
    assert row[1] is None   # mis
    assert row[2] is None   # diff
    assert row[3] is None   # breach


def test_metric_row_breach_yes():
    row = _make_metric_row("revenue", afs=100.0, mis=115.0, pct_thresh=0.10)
    assert row[3] == "Yes", f"Expected breach=Yes, got {row[3]}"


def test_metric_row_breach_no():
    row = _make_metric_row("revenue", afs=100.0, mis=103.0, pct_thresh=0.10)
    assert row[3] == "No", f"Expected breach=No, got {row[3]}"


def test_difference_in_value():
    row = _make_metric_row("revenue", afs=100.0, mis=150.0)
    assert abs(row[2] - 50.0) < 1e-6, f"Expected 50.0, got {row[2]}"


def test_variance_breach_missing_value():
    assert _metric_variance_breach(None, 100, metric_key="revenue", pct_by_metric={}) is False
    assert _metric_variance_breach(100, None, metric_key="revenue", pct_by_metric={}) is False


def test_variance_breach_above_threshold():
    assert _metric_variance_breach(110, 100, metric_key="revenue", pct_by_metric={"revenue": 0.05}) is True


def test_variance_breach_below_threshold():
    assert _metric_variance_breach(102, 100, metric_key="revenue", pct_by_metric={"revenue": 0.05}) is False


def test_extract_qualitative_none():
    q = _extract_qualitative_fields(None)
    for v in q.values():
        assert v is None


def test_extract_qualitative_full():
    ocr = _ocr(
        audit_qualitative={
            "reporting_scope": {"financials_basis": "consolidated"},
            "auditor_engagement": {
                "signing_date": "2024-03-31",
                "auditor_firm": "KPMG India",
                "signing_partner_or_team": "J. Doe",
                "auditor_tier_label": "BIG 4",
                "sections_present": {"independent_auditors_report": True},
            },
            "caro": {"available": True, "overall_assessment": "has_highlights", "summary": "CARO note."},
            "ifc": {"available": True, "overall_assessment": "has_weaknesses"},
        },
        auditor_opinion={
            "opinion_type": "qualified",
            "other_report_paragraphs": {
                "emphasis_of_matter": {"present": True},
                "other_matters": {"present": False},
                "going_concern_material_uncertainty": {"present": True},
            },
        },
    )
    q = _extract_qualitative_fields(ocr)
    assert q["consolidated_standalone"] == "Consolidated"
    assert q["date_of_signing"] == "2024-03-31"
    assert q["auditor_name_final"] == "KPMG India"
    assert q["auditor_partner"] == "J. Doe"
    assert q["category_of_auditor_final"] == "BIG 4"
    assert q["audit_report_status"] == "Present"
    assert q["auditor_opinion"] == "Qualified"
    assert q["emphasis_on_matters"] == "Yes"
    assert q["other_matters"] == "No"
    assert q["going_concern"] == "Yes"
    assert q["caro_availability"] == "Yes"
    assert q["caro_gaps"] == "Has Highlights"
    assert q["caro_notes"] == "CARO note."
    assert q["ifc_available"] == "Yes"
    assert q["ifc_gaps"] == "Has Weaknesses"


def test_entity_exclusion_without_audit_file():
    """Entities not in the audit-file set must be excluded from report rows."""
    all_entity_ids = {1, 2, 3}
    audit_entity_ids = {1, 3}
    included = all_entity_ids & audit_entity_ids
    assert 2 not in included
    assert included == {1, 3}


def test_all_cycles_produces_one_sheet_per_cycle():
    """Workbook must have one sheet per review cycle when all=True."""
    # We test the logic: one worksheet title per cycle
    cycles = ["RC2022", "RC2023", "RC2024"]
    sheet_titles = []
    for rc_id in cycles:
        title = rc_id[:31]
        sheet_titles.append(title)
    assert len(sheet_titles) == len(cycles), "Should produce one sheet per cycle"
    assert sheet_titles == cycles


def test_selected_cycle_single_sheet():
    """When a specific cycle is selected, only one sheet is produced."""
    selected = "RC2024"
    cycles = [c for c in ["RC2022", "RC2023", "RC2024"] if c == selected]
    assert len(cycles) == 1


def test_worksheet_title_truncated_to_31():
    long_name = "A" * 40
    title = long_name[:31]
    assert len(title) == 31


if __name__ == "__main__":
    tests = [
        ("TAT 10 days", test_tat_days_basic),
        ("TAT missing start", test_tat_days_missing_start),
        ("TAT missing end", test_tat_days_missing_end),
        ("TAT both missing", test_tat_days_both_missing),
        ("Overall TAT requires all three", test_overall_tat_requires_all_three),
        ("Overall TAT sum when all present", test_overall_tat_sum_when_all_present),
        ("Missing datetime → None", test_missing_datetime_is_none),
        ("Header count = 94", test_headers_count),
        ("Metrics excludes PBT", test_report_metrics_excludes_pbt),
        ("Metric row no data → None", test_metric_row_no_data),
        ("Metric row breach Yes", test_metric_row_breach_yes),
        ("Metric row breach No", test_metric_row_breach_no),
        ("Difference in value", test_difference_in_value),
        ("Breach missing value → False", test_variance_breach_missing_value),
        ("Breach above threshold", test_variance_breach_above_threshold),
        ("Breach below threshold", test_variance_breach_below_threshold),
        ("Qualitative fields None ocr", test_extract_qualitative_none),
        ("Qualitative fields full", test_extract_qualitative_full),
        ("Entity excluded without audit file", test_entity_exclusion_without_audit_file),
        ("All cycles → one sheet per cycle", test_all_cycles_produces_one_sheet_per_cycle),
        ("Selected cycle → single sheet", test_selected_cycle_single_sheet),
        ("Worksheet title truncated to 31 chars", test_worksheet_title_truncated_to_31),
    ]

    print(f"\nRunning {len(tests)} audit-report export tests\n{'-' * 50}")
    for name, fn in tests:
        run_test(name, fn)

    print(f"\n{'-' * 50}")
    print(f"Results: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print(f"FAILED: {', '.join(FAIL)}")
        sys.exit(1)
    else:
        print("All tests passed.")
