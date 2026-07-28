"""
Async XLSX audit-report export service.

Generates one worksheet per review cycle (or a single worksheet for a specific cycle).
Each row = one company + entity + review cycle combination where at least one audit/financial
file (non-org-chart) exists for the entity.
"""
from __future__ import annotations

import calendar
import io
import logging
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.file_filters import exclude_org_chart_uploads_clause
from src.db.models import (
    APP_SCHEMA,
    Entity,
    ExportJob,
    File,
    FileOCRMetadata,
    FinancialDataSnowflake,
    FinancialMetricReconciliation,
    PortfolioCompany,
    ReviewCycle,
)
from src.db.session import async_session
from src.services.financial_audit_schema import (
    list_audit_financials_numeric_leaf_paths,
    load_audit_financials_schema,
    pick_first_numeric_deep,
)
from src.services.financial_reconciliation import (
    load_variance_threshold_maps,
    metric_variance_breach,
    fetch_usd_to_inr_rate,
    snowflake_currency,
    _snowflake_metric_value,
)
from src.services.fy_end import fy_end_last_day, normalize_fy_end, fy_end_from_legacy_date
from src.services.fx_service import FxConversionUnavailable, fetch_historical_rate

logger = logging.getLogger(__name__)

JOB_TYPE_AUDIT_REPORT = "audit_report"  # reconciliation report (legacy default)
JOB_TYPE_EXTRACTED_FINANCIALS = "audit_extracted_financials"
REPORT_TYPE_RECONCILIATION = "reconciliation"
REPORT_TYPE_EXTRACTED_FINANCIALS = "extracted_financials"
SUPPORTED_REPORT_TYPES: tuple[str, ...] = (REPORT_TYPE_RECONCILIATION, REPORT_TYPE_EXTRACTED_FINANCIALS)
_REPORT_TYPE_TO_JOB_TYPE = {
    REPORT_TYPE_RECONCILIATION: JOB_TYPE_AUDIT_REPORT,
    REPORT_TYPE_EXTRACTED_FINANCIALS: JOB_TYPE_EXTRACTED_FINANCIALS,
}

_REPORT_METRICS = ("revenue", "ebitda", "pat", "cash", "debt")

SUPPORTED_OUTPUT_CURRENCIES: tuple[str, ...] = ("USD", "INR")
_MILLION = 1_000_000.0
_CR = 10_000_000.0        # 1 Crore
_DECIMALS = 4
_CURRENCY_SYMBOL = {"USD": "$", "INR": "₹"}
NA = "NA"

# Column headers in order
_HEADERS = [
    "COID",
    "Company",
    "Legal Name",
    "Entity",
    "Audited FS Currency",
    "Currency",
    "Financials",
    "Holding/Subsidiary",
    "Consolidated/Standalone",
    "Date of signing",
    "Current Status",
    "Financials added date",
    "Queries sent to company",
    "Reminder 1 to company",
    "Reminder 2 to company",
    "TAT (Queries sent)",
    "Companies Responded",
    "TAT (Company response)",
    "Approved/Rejected on",
    "TAT (Approval/Rejection)",
    "Overall TAT",
    "Highlighted to investor? (at deal level)",
    "Reporting Standards",
    "Auditor's Name (Final)",
    "Auditor's Partner Name",
    "Category Of Auditor (Final)",
    "Status Of Financials",
    "Status of Signed Financials",
    "Audit Report Status",
    "Auditor Opinion",
    "Emphasis on Matters",
    "Other Matters",
    "Going Concern",
    "CARO availability",
    "CARO Gaps",
    "CARO Notes",
    "Internal Financial Control",
    "Internal Financial Control Gaps",
    # Revenue block
    "As Per Financial Revenue",
    "As Per MIS Revenue",
    "Difference in Value Revenue",
    "Difference% Revenue >10% diff/ No",
    "Difference% Revenue Bucket",
    "To be sent? Revenue",
    "Status Revenue",
    "Company response Revenue",
    "Reviewer remarks Revenue",
    "Company remarks Revenue",
    "Revenue sub-category for analysis",
    # EBITDA block
    "As Per Financial EBITDA",
    "As Per MIS EBITDA",
    "Difference in Value EBITDA",
    "Difference% EBITDA >10% diff/ No",
    "Difference% EBITDA Bucket",
    "To be sent? EBITDA",
    "Status EBITDA",
    "Company response EBITDA",
    "Reviewer remarks EBITDA",
    "Company remarks EBITDA",
    "EBITDA Category for analysis",
    # PAT block
    "As Per Financial PAT",
    "As Per MIS PAT",
    "Difference in Value PAT",
    "Difference% PAT >10% diff/ No",
    "Difference% PAT Bucket",
    "To be sent? PAT",
    "Status PAT",
    "Company response PAT",
    "Reviewer remarks PAT",
    "Company remarks PAT",
    "PAT sub-category for Analysis",
    # Cash block
    "As Per Financial Cash",
    "As Per MIS Cash",
    "Difference in Value Cash",
    "Difference% Cash >10% diff/ No",
    "Difference% Cash Bucket",
    "To be sent? Cash",
    "Status Cash",
    "Company response Cash",
    "Reviewer remarks Cash",
    "Company remarks Cash",
    "Cash sub-category for Analysis",
    # Debt block
    "As Per Financial Debt",
    "As Per MIS Debt",
    "Difference in Value Debt",
    "Difference% Debt >10% diff/ No",
    "Difference% Debt Bucket",
    "To be sent? Debt",
    "Status Debt",
    "Company response Debt",
    "Reviewer remarks Debt",
    "Company remarks Debt",
    "Debt sub-category for Analysis",
]


_SECTION_PREFIX = {
    "profit_and_loss": "P&L",
    "balance_sheet": "BS",
    "cash_flow_statement": "CF",
}


def _humanize_leaf(path_parts: list[str]) -> str:
    leaf = path_parts[-1]
    return leaf.replace("_", " ").strip().title()


def _canonical_column_header(dotted_path: str, output_currency: str) -> str:
    parts = dotted_path.split(".")
    section = parts[0] if parts else ""
    prefix = _SECTION_PREFIX.get(section, section.replace("_", " ").title())
    # Balance Sheet: include sub-section (assets / equity / liabilities)
    if section == "balance_sheet" and len(parts) >= 3:
        sub = parts[1].title()
        label = _humanize_leaf(parts[2:])
        head = f"[{prefix} {sub}] {label}"
    else:
        label = _humanize_leaf(parts[1:]) if len(parts) > 1 else _humanize_leaf(parts)
        head = f"[{prefix}] {label}"
    sym = _CURRENCY_SYMBOL.get(output_currency, output_currency)
    return f"{head} ({sym}M)"


def _row_fx_date(pc: PortfolioCompany) -> Optional[date]:
    """Resolve the date to use for FX conversion: fy_end_date if parseable, else last day of fy_end."""
    legacy = fy_end_from_legacy_date(pc.fy_end_date)
    if legacy:
        try:
            return fy_end_last_day(legacy)
        except ValueError:
            pass
    normalized = normalize_fy_end(pc.fy_end)
    if normalized:
        try:
            return fy_end_last_day(normalized)
        except ValueError:
            return None
    return None


def _scaled_to_millions(value: Optional[float]) -> Any:
    if value is None:
        return NA
    return round(float(value) / _MILLION, _DECIMALS)


def _convert_then_scale(value: Optional[float], rate: float) -> Any:
    if value is None:
        return NA
    return round(float(value) * rate / _MILLION, _DECIMALS)


def _scale_by_currency(value: Optional[float], currency: Optional[str]) -> Any:
    """Scale a raw amount based on currency.

    INR          → divide by 1 Cr  (10,000,000)
    USD          → divide by 1 M   (1,000,000)
    anything else → show full number (no scaling)
    """
    if value is None:
        return None
    cur = (currency or "").strip().upper()
    if cur == "INR":
        return round(float(value) / _CR, _DECIMALS)
    if cur == "USD":
        return round(float(value) / _MILLION, _DECIMALS)
    return round(float(value), _DECIMALS)


def _convert_and_scale(
    value: Optional[float],
    from_currency: Optional[str],
    rate: float,
    output_currency: str,
) -> Any:
    """Apply FX rate then scale to Cr (INR) or M (USD/other) for the output currency."""
    if value is None:
        return NA
    converted = float(value) * rate
    divisor = _CR if output_currency == "INR" else _MILLION
    return round(converted / divisor, _DECIMALS)


def _tat_days(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if start is None or end is None:
        return None
    delta = end.date() - start.date() if hasattr(start, "date") else None
    if delta is None:
        try:
            delta = end - start
        except TypeError:
            return None
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


def _extract_qualitative_fields(ocr_meta: Optional[FileOCRMetadata]) -> dict[str, Any]:
    """Pull compliance/audit fields from FileOCRMetadata JSON blobs."""
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
        "reporting_standards": None,
        "financials_status": None,
    }
    if ocr_meta is None:
        return out

    aq = ocr_meta.audit_qualitative or {}
    ao = ocr_meta.auditor_opinion or {}

    # Consolidated/Standalone — reporting_scope.financials_basis
    scope = aq.get("reporting_scope") or {}
    fb = scope.get("financials_basis")
    if fb and fb != "unclear":
        out["consolidated_standalone"] = fb.capitalize()

    # Auditor engagement fields
    eng = aq.get("auditor_engagement") or {}
    signing = eng.get("signing_date") or eng.get("signing_date_raw")
    out["date_of_signing"] = signing
    out["auditor_name_final"] = eng.get("auditor_firm")
    out["auditor_partner"] = eng.get("signing_partner_or_team")
    out["category_of_auditor_final"] = eng.get("auditor_tier_label")

    # Audit report section present
    sections = eng.get("sections_present") or {}
    if sections.get("independent_auditors_report") is True:
        out["audit_report_status"] = "Present"
    elif isinstance(sections.get("independent_auditors_report"), bool):
        out["audit_report_status"] = "Not present"

    # Opinion type
    opinion_type = ao.get("opinion_type")
    if opinion_type:
        out["auditor_opinion"] = opinion_type.replace("_", " ").title()

    # Other report paragraphs
    other = ao.get("other_report_paragraphs") or {}
    eom = other.get("emphasis_of_matter") or {}
    om = other.get("other_matters") or {}
    gc = other.get("going_concern_material_uncertainty") or {}
    out["emphasis_on_matters"] = _yes_no(eom.get("present"))
    out["other_matters"] = _yes_no(om.get("present"))
    out["going_concern"] = _yes_no(gc.get("present"))

    # CARO
    caro = aq.get("caro") or {}
    if caro.get("available") is True:
        out["caro_availability"] = "Yes"
        out["caro_gaps"] = caro.get("overall_assessment", "").replace("_", " ").title() or None
        out["caro_notes"] = caro.get("summary")
    elif isinstance(caro.get("available"), bool):
        out["caro_availability"] = "No"

    # IFC
    ifc = aq.get("ifc") or {}
    if ifc.get("available") is True:
        out["ifc_available"] = "Yes"
        out["ifc_gaps"] = ifc.get("overall_assessment", "").replace("_", " ").title() or None
    elif isinstance(ifc.get("available"), bool):
        out["ifc_available"] = "No"

    # Reporting standards
    rs = aq.get("reporting_standards")
    if rs:
        out["reporting_standards"] = rs

    # Financials status (Signed / Draft)
    fs = aq.get("financials_status")
    if fs:
        out["financials_status"] = fs

    return out


def _metric_row(
    metric: str,
    fmr: Optional[FinancialMetricReconciliation],
    fds: Optional[FinancialDataSnowflake],
    pct_by_metric: dict,
    abs_by_metric: dict,
    pct_by_label: dict,
    abs_by_label: dict,
    usd_to_inr_rate: Optional[float],
    pc_currency: Optional[str] = None,
    afs_currency: Optional[str] = None,
) -> list[Any]:
    """Return the 11-cell block for one metric.

    Scale currency: use pc_currency (MIS) when available, fall back to afs_currency (Audited FS).
    INR → Crore, USD/other → Million. No FX conversion.
    """
    afs_raw = _safe_float(fmr.afs_amount) if fmr else None
    mis_raw = _safe_float(_snowflake_metric_value(fds, metric)) if fds else None
    snowflake_ccy = snowflake_currency(fds) if fds else None

    diff_raw: Optional[float] = None
    breach_str: Optional[str] = None
    if mis_raw is not None and afs_raw is not None:
        diff_raw = mis_raw - afs_raw
        breached = metric_variance_breach(
            mis_raw,
            afs_raw,
            metric_key=metric,
            pct_by_metric=pct_by_metric,
            abs_by_metric=abs_by_metric,
            pct_by_label=pct_by_label,
            abs_by_label=abs_by_label,
            currency=snowflake_ccy,
            usd_to_inr_rate=usd_to_inr_rate,
        )
        breach_str = "Yes" if breached else "No"

    scale_ccy = pc_currency if pc_currency else afs_currency
    afs = _scale_by_currency(afs_raw, scale_ccy)
    mis = _scale_by_currency(mis_raw, scale_ccy)
    diff = _scale_by_currency(diff_raw, scale_ccy)

    bucket = fmr.variance_category if fmr else None
    to_send = _yes_no(fmr.enable) if fmr else None
    status = fmr.status if fmr else None
    company_response = fmr.company_response if fmr else None
    reviewer_remarks = fmr.reviewer_remarks if fmr else None

    return [
        afs,
        mis,
        diff,
        breach_str,
        bucket,
        to_send,
        status,
        company_response,
        reviewer_remarks,
        None,          # Company remarks — empty for now
        bucket,        # sub-category for analysis = same as bucket
    ]


def _build_row(
    pc: PortfolioCompany,
    entity: Entity,
    review_cycle_label: str,
    fds: Optional[FinancialDataSnowflake],
    fmr_by_metric: dict[str, FinancialMetricReconciliation],
    ocr_meta: Optional[FileOCRMetadata],
    financials_added_date: Optional[datetime],
    pct_by_metric: dict,
    abs_by_metric: dict,
    pct_by_label: dict,
    abs_by_label: dict,
    usd_to_inr_rate: Optional[float],
    original_currency: Optional[str] = None,
) -> list[Any]:
    mis_currency = pc.currency or None

    entity_fy_end = normalize_fy_end(entity.fy_end)
    financials_date: Optional[str] = None
    if entity_fy_end:
        try:
            financials_date = fy_end_last_day(entity_fy_end).isoformat()
        except ValueError:
            pass

    q = _extract_qualitative_fields(ocr_meta)

    # Status of Signed Financials — only meaningful when financials are signed.
    # "Within due date" if signing date ≤ 6 months after FY year-end, else "Post due date".
    status_of_signed_financials: Optional[str] = None
    if q["financials_status"] == "Signed" and q["date_of_signing"] and entity_fy_end:
        try:
            signing_date_parsed: Optional[date] = None
            raw_signing = q["date_of_signing"]
            if isinstance(raw_signing, str) and len(raw_signing) >= 10:
                signing_date_parsed = date.fromisoformat(raw_signing[:10])
            elif isinstance(raw_signing, date):
                signing_date_parsed = raw_signing
            if signing_date_parsed:
                year_end_date = fy_end_last_day(entity_fy_end)
                # 6-month threshold: same day 6 months later (using month arithmetic)
                due_month = year_end_date.month + 6
                due_year = year_end_date.year + (due_month - 1) // 12
                due_month = (due_month - 1) % 12 + 1
                import calendar
                max_day = calendar.monthrange(due_year, due_month)[1]
                due_date = date(due_year, due_month, min(year_end_date.day, max_day))
                status_of_signed_financials = (
                    "Within due date" if signing_date_parsed <= due_date else "Post due date"
                )
        except (ValueError, TypeError):
            pass

    fin_date = financials_added_date
    queries_sent = entity.discrepancy_email_sent_at
    reminder1 = entity.reminder_1_sent_at
    reminder2 = entity.reminder_2_sent_at
    responded = entity.first_reply_received_at
    resolved = entity.resolved_at

    tat_queries = _tat_days(fin_date, queries_sent)
    tat_response = _tat_days(queries_sent, responded)
    tat_approval = _tat_days(responded, resolved)
    overall_tat: Optional[int] = None
    if tat_queries is not None and tat_response is not None and tat_approval is not None:
        overall_tat = tat_queries + tat_response + tat_approval

    base: list[Any] = [
        pc.company_id,                        # 1  COID
        pc.name,                              # 2  Company
        entity.name,                          # 3  Legal Name
        entity.name,                          # 4  Entity
        original_currency,                    # 5  Audited FS Currency
        mis_currency,                         # 6  Currency
        financials_date,                      # 7  Financials
        entity.entity_type,                   # 7  Holding/Subsidiary
        q["consolidated_standalone"],         # 8  Consolidated/Standalone
        q["date_of_signing"],                 # 9  Date of signing
        entity.status,                        # 10 Current Status
        _date_str(fin_date),                  # 11 Financials added date
        _date_str(queries_sent),              # 12 Queries sent to company
        _date_str(reminder1),                 # 13 Reminder 1
        _date_str(reminder2),                 # 14 Reminder 2
        tat_queries,                          # 15 TAT (Queries sent)
        _date_str(responded),                 # 16 Companies Responded
        tat_response,                         # 17 TAT (Company response)
        _date_str(resolved),                  # 18 Approved/Rejected on
        tat_approval,                         # 19 TAT (Approval/Rejection)
        overall_tat,                          # 20 Overall TAT
        None,                                 # 21 Highlighted to investor (empty)
        q["reporting_standards"],             # 22 Reporting Standards
        q["auditor_name_final"],              # 23 Auditor's Name (Final)
        q["auditor_partner"],                 # 24 Auditor's Partner Name
        q["category_of_auditor_final"],       # 25 Category Of Auditor (Final)
        q["financials_status"],               # 26 Status Of Financials
        status_of_signed_financials,          # 27 Status of Signed Financials
        q["audit_report_status"],             # 28 Audit Report Status
        q["auditor_opinion"],                 # 29 Auditor Opinion
        q["emphasis_on_matters"],             # 30 Emphasis on Matters
        q["other_matters"],                   # 31 Other Matters
        q["going_concern"],                   # 32 Going Concern
        q["caro_availability"],               # 33 CARO availability
        q["caro_gaps"],                       # 34 CARO Gaps
        q["caro_notes"],                      # 35 CARO Notes
        q["ifc_available"],                   # 36 Internal Financial Control
        q["ifc_gaps"],                        # 37 Internal Financial Control Gaps
    ]

    for metric in _REPORT_METRICS:
        base.extend(
            _metric_row(
                metric,
                fmr_by_metric.get(metric),
                fds,
                pct_by_metric,
                abs_by_metric,
                pct_by_label,
                abs_by_label,
                usd_to_inr_rate,
                pc_currency=mis_currency,
                afs_currency=original_currency,
            )
        )

    return base


def _new_workbook_with_header(headers: list[str]):
    """Open a fresh openpyxl workbook and prep header styling (factored helper)."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="2563EB")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb, hdr_font, hdr_fill, hdr_align


async def _load_review_cycles(
    db: AsyncSession, review_cycle_id: Optional[str]
) -> list[ReviewCycle]:
    rc_stmt = select(ReviewCycle).order_by(ReviewCycle.starts_at.asc().nullslast())
    if review_cycle_id:
        rc_stmt = rc_stmt.where(ReviewCycle.id == review_cycle_id)
    return list((await db.execute(rc_stmt)).scalars().all())


def _filename(prefix: str, review_cycle_id: Optional[str], *, suffix: str = "") -> str:
    safe = (
        review_cycle_id.replace("/", "-").replace(" ", "_") if review_cycle_id else "all"
    )
    tail = f"_{suffix}" if suffix else ""
    return f"{prefix}_{safe}{tail}_{date.today()}.xlsx"


async def _generate_reconciliation_workbook(
    db: AsyncSession,
    review_cycle_id: Optional[str],
) -> tuple[io.BytesIO, str]:
    """Reconciliation report — amounts scaled per PortfolioCompany.currency (INR→Cr, USD/other→M)."""
    import openpyxl

    wb, hdr_font, hdr_fill, hdr_align = _new_workbook_with_header(_HEADERS)

    review_cycles = await _load_review_cycles(db, review_cycle_id)
    if not review_cycles:
        wb_empty = openpyxl.Workbook()
        ws_empty = wb_empty.active
        ws_empty.title = "Audit Report"
        buf = io.BytesIO()
        wb_empty.save(buf)
        buf.seek(0)
        return buf, _filename("audit_recon", review_cycle_id)

    pct_by_metric, abs_by_metric, pct_by_label, abs_by_label = await load_variance_threshold_maps(db)
    usd_to_inr_rate = await fetch_usd_to_inr_rate(db)

    for rc in review_cycles:
        rc_label = rc.name or rc.id
        ws = wb.create_sheet(title=(rc_label or rc.id)[:31])
        for ci, h in enumerate(_HEADERS, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = hdr_align
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"

        await _fill_reconciliation_worksheet(
            db=db,
            ws=ws,
            review_cycle_id=rc.id,
            review_cycle_label=rc_label,
            pct_by_metric=pct_by_metric,
            abs_by_metric=abs_by_metric,
            pct_by_label=pct_by_label,
            abs_by_label=abs_by_label,
            usd_to_inr_rate=usd_to_inr_rate,
        )

    if not wb.sheetnames:
        wb.create_sheet("Audit Report")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, _filename("audit_recon", review_cycle_id)


_EXTRACTED_IDENTITY_HEADERS = [
    "COID",
    "Company",
    "Legal Name",
    "Entity",
    "Source Currency",
    "Financial Year End",
    "FX Rate Applied",
    "FX Date",
]


async def _generate_extracted_financials_workbook(
    db: AsyncSession,
    review_cycle_id: Optional[str],
    output_currency: str,
) -> tuple[io.BytesIO, str]:
    """Identity columns + canonical P&L/BS/CF leaves, FX-converted to ``output_currency`` and scaled to millions.

    Raises ``FxConversionUnavailable`` if any required FX rate cannot be obtained.
    """
    import openpyxl

    output_currency = output_currency.strip().upper()
    if output_currency not in SUPPORTED_OUTPUT_CURRENCIES:
        raise ValueError(f"unsupported output_currency: {output_currency!r}")

    schema = await load_audit_financials_schema(db)
    canonical_leaf_paths = list_audit_financials_numeric_leaf_paths(schema)
    canonical_headers = [_canonical_column_header(p, output_currency) for p in canonical_leaf_paths]
    full_headers = list(_EXTRACTED_IDENTITY_HEADERS) + canonical_headers

    wb, hdr_font, hdr_fill, hdr_align = _new_workbook_with_header(full_headers)

    review_cycles = await _load_review_cycles(db, review_cycle_id)
    if not review_cycles:
        wb_empty = openpyxl.Workbook()
        ws_empty = wb_empty.active
        ws_empty.title = "Extracted Financials"
        buf = io.BytesIO()
        wb_empty.save(buf)
        buf.seek(0)
        return buf, _filename("audit_extracted", review_cycle_id, suffix=output_currency)

    for rc in review_cycles:
        rc_label = rc.name or rc.id
        ws = wb.create_sheet(title=(rc_label or rc.id)[:31])
        for ci, h in enumerate(full_headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = hdr_align
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"

        await _fill_extracted_financials_worksheet(
            db=db,
            ws=ws,
            review_cycle_id=rc.id,
            output_currency=output_currency,
            canonical_leaf_paths=canonical_leaf_paths,
        )

    if not wb.sheetnames:
        wb.create_sheet("Extracted Financials")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, _filename("audit_extracted", review_cycle_id, suffix=output_currency)


async def _load_audit_entities_for_cycle(
    db: AsyncSession,
    review_cycle_id: str,
) -> tuple[
    list[PortfolioCompany],
    list[Entity],
    set[int],
    dict[int, datetime],
    dict[int, FileOCRMetadata],
    dict[int, Optional[str]],
]:
    """Entity-load for a cycle: all entities regardless of file presence.

    Returns:
      companies, entities (all), audit_entity_ids (those with files),
      financials_added_date_map, ocr_by_entity, original_currency_by_entity.
    Entities with no audit file will have empty entries in the last three dicts.
    """
    pc_stmt = (
        select(PortfolioCompany)
        .where(PortfolioCompany.review_cycle_id == review_cycle_id)
    )
    companies = list((await db.execute(pc_stmt)).scalars().all())
    if not companies:
        return [], [], set(), {}, {}, {}

    company_ids = [pc.id for pc in companies]

    # All entities for these companies — no file filter.
    entity_stmt = select(Entity).where(Entity.portfolio_company_id.in_(company_ids))
    entities = list((await db.execute(entity_stmt)).scalars().all())

    # Subset of entity ids that have at least one non-org-chart audit file.
    audit_file_entity_ids_stmt = (
        select(File.entity_id)
        .where(
            File.portfolio_company_id.in_(company_ids),
            File.entity_id.isnot(None),
            File.status != "deleted",
            exclude_org_chart_uploads_clause(),
        )
        .distinct()
    )
    audit_entity_ids = set(
        (await db.execute(audit_file_entity_ids_stmt)).scalars().all()
    )

    financials_added_date_map: dict[int, datetime] = {}
    ocr_by_entity: dict[int, FileOCRMetadata] = {}
    original_currency_by_entity: dict[int, Optional[str]] = {}

    if audit_entity_ids:
        file_date_stmt = (
            select(File.entity_id, func.max(File.created_at).label("max_created_at"))
            .where(
                File.entity_id.in_(audit_entity_ids),
                File.status != "deleted",
                exclude_org_chart_uploads_clause(),
            )
            .group_by(File.entity_id)
        )
        financials_added_date_map = {
            row.entity_id: row.max_created_at
            for row in (await db.execute(file_date_stmt)).all()
        }

        ocr_stmt = (
            select(FileOCRMetadata)
            .join(File, FileOCRMetadata.file_id == File.id)
            .where(
                File.entity_id.in_(audit_entity_ids),
                File.status != "deleted",
                exclude_org_chart_uploads_clause(),
            )
            .order_by(File.entity_id, File.created_at.desc())
        )
        ocr_rows = list((await db.execute(ocr_stmt)).scalars().all())
        for ocr in ocr_rows:
            feid = (
                await db.execute(select(File.entity_id).where(File.id == ocr.file_id))
            ).scalar_one_or_none()
            if feid is not None and feid not in ocr_by_entity:
                ocr_by_entity[feid] = ocr

        # original_currency from the most-recently-created audit file per entity.
        orig_cur_stmt = (
            select(File.entity_id, File.original_currency)
            .where(
                File.entity_id.in_(audit_entity_ids),
                File.status != "deleted",
                exclude_org_chart_uploads_clause(),
            )
            .order_by(File.entity_id, File.created_at.desc())
        )
        seen_orig: set[int] = set()
        for row in (await db.execute(orig_cur_stmt)).all():
            if row.entity_id not in seen_orig:
                original_currency_by_entity[row.entity_id] = row.original_currency
                seen_orig.add(row.entity_id)

    return companies, entities, audit_entity_ids, financials_added_date_map, ocr_by_entity, original_currency_by_entity


async def _fetch_entity_fx_rate(
    db: AsyncSession,
    from_currency: Optional[str],
    to_currency: str,
    entity_fy_end: Optional[str],
    fx_cache: dict,
) -> float:
    """Return the historical FX rate from_currency → to_currency at entity fy_end last day.
    Returns 1.0 when currencies match or rate cannot be resolved.
    """
    src = (from_currency or "").strip().upper()
    tgt = to_currency.strip().upper()
    if not src or len(src) != 3 or src == tgt:
        return 1.0
    fy_end_norm = normalize_fy_end(entity_fy_end)
    if not fy_end_norm:
        return 1.0
    try:
        fy_date = fy_end_last_day(fy_end_norm)
    except ValueError:
        return 1.0
    cache_key = (src, tgt, fy_date)
    if cache_key in fx_cache:
        return fx_cache[cache_key][0]
    at = datetime.combine(fy_date, time(12, 0, 0), tzinfo=timezone.utc)
    try:
        rate, fx_ts = await fetch_historical_rate(db, from_currency=src, to_currency=tgt, at=at)
        fx_cache[cache_key] = (rate, fx_ts)
        return rate
    except FxConversionUnavailable:
        logger.warning("FX rate unavailable %s→%s at %s; using 1.0", src, tgt, fy_date)
        return 1.0


async def _fill_reconciliation_worksheet(
    db: AsyncSession,
    ws: Any,
    review_cycle_id: str,
    review_cycle_label: str,
    pct_by_metric: dict,
    abs_by_metric: dict,
    pct_by_label: dict,
    abs_by_label: dict,
    usd_to_inr_rate: Optional[float],
) -> None:
    """Reconciliation report fill — amounts scaled by pc.currency (INR→Cr, USD/other→M). No FX conversion."""
    companies, entities, audit_entity_ids, financials_added_date_map, ocr_by_entity, original_currency_by_entity = (
        await _load_audit_entities_for_cycle(db, review_cycle_id)
    )
    if not companies or not entities:
        return

    company_ids = [pc.id for pc in companies]
    all_entity_ids = [e.id for e in entities]

    fds_stmt = select(FinancialDataSnowflake).where(
        FinancialDataSnowflake.portfolio_company_id.in_(company_ids),
        FinancialDataSnowflake.review_cycle == review_cycle_id,
    )
    fds_rows = list((await db.execute(fds_stmt)).scalars().all())
    fds_by_entity: dict[tuple[int, Optional[int]], FinancialDataSnowflake] = {}
    fds_by_company: dict[int, FinancialDataSnowflake] = {}
    for fds in fds_rows:
        if fds.entity_id is not None:
            fds_by_entity[(fds.portfolio_company_id, fds.entity_id)] = fds
        else:
            fds_by_company[fds.portfolio_company_id] = fds

    fmr_stmt = select(FinancialMetricReconciliation).where(
        FinancialMetricReconciliation.portfolio_company_id.in_(company_ids),
        FinancialMetricReconciliation.entity_id.in_(all_entity_ids),
        FinancialMetricReconciliation.review_cycle == review_cycle_id,
    )
    fmr_rows = list((await db.execute(fmr_stmt)).scalars().all())
    fmr_map: dict[tuple[int, str], FinancialMetricReconciliation] = {
        (r.entity_id, r.metric_key.strip().lower()): r for r in fmr_rows
    }

    for pc in sorted(companies, key=lambda c: (c.name or "")):
        company_entities = [
            e for e in entities if e.portfolio_company_id == pc.id
        ]
        for entity in sorted(company_entities, key=lambda e: (e.name or "")):
            fds = fds_by_entity.get((pc.id, entity.id)) or fds_by_company.get(pc.id)
            fmr_by_metric: dict[str, FinancialMetricReconciliation] = {}
            for metric in _REPORT_METRICS:
                key = (entity.id, metric)
                if key in fmr_map:
                    fmr_by_metric[metric] = fmr_map[key]
            ocr_meta = ocr_by_entity.get(entity.id)
            fin_date = financials_added_date_map.get(entity.id)
            orig_cur = original_currency_by_entity.get(entity.id)

            row_data = _build_row(
                pc=pc,
                entity=entity,
                review_cycle_label=review_cycle_label,
                fds=fds,
                fmr_by_metric=fmr_by_metric,
                ocr_meta=ocr_meta,
                financials_added_date=fin_date,
                pct_by_metric=pct_by_metric,
                abs_by_metric=abs_by_metric,
                pct_by_label=pct_by_label,
                abs_by_label=abs_by_label,
                usd_to_inr_rate=usd_to_inr_rate,
                original_currency=orig_cur,
            )
            ws.append(row_data)


async def _fill_extracted_financials_worksheet(
    db: AsyncSession,
    ws: Any,
    review_cycle_id: str,
    output_currency: str,
    canonical_leaf_paths: list[str],
) -> None:
    """Extracted financials fill: identity columns + canonical leaves with FX + scaling.

    Source currency and FX date are resolved per-entity:
    - source currency = ocr_json["currency"] (the post-conversion stored currency)
    - FX date        = last day of entity.fy_end
    """
    companies, entities, audit_entity_ids, _financials_added_date_map, ocr_by_entity, _original_currency_by_entity = (
        await _load_audit_entities_for_cycle(db, review_cycle_id)
    )
    if not companies or not entities:
        return

    # FX cache: (source_ccy, tgt_ccy, fy_date) → (rate, fx_ts_str)
    fx_cache: dict[tuple[str, str, date], tuple[float, str]] = {}

    for pc in sorted(companies, key=lambda c: (c.name or "")):
        company_entities = [
            e for e in entities if e.portfolio_company_id == pc.id and e.id in audit_entity_ids
        ]

        for entity in sorted(company_entities, key=lambda e: (e.name or "")):
            ocr_meta = ocr_by_entity.get(entity.id)
            extracted_tree = _extract_canonical_tree(ocr_meta)

            # Per-entity source currency from the stored (post-conversion) ocr_json["currency"].
            source_ccy_raw = ""
            if ocr_meta and isinstance(ocr_meta.ocr_json, dict):
                source_ccy_raw = (ocr_meta.ocr_json.get("currency") or "").strip().upper()
            source_ccy = source_ccy_raw if len(source_ccy_raw) == 3 and source_ccy_raw.isalpha() else None

            # Per-entity FX date = last day of entity.fy_end.
            entity_fy_end_norm = normalize_fy_end(entity.fy_end)
            fy_dt: Optional[date] = None
            if entity_fy_end_norm:
                try:
                    fy_dt = fy_end_last_day(entity_fy_end_norm)
                except ValueError:
                    pass

            rate: Optional[float]
            fx_ts_str: Optional[str] = None
            if source_ccy is None or fy_dt is None:
                rate = None
            elif source_ccy == output_currency:
                rate = 1.0
                fx_ts_str = "n/a (same currency)"
            else:
                cache_key = (source_ccy, output_currency, fy_dt)
                if cache_key in fx_cache:
                    rate, fx_ts_str = fx_cache[cache_key]
                else:
                    at = datetime.combine(fy_dt, time(12, 0, 0), tzinfo=timezone.utc)
                    rate, fx_ts_str = await fetch_historical_rate(
                        db, from_currency=source_ccy, to_currency=output_currency, at=at
                    )
                    fx_cache[cache_key] = (rate, fx_ts_str)

            identity_row: list[Any] = [
                pc.company_id,                                      # COID
                pc.name,                                            # Company
                entity.name,                                        # Legal Name
                entity.name,                                        # Entity
                source_ccy or NA,                                   # Source Currency
                fy_dt.isoformat() if fy_dt else NA,                 # Financial Year End
                round(rate, 6) if rate is not None else NA,         # FX Rate Applied
                fx_ts_str if fx_ts_str else NA,                     # FX Date
            ]

            for path in canonical_leaf_paths:
                if extracted_tree is None or rate is None:
                    identity_row.append(NA)
                    continue
                raw = pick_first_numeric_deep(extracted_tree, [path])
                if raw is None:
                    identity_row.append(NA)
                else:
                    identity_row.append(_convert_and_scale(raw, source_ccy, rate, output_currency))

            ws.append(identity_row)


def _extract_canonical_tree(ocr_meta: Optional[FileOCRMetadata]) -> Optional[dict[str, Any]]:
    """Return the finalized canonical financials tree stored on a FileOCRMetadata row, if any."""
    if ocr_meta is None or not isinstance(ocr_meta.ocr_json, dict):
        return None
    extracted = ocr_meta.ocr_json.get("extracted")
    if not isinstance(extracted, dict):
        return None
    if "parse_error" in extracted:
        return None
    return extracted


async def run_export_job(job_id: str) -> None:
    """Background coroutine: generate the XLSX and update job status."""
    async with async_session() as db:
        job = (
            await db.execute(select(ExportJob).where(ExportJob.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            logger.error("export_job %s not found", job_id)
            return

        job.status = "running"
        job.updated_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            if job.job_type == JOB_TYPE_AUDIT_REPORT:
                buf, filename = await _generate_reconciliation_workbook(
                    db, job.review_cycle_id
                )
            elif job.job_type == JOB_TYPE_EXTRACTED_FINANCIALS:
                output_currency = (job.output_currency or "").strip().upper()
                if output_currency not in SUPPORTED_OUTPUT_CURRENCIES:
                    raise ValueError(
                        f"export_job {job_id} has invalid output_currency={job.output_currency!r}"
                    )
                buf, filename = await _generate_extracted_financials_workbook(
                    db, job.review_cycle_id, output_currency
                )
            else:
                raise ValueError(f"unsupported export job_type={job.job_type!r}")

            # Store result in-memory as bytes on the job record (meta key "xlsx_b64")
            import base64
            xlsx_bytes = buf.read()
            job.meta = {
                **(job.meta or {}),
                "xlsx_b64": base64.b64encode(xlsx_bytes).decode(),
            }
            job.filename = filename
            job.status = "done"
            job.updated_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("export_job %s done: %s (%d bytes)", job_id, filename, len(xlsx_bytes))

        except FxConversionUnavailable as exc:
            logger.warning("export_job %s failed FX conversion: %s", job_id, exc)
            job.status = "failed"
            job.error_message = (
                f"FX conversion failed after retries — {exc}. Please retry the export later."
            )[:2000]
            job.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return
        except Exception as exc:
            logger.exception("export_job %s failed", job_id)
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.updated_at = datetime.now(timezone.utc)
            await db.commit()


def create_export_job(review_cycle_id: Optional[str]) -> str:
    """Create a new ExportJob record synchronously (returns job_id). Caller enqueues run_export_job."""
    return str(uuid.uuid4())
