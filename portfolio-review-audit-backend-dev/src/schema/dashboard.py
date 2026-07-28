"""Read models for the analytics dashboard (Scoping + Variance views).

These are response-only schemas. The dashboard is read-only and aggregated;
no Create/Patch models are needed. Source spec: ``Dashboard with dummy numbers.xlsx``.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Filters / vocabulary ────────────────────────────────────────────────────
class ReviewCycleOption(BaseModel):
    id: str
    name: Optional[str] = None


class DashboardFiltersRead(BaseModel):
    """Distinct values that populate the dashboard filter dropdowns."""

    review_cycles: list[ReviewCycleOption] = Field(default_factory=list)
    funds: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    investment_leads: list[str] = Field(default_factory=list)


# ── Scoping overview (KPI cards) ─────────────────────────────────────────────
class PCMDealRow(BaseModel):
    id: int
    deal_id: str
    deal_name: str
    fund: str
    strategy: str
    category: Optional[str] = None
    fy_end: Optional[str] = None
    il_main: Optional[str] = None
    deal_level_stage_1: Optional[str] = None
    deal_level_stage_2: Optional[str] = None
    auditor: Optional[str] = None
    category_of_auditor: Optional[str] = None
    consolidated_cost: Optional[float] = None
    consolidated_fmv: Optional[float] = None
    # Master-scoping decision: True = scoped in for audit, False = scoped out.
    scoping_for_audit: Optional[bool] = None
    reason_for_exclusion: Optional[str] = None


class ScopingOverviewRead(BaseModel):
    review_cycle_id: Optional[str] = None
    total_companies: int = 0
    new_added: int = 0
    removed: int = 0
    # Master-scoping decision counts (PortfolioCompanyMetadata.scoping_for_audit).
    scoped_in: int = 0
    scoped_out: int = 0
    # Coarse status split for the header cards (label → count).
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    # Deal-level stage distributions from PortfolioCompanyMetadata.
    stage_1_breakdown: dict[str, int] = Field(default_factory=dict)
    stage_2_breakdown: dict[str, int] = Field(default_factory=dict)


# ── Status × FYE-month matrix ────────────────────────────────────────────────
class StatusMatrixRow(BaseModel):
    status: str
    by_month: dict[str, int] = Field(default_factory=dict)
    total: int = 0


class StatusMatrixRead(BaseModel):
    """A status (rows) × FYE-month (columns) count matrix.

    ``months`` is the ordered list of column headers (e.g. ``["Jun-25", ...]``).
    """

    review_cycle_id: Optional[str] = None
    months: list[str] = Field(default_factory=list)
    rows: list[StatusMatrixRow] = Field(default_factory=list)
    column_totals: dict[str, int] = Field(default_factory=dict)
    grand_total: int = 0
    # Optional metadata row: due date per FYE month (not a count).
    due_dates_by_month: dict[str, str] = Field(default_factory=dict)


# ── Completion timeline (spec A14/A15: click a number → timeline by month) ───
class CompletionTimelineRead(BaseModel):
    review_cycle_id: Optional[str] = None
    status: Optional[str] = None
    fy_end: Optional[str] = None
    months: list[str] = Field(default_factory=list)          # ordered completion-month labels
    counts: dict[str, int] = Field(default_factory=dict)     # month label -> company count
    total: int = 0


# ── Year-over-year summary (Overall Status / Auditors pies, CY25 vs CY24) ─────
class YoYRow(BaseModel):
    category: str
    current: int = 0
    prior: int = 0


class YoYSummaryRead(BaseModel):
    review_cycle_id: Optional[str] = None
    # The prior cycle the right-hand donut/column represents, so the UI can drill
    # into the correct year when a prior-year slice is clicked.
    prior_review_cycle_id: Optional[str] = None
    current_label: str = ""
    prior_label: str = ""
    rows: list[YoYRow] = Field(default_factory=list)
    current_total: int = 0
    prior_total: int = 0


# ── Drill-down company row ───────────────────────────────────────────────────
class DashboardCompanyRow(BaseModel):
    portfolio_company_id: int
    company_id: Optional[str] = None
    company_name: str
    fund: Optional[str] = None
    geography: Optional[str] = None
    fy_end: Optional[str] = None
    strategy: Optional[str] = None
    investment_lead_1: Optional[str] = None
    investment_lead_2: Optional[str] = None
    consolidated_cost: Optional[str] = None
    consolidated_fmv: Optional[str] = None
    latest_category: Optional[str] = None
    audit_status: Optional[str] = None
    review_stage: Optional[str] = None
    last_year_audit_status: Optional[str] = None
    # Spec sheet 1 col AH: flagged when the past-year trend is continuous overdue.
    flagged_continuous_overdue: bool = False


# ── Variance / Review dashboard (spec sheet: Included_co__Variance) ───────────
class StatusCount(BaseModel):
    status: str
    count: int = 0


class VarianceActionablesRead(BaseModel):
    """Long-overdue actionables summary (section i)."""

    review_cycle_id: Optional[str] = None
    rows: list[StatusCount] = Field(default_factory=list)
    total: int = 0


class VarianceCycleSummaryRead(BaseModel):
    """Current-cycle scoped-in totals + status summary (section ii)."""

    review_cycle_id: Optional[str] = None
    scoped_in_total: int = 0
    scoped_in_by_month: dict[str, int] = Field(default_factory=dict)
    status_summary: list[StatusCount] = Field(default_factory=list)
    total: int = 0


class ComplianceSummaryRead(BaseModel):
    """Audit-report compliance for completed companies (section iii). Counts keyed by label."""

    review_cycle_id: Optional[str] = None
    completed_highlighted_to_investor: int = 0
    opinions: dict[str, int] = Field(default_factory=dict)
    emphasis_of_matter: dict[str, int] = Field(default_factory=dict)
    other_matters: dict[str, int] = Field(default_factory=dict)


class MetricDiscrepancyRow(BaseModel):
    metric: str
    gt_10: int = 0           # breaches the configured threshold (default label > +/-10%)
    lt_10: int = 0           # within the configured threshold
    not_comparable: int = 0
    total: int = 0
    # The configured variance threshold for this metric (fraction, e.g. 0.10 = ±10%),
    # read from ParameterThreshold settings; falls back to the hardcoded default.
    threshold_pct: Optional[float] = None


class DiscrepancySummaryRead(BaseModel):
    """Per-metric variance buckets + the multi-parameter breach count (section iii)."""

    review_cycle_id: Optional[str] = None
    metrics: list[MetricDiscrepancyRow] = Field(default_factory=list)
    diff_more_than_3_params: int = 0


class SubcategoryCount(BaseModel):
    subcategory: str
    count: int = 0


class DiscrepancySubcategoryRead(BaseModel):
    """>10% variance sub-category breakdown for one metric (section iii drill-down)."""

    review_cycle_id: Optional[str] = None
    metric: str
    total: int = 0
    rows: list[SubcategoryCount] = Field(default_factory=list)


class DiscrepancySubcategoryMatrixRead(BaseModel):
    """Above-threshold variance reason breakdown across ALL metrics at once.

    The combined metric x reason matrix from the spec (Included_co__Variance):
    one ``DiscrepancySubcategoryRead`` per canonical metric.
    """

    review_cycle_id: Optional[str] = None
    metrics: list[DiscrepancySubcategoryRead] = Field(default_factory=list)


class DiscrepancyDetailRow(BaseModel):
    """Company/entity-level discrepancy detail (spec r73/r89/E54: remarks, response, status)."""

    portfolio_company_id: Optional[int] = None
    entity_id: Optional[int] = None
    company: str
    entity: Optional[str] = None
    metric: str
    mis_amount: Optional[float] = None
    afs_amount: Optional[float] = None
    diff_pct: Optional[float] = None
    bucket: Optional[str] = None          # ">10%" | "<10%" | "Not Comparable"
    variance_category: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    company_response: Optional[str] = None
    status: Optional[str] = None


class ReportViewRow(BaseModel):
    """Report-view row (section i): Previous-years-pending / Long-overdue companies."""

    no: int
    portfolio_company_id: Optional[int] = None
    cid: Optional[str] = None
    company: str
    fy_end: Optional[str] = None
    current_status: Optional[str] = None
    strategy: Optional[str] = None
    category: Optional[str] = None
    consolidated_cost: Optional[str] = None
    consolidated_fmv: Optional[str] = None
    investment_lead_1: Optional[str] = None
    investment_lead_2: Optional[str] = None
    pending_since_days: Optional[int] = None
    auditor: Optional[str] = None
    auditor_category: Optional[str] = None


# ── Per-entity variance detail table (spec sheet 3, rows 4-9) ────────────────
class MetricCell(BaseModel):
    """One metric's reconciliation cell group (Revenue/EBITDA/PBT/PAT/Cash/Debt)."""

    metric: str
    afs: Optional[float] = None              # As Per Financial
    mis: Optional[float] = None              # As Per MIS
    diff_value: Optional[float] = None       # Difference in value
    diff_pct: Optional[float] = None         # Difference %
    bucket: Optional[str] = None             # ">10%" | "<10%" | "NC"
    to_be_sent: Optional[str] = None
    status: Optional[str] = None
    company_remarks: Optional[str] = None
    subcategory: Optional[str] = None        # sub-category for analysis


class VarianceCompanyDetailRow(BaseModel):
    """A single included (scoped-in) entity row with the full review + compliance
    + per-metric reconciliation, mirroring the spec's per-company master table."""

    no: int
    portfolio_company_id: int
    entity_id: Optional[int] = None
    cid: Optional[str] = None
    company: str
    legal_name: Optional[str] = None
    entity: Optional[str] = None
    currency: Optional[str] = None
    holding_or_subsidiary: Optional[str] = None
    consolidated_or_standalone: Optional[str] = None
    date_of_signing: Optional[str] = None
    current_status: Optional[str] = None
    # Workflow timeline + turnaround times (days); null where the dates aren't set.
    financials_added: Optional[str] = None
    in_review: Optional[str] = None
    tat_review: Optional[int] = None
    queries_sent: Optional[str] = None
    reminder_1: Optional[str] = None
    reminder_2: Optional[str] = None
    tat_queries_sent: Optional[int] = None
    responded: Optional[str] = None
    tat_response: Optional[int] = None
    approved_rejected_on: Optional[str] = None
    tat_approval: Optional[int] = None
    overall_tat: Optional[int] = None
    highlighted_to_investor: bool = False
    # Compliance
    reporting_standards: Optional[str] = None
    auditor_name: Optional[str] = None
    auditor_partner: Optional[str] = None
    auditor_category: Optional[str] = None
    status_of_financials: Optional[str] = None
    status_of_signed_financials: Optional[str] = None
    audit_report_status: Optional[str] = None
    auditor_opinion: Optional[str] = None
    emphasis_of_matter: Optional[str] = None
    other_matters: Optional[str] = None
    going_concern: Optional[str] = None
    caro_availability: Optional[str] = None
    caro_gaps: Optional[str] = None
    caro_notes: Optional[str] = None
    internal_financial_control: Optional[str] = None
    ifc_gaps: Optional[str] = None
    metrics: list[MetricCell] = Field(default_factory=list)
    overall_comments: Optional[str] = None
    overall_status: Optional[str] = None
    flagged_continuous_overdue: bool = False


# ── Timeline view (spec sheet "Timeline") ────────────────────────────────────
# Every figure is sourced from the master scoping table (PortfolioCompanyMetadata):
#   • strategy            → row grouping ("Seed"/"Growth"/"Venture"/…)
#   • geo_l1              → India / SEA split
#   • scoping_for_audit   → the "Included" (scoped-in-for-audit) filter
#   • deal_level_stage_1  → audit-timeline status rows (raw stage-1 values)
#   • fy_end              → FYE-month cross-tab columns
# Uniqueness is by CID (deal_id): a company can sit under multiple strategies, so
# strategy-wise tables double-count while the "for analysis" tables dedupe.
class TimelineStrategyRow(BaseModel):
    """One strategy's India / SEA / Total unique-company counts."""

    strategy: str
    india: int = 0
    sea: int = 0
    total: int = 0  # india + sea


class TimelineStrategyTable(BaseModel):
    """Strategy × {India, SEA, Total} unique counts + a summed Total row."""

    rows: list[TimelineStrategyRow] = Field(default_factory=list)
    total: TimelineStrategyRow = Field(
        default_factory=lambda: TimelineStrategyRow(strategy="Total")
    )


class TimelineAnalysisCounts(BaseModel):
    """Unique companies for analysis (deduped across strategies)."""

    india: int = 0
    sea: int = 0
    total: int = 0  # india + sea


class TimelineStatusRow(BaseModel):
    """One audit-timeline status across the breakdown columns (the table whose label
    was the workbook's "# of financials")."""

    status: str
    overall: int = 0
    by_strategy: dict[str, int] = Field(default_factory=dict)
    india: int = 0
    sea: int = 0


class TimelineStatusTable(BaseModel):
    """Audit-timeline status × {Overall, per-strategy, India, SEA}, included cohort.

    Always carries every canonical status row (0 where empty) so the UI renders the
    full grid. Overall / India / SEA dedupe by CID; per-strategy is strategy-wise.
    """

    strategies: list[str] = Field(default_factory=list)  # column order
    rows: list[TimelineStatusRow] = Field(default_factory=list)
    total: TimelineStatusRow = Field(
        default_factory=lambda: TimelineStatusRow(status="Total")
    )


class TimelineMatrixRow(BaseModel):
    status: str
    by_month: dict[str, int] = Field(default_factory=dict)
    total: int = 0


class TimelineMatrix(BaseModel):
    """Audit-timeline cross-tab (status × FYE-month) for one (scope, geo) slice.

    ``scope`` is "Overall" or a strategy; ``geo`` is "India and SEA"/"India"/"SEA".
    Counts cover the scoped-in (included) cohort only.
    """

    scope: str
    geo: str
    months: list[str] = Field(default_factory=list)
    rows: list[TimelineMatrixRow] = Field(default_factory=list)
    column_totals: dict[str, int] = Field(default_factory=dict)
    grand_total: int = 0


class TimelineSummaryRead(BaseModel):
    """Full payload for the Timeline dashboard page (one request renders the page)."""

    review_cycle_id: Optional[str] = None
    strategy_all: TimelineStrategyTable = Field(default_factory=TimelineStrategyTable)
    strategy_included: TimelineStrategyTable = Field(default_factory=TimelineStrategyTable)
    analysis_all: TimelineAnalysisCounts = Field(default_factory=TimelineAnalysisCounts)
    analysis_included: TimelineAnalysisCounts = Field(default_factory=TimelineAnalysisCounts)
    status_table: TimelineStatusTable = Field(default_factory=TimelineStatusTable)
    matrices: list[TimelineMatrix] = Field(default_factory=list)
    status_rows: list[str] = Field(default_factory=list)  # canonical deal_level_stage_1 order


class TimelineChartRow(BaseModel):
    """One company row feeding the filterable audit-timeline chart.

    Pre-normalised so the chart can bucket + filter entirely client-side:
    ``month_key`` (sortable ``YYYY-MM``) and ``month_label`` (e.g. ``Apr-2026``) come
    from ``tentative_audit_completion_date``; ``due_bucket`` is the within-due / overdue
    classification of ``deal_level_stage_1``.
    """

    deal_id: str
    strategy: Optional[str] = None
    geo: Optional[str] = None  # "India" / "SEA" / None
    scoped_in: bool = False
    fy_end: Optional[str] = None
    stage_1: Optional[str] = None
    due_bucket: Optional[str] = None  # "Within due date" / "Overdue" / None
    month_key: str  # sortable; sentinel "9999-99" for missing/unparseable dates
    month_label: str  # e.g. "Apr-2026" or "Unscheduled"


class TimelineChartFeed(BaseModel):
    """Row-level feed for the filterable audit-timeline chart.

    The chart's X-axis is the tentative-audit-completion month; bars are unique-company
    counts. Filtering + bucketing happen client-side so dropdown changes are instant.
    The ``*_options`` lists drive the filter dropdowns (only values present in the data).
    """

    review_cycle_id: Optional[str] = None
    rows: list[TimelineChartRow] = Field(default_factory=list)
    geo_options: list[str] = Field(default_factory=list)
    strategy_options: list[str] = Field(default_factory=list)
    fy_end_options: list[str] = Field(default_factory=list)
    due_options: list[str] = Field(default_factory=list)
