"""DB dashboard provider — live SQL aggregation.

Every summary is a single ``GROUP BY`` query (counts computed in the database,
never row-by-row in Python) so there is no N+1 and payloads stay small.
Drill-down lists are paginated.

Status taxonomy: ``audit_status`` holds the tentative-matrix row label and
``extra_data.actual_status`` the actual-matrix row label (identity-mapped);
``extra_data.auditor_category`` holds the auditor band. Unknown statuses fall
into the catch-all row.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Entity,
    File,
    FileOCRMetadata,
    FinancialMetricReconciliation as FMR,
    PortfolioCompany,
    PortfolioCompanyMetadata,
    ReviewCycle,
)
from src.schema.dashboard import (
    CompletionTimelineRead,
    ComplianceSummaryRead,
    DashboardCompanyRow,
    DashboardFiltersRead,
    PCMDealRow,
    DiscrepancyDetailRow,
    DiscrepancySubcategoryMatrixRead,
    DiscrepancySubcategoryRead,
    DiscrepancySummaryRead,
    MetricCell,
    MetricDiscrepancyRow,
    ReportViewRow,
    VarianceCompanyDetailRow,
    ReviewCycleOption,
    ScopingOverviewRead,
    StatusCount,
    StatusMatrixRead,
    StatusMatrixRow,
    SubcategoryCount,
    TimelineAnalysisCounts,
    TimelineChartFeed,
    TimelineChartRow,
    TimelineMatrix,
    TimelineMatrixRow,
    TimelineStatusRow,
    TimelineStatusTable,
    TimelineStrategyRow,
    TimelineStrategyTable,
    TimelineSummaryRead,
    VarianceActionablesRead,
    VarianceCycleSummaryRead,
    YoYRow,
    YoYSummaryRead,
)
from src.schema.portfolio import DealLevelStage1, DealLevelStage2, EntityReviewStatus
from src.services.dashboard.base import DashboardProvider
from src.services.financial_reconciliation import load_variance_threshold_maps
from src.services.dashboard.buckets import (
    ACTUAL_STATUS_ROWS,
    TENTATIVE_STATUS_ROWS,
    months_for_cycle,
    order_month_labels,
)
from src.services.fy_end import coerce_fy_end, fy_end_from_legacy_date, normalize_fy_end
from src.services.review_cycle_provisioning import format_cycle_id, parse_cycle_id

# Real/seed data sets audit_status to a canonical tentative row label and
# extra_data.actual_status to a canonical actual row label → mapping is identity.
def _identity(rows: tuple[str, ...]) -> dict[str, str]:
    return {r.lower(): r for r in rows}


# Overall-status pie shares the tentative matrix's row labels.
_OVERALL_STATUS_LABELS = TENTATIVE_STATUS_ROWS
_OVERALL_STATUS_MAP = {
    **_identity(TENTATIVE_STATUS_ROWS),
    "completed": "Completed", "approved": "Completed",
    "not applicable": "Not applicable", "excluded": "Others/Excluded",
}
_AUDITOR_LABELS = ("BIG 4", "BIG 6", "BIG 10", "Others")
_BIG4_KEYWORDS = ("deloitte", "pwc", "pricewaterhouse", "ernst", "young", " ey", "kpmg")


def _prior_cycle_id(cycle_id: Optional[str]) -> Optional[str]:
    parsed = parse_cycle_id((cycle_id or "").strip())
    if not parsed:
        return None
    cy, fy = parsed
    return format_cycle_id(cy - 1, fy - 1)


def _auditor_category(name: Optional[str]) -> str:
    n = (name or "").lower()
    return "BIG 4" if any(k in n for k in _BIG4_KEYWORDS) else "Others"


def _auditor_bucket_expr():
    """SQL expression yielding a company's auditor band ("BIG 4"/"BIG 6"/"BIG 10"/"Others").

    Mirrors the Python bucketing in ``_auditor_counts``: prefer the explicit
    ``extra_data.auditor_category`` when it is one of the canonical bands, else
    derive BIG 4 vs Others from the auditor name. Shared by BOTH the pie counts
    and the drill-down filter so a slice and the list behind it can never diverge.
    """
    cat_norm = func.coalesce(PortfolioCompany.extra_data.op("->>")("auditor_category"), "")
    name_lc = func.lower(func.coalesce(PortfolioCompany.auditor, ""))
    name_is_big4 = or_(*[name_lc.like(f"%{k}%") for k in _BIG4_KEYWORDS])
    return case(
        (cat_norm.in_(list(_AUDITOR_LABELS)), cat_norm),
        (name_is_big4, "BIG 4"),
        else_="Others",
    )


def _has_auditor_clause():
    """True when a company has an auditor on record (explicit band or a name).

    Companies with neither are excluded from the auditor summary entirely, so
    the drill-down must exclude them too (matches the ``not cat and not name``
    skip in ``_auditor_counts``)."""
    cat_norm = func.coalesce(PortfolioCompany.extra_data.op("->>")("auditor_category"), "")
    name_norm = func.coalesce(PortfolioCompany.auditor, "")
    return or_(cat_norm != "", name_norm != "")


def _pending_days(raw: Optional[str]) -> Optional[int]:
    """Days from a (string) tentative completion date to today; None if unparseable."""
    if not raw:
        return None
    s = str(raw).strip()
    for parse in (
        lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
        lambda x: datetime.strptime(x, "%m/%d/%Y"),
        lambda x: datetime.strptime(x, "%Y-%m-%d"),
    ):
        try:
            dt = parse(s)
            today = datetime.now(timezone.utc).date()
            return (today - dt.date()).days
        except (ValueError, TypeError):
            continue
    return None

def _is_overdue_label(s: Optional[str]) -> bool:
    return "overdue" in (s or "").strip().lower()


def _flagged_continuous_overdue(
    actual_status: Optional[str], audit_status: Optional[str], last_year_status: Optional[str]
) -> bool:
    """Spec sheet 1 col AH: flag a company whose past-year trend is continuous overdue —
    overdue this cycle (actual/tentative status) AND overdue in the prior year."""
    this_year = _is_overdue_label(actual_status) or _is_overdue_label(audit_status)
    return bool(this_year and _is_overdue_label(last_year_status))


def _iso_date(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.date().isoformat()
    except AttributeError:
        return str(dt) or None


def _days_between(start, end) -> Optional[int]:
    """Whole days from ``start`` to ``end`` (both datetimes); None if either is missing."""
    if start is None or end is None:
        return None
    try:
        return (end.date() - start.date()).days
    except AttributeError:
        return None


# Hardcoded fallback threshold (fraction): used ONLY for the displayed bucket label
# when a metric has no ParameterThreshold setting. The actual bucketing is driven by
# the precomputed FinancialMetricReconciliation.is_within_threshold (set at reconcile
# time from the same settings), so this constant never affects the counts.
_VARIANCE_PCT = 0.10
_VARIANCE_METRICS = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")
# Above-threshold reconciliations with no variance_category reason recorded are
# bucketed here so the reasons breakdown reconciles with the discrepancy-summary
# counts (otherwise an un-categorised >threshold row would silently vanish).
_VARIANCE_UNCATEGORIZED = "Uncategorized"
# audit_qualitative.opinion_type -> spec compliance label.
_OPINION_LABEL = {
    "unmodified": "Clean", "qualified": "Qualified",
    "adverse": "Adverse", "disclaimer_of_opinion": "Disclaimer of Opinion",
}

logger = logging.getLogger(__name__)

_OTHERS_TENTATIVE = "Others/Excluded"
_OTHERS_ACTUAL = "Excluded"

# Deal-stage matrix rows (master scoping); order follows the canonical enums.
_DEAL_STAGE_1_ROWS: tuple[str, ...] = tuple(s.value for s in DealLevelStage1)
_DEAL_STAGE_2_ROWS: tuple[str, ...] = tuple(s.value for s in DealLevelStage2)

# Master-scoping (PCM) overall-status pie + scoping-status matrix: the
# deal_level_stage_1 distribution. Blank/unknown stage → "Not Applicable".
_DEAL_STAGE_1_LOWER: dict[str, str] = {s.lower(): s for s in _DEAL_STAGE_1_ROWS}
_DEAL_STAGE_1_NA = "Not Applicable"
_OVERALL_STAGE1_LABELS: tuple[str, ...] = _DEAL_STAGE_1_ROWS + (_DEAL_STAGE_1_NA,)
_AUDITOR_CATEGORY_NA = "Not specified"


def _ordered_auditor_categories(present: set[str]) -> list[str]:
    """Auditor-category order for the summary pie: 'Big 4' first, others
    alphabetically, 'Not specified' last."""
    rest = sorted(p for p in present if p not in ("Big 4", _AUDITOR_CATEGORY_NA))
    out: list[str] = []
    if "Big 4" in present:
        out.append("Big 4")
    out += rest
    if _AUDITOR_CATEGORY_NA in present:
        out.append(_AUDITOR_CATEGORY_NA)
    return out

# ── Timeline view helpers (spec sheet "Timeline") ────────────────────────────
# Audit-timeline status rows ARE the raw deal_level_stage_1 values; null/blank →
# "Not Applicable". Canonical display order follows the DealLevelStage1 enum.
_TIMELINE_NOT_APPLICABLE = "Not Applicable"
_TIMELINE_STATUS_ORDER: tuple[str, ...] = _DEAL_STAGE_1_ROWS + (_TIMELINE_NOT_APPLICABLE,)
# Preferred display order for strategy rows/columns; unknown strategies append after.
_TIMELINE_STRATEGY_ORDER: tuple[str, ...] = ("Seed", "Growth", "Venture")
_TIMELINE_NA_MONTH = "NA"


def _timeline_geo(geo_l1: Optional[str]) -> Optional[str]:
    """Map geo_l1 to the India / SEA buckets (case-insensitive). Other → None."""
    g = (geo_l1 or "").strip().lower()
    if g == "india":
        return "india"
    if g in ("sea", "south east asia", "south-east asia", "southeast asia", "s.e. asia"):
        return "sea"
    return None


def _timeline_status(stage_1: Optional[str]) -> str:
    """Raw deal_level_stage_1 value; blank / null → 'Not Applicable'."""
    return (stage_1 or "").strip() or _TIMELINE_NOT_APPLICABLE


def _ordered_strategies(present: set[str]) -> list[str]:
    """Preferred strategies first (Seed/Growth/Venture), then the rest alphabetically."""
    known = [s for s in _TIMELINE_STRATEGY_ORDER if s in present]
    rest = sorted(p for p in present if p not in set(_TIMELINE_STRATEGY_ORDER))
    return known + rest


def _ordered_statuses(present: set[str]) -> list[str]:
    """Canonical deal_level_stage_1 order first, then any extras alphabetically."""
    canon_lower = {c.lower(): c for c in _TIMELINE_STATUS_ORDER}
    present_lower = {p.lower(): p for p in present}
    ordered = [present_lower[c.lower()] for c in _TIMELINE_STATUS_ORDER if c.lower() in present_lower]
    extras = sorted(p for p in present if p.lower() not in canon_lower)
    return ordered + extras


# ── Timeline chart helpers (filterable bar chart) ────────────────────────────
# Display labels for the India / SEA geo buckets (chart-facing, title-cased).
_TIMELINE_GEO_DISPLAY = {"india": "India", "sea": "SEA"}
# Accepted lower-cased SEA spellings (shared by the geo filter + india_sea_only).
_SEA_GEO_VARIANTS = ("sea", "south east asia", "south-east asia", "southeast asia", "s.e. asia")
_TIMELINE_GEO_ORDER: tuple[str, ...] = ("India", "SEA")

# "Within due date / Overdue" classification of deal_level_stage_1.
_TIMELINE_DUE_WITHIN = "Within due date"
_TIMELINE_DUE_OVERDUE = "Overdue"
_DUE_BUCKET_BY_STAGE_1: dict[str, str] = {
    DealLevelStage1.COMPLETED_WITHIN_DUE_DATE.value: _TIMELINE_DUE_WITHIN,
    DealLevelStage1.EXPECTED_TO_COMPLETE_WITHIN_TIMELINE.value: _TIMELINE_DUE_WITHIN,
    DealLevelStage1.COMPLETED_POST_DUE_DATE.value: _TIMELINE_DUE_OVERDUE,
    DealLevelStage1.OVERDUE.value: _TIMELINE_DUE_OVERDUE,
    DealLevelStage1.EXPECTED_DELAY.value: _TIMELINE_DUE_OVERDUE,
}

# Tentative-completion months with no parseable date land in this bucket (sorts last).
_TIMELINE_NO_DATE_KEY = "9999-99"
_TIMELINE_NO_DATE_LABEL = "Unscheduled"
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Accepted string layouts for tentative_audit_completion_date (stored free-form).
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y",
    "%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y",
    "%b-%Y", "%B-%Y", "%b %Y", "%B %Y", "%b-%y", "%b %y",
    "%Y-%m", "%m-%Y", "%m/%Y",
)


def _timeline_due_bucket(stage_1: Optional[str]) -> Optional[str]:
    """Classify deal_level_stage_1 into 'Within due date' / 'Overdue' (None if neither)."""
    return _DUE_BUCKET_BY_STAGE_1.get((stage_1 or "").strip())


def _ordered_geos(present: set[str]) -> list[str]:
    """India then SEA, restricted to those actually present."""
    return [g for g in _TIMELINE_GEO_ORDER if g in present]


def _parse_completion_month(raw: Optional[str]) -> tuple[str, str]:
    """Best-effort parse a tentative-completion date → (sortable key, display label).

    Returns ``("YYYY-MM", "Mon-YYYY")`` on success, else the unscheduled sentinel.
    """
    s = (raw or "").strip()
    if not s:
        return _TIMELINE_NO_DATE_KEY, _TIMELINE_NO_DATE_LABEL
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return f"{dt.year:04d}-{dt.month:02d}", f"{_MONTH_ABBR[dt.month - 1]}-{dt.year}"
    return _TIMELINE_NO_DATE_KEY, _TIMELINE_NO_DATE_LABEL


def _build_timeline_chart(rows, review_cycle_id: Optional[str]) -> TimelineChartFeed:
    """Pure builder: normalise master-scoping rows into the chart feed + filter options."""
    out_rows: list[TimelineChartRow] = []
    geos: set[str] = set()
    strategies: set[str] = set()
    fy_ends: set[str] = set()
    dues: set[str] = set()

    for deal_id, strategy, geo_l1, scoping, stage_1, fy_end, tentative in rows:
        geo_key = _timeline_geo(geo_l1)
        geo = _TIMELINE_GEO_DISPLAY.get(geo_key) if geo_key else None
        strat = (strategy or "").strip() or None
        # Derive the concrete year from the review cycle when master scoping stores
        # a placeholder like "Mar FY", so the filter options/rows are canonical.
        fy = coerce_fy_end(fy_end, review_cycle_id=review_cycle_id)
        stage = (stage_1 or "").strip() or None
        due = _timeline_due_bucket(stage_1)
        key, label = _parse_completion_month(tentative)

        out_rows.append(
            TimelineChartRow(
                deal_id=str(deal_id),
                strategy=strat,
                geo=geo,
                scoped_in=bool(scoping),
                fy_end=fy,
                stage_1=stage,
                due_bucket=due,
                month_key=key,
                month_label=label,
            )
        )
        if geo:
            geos.add(geo)
        if strat:
            strategies.add(strat)
        if fy:
            fy_ends.add(fy)
        if due:
            dues.add(due)

    return TimelineChartFeed(
        review_cycle_id=review_cycle_id,
        rows=out_rows,
        geo_options=_ordered_geos(geos),
        strategy_options=_ordered_strategies(strategies),
        fy_end_options=sorted(fy_ends),
        due_options=[d for d in (_TIMELINE_DUE_WITHIN, _TIMELINE_DUE_OVERDUE) if d in dues],
    )


def _build_timeline_summary(rows, review_cycle_id: Optional[str]) -> TimelineSummaryRead:
    """Aggregate master-scoping rows into the full Timeline payload (pure function).

    Each ``row`` is ``(deal_id, strategy, geo_l1, scoping_for_audit, deal_level_stage_1,
    fy_end)``. CID = ``deal_id``; sets dedupe so strategy-wise tables double-count a
    multi-strategy company while the "for analysis" tables count it once.
    """
    # Strategy × geo distinct-CID sets, for the "all" and "included" cohorts.
    strat_all: dict[str, dict[str, set]] = defaultdict(lambda: {"india": set(), "sea": set()})
    strat_inc: dict[str, dict[str, set]] = defaultdict(lambda: {"india": set(), "sea": set()})
    # Unique-for-analysis CID sets per geo (deduped across strategies).
    ana_all = {"india": set(), "sea": set()}
    ana_inc = {"india": set(), "sea": set()}
    # Audit-timeline status table (included): status → {overall/india/sea sets, by_strategy}.
    fin: dict[str, dict] = defaultdict(
        lambda: {"overall": set(), "india": set(), "sea": set(), "by_strategy": defaultdict(set)}
    )
    # Cross-tab (included): (scope, geo) → status → month → CID set.
    # scope ∈ {"Overall", <strategy>}; geo ∈ {"India and SEA", "India", "SEA"}.
    # status = raw deal_level_stage_1; month = fy_end ("NA" when blank).
    matrix_acc: dict[tuple, dict] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set))
    )
    strategies_present: set[str] = set()
    statuses_present: set[str] = set()

    for deal_id, strategy, geo_l1, scoping, stage_1, fy_end in rows:
        cid = deal_id
        strat = (strategy or "").strip() or "Unspecified"
        strategies_present.add(strat)
        geo = _timeline_geo(geo_l1)
        if geo is None:
            continue  # only India / SEA participate (matches the workbook headers)
        included = bool(scoping)

        strat_all[strat][geo].add(cid)
        ana_all[geo].add(cid)
        if not included:
            continue

        strat_inc[strat][geo].add(cid)
        ana_inc[geo].add(cid)

        status = _timeline_status(stage_1)
        statuses_present.add(status)
        fin[status]["overall"].add(cid)
        fin[status][geo].add(cid)
        fin[status]["by_strategy"][strat].add(cid)
        month = coerce_fy_end(fy_end, review_cycle_id=review_cycle_id) or _TIMELINE_NA_MONTH
        geo_label = "India" if geo == "india" else "SEA"
        for scope in ("Overall", strat):
            for geo_key in ("India and SEA", geo_label):
                matrix_acc[(scope, geo_key)][status][month].add(cid)

    # Always surface the canonical strategies / statuses so the UI renders the full
    # grid (showing 0 where there is no data), plus any extra values seen in the data.
    strategies = _ordered_strategies(strategies_present | set(_TIMELINE_STRATEGY_ORDER))
    status_order = _ordered_statuses(statuses_present | set(_TIMELINE_STATUS_ORDER))

    def _strategy_table(acc: dict[str, dict[str, set]]) -> TimelineStrategyTable:
        out_rows: list[TimelineStrategyRow] = []
        tot_india = tot_sea = 0
        for strat in strategies:
            india = len(acc[strat]["india"]) if strat in acc else 0
            sea = len(acc[strat]["sea"]) if strat in acc else 0
            out_rows.append(
                TimelineStrategyRow(strategy=strat, india=india, sea=sea, total=india + sea)
            )
            tot_india += india
            tot_sea += sea
        return TimelineStrategyTable(
            rows=out_rows,
            total=TimelineStrategyRow(
                strategy="Total", india=tot_india, sea=tot_sea, total=tot_india + tot_sea
            ),
        )

    def _analysis(acc: dict[str, set]) -> TimelineAnalysisCounts:
        india, sea = len(acc["india"]), len(acc["sea"])
        return TimelineAnalysisCounts(india=india, sea=sea, total=india + sea)

    # Audit-timeline status table (was the workbook's "# of financials"): every canonical
    # status row is emitted (0 where empty). Overall / India / SEA dedupe by CID; the
    # per-strategy columns are strategy-wise. The Total row dedupes via set-union.
    status_rows: list[TimelineStatusRow] = []
    tot_overall, tot_india, tot_sea = set(), set(), set()
    tot_by_strategy: dict[str, set] = defaultdict(set)
    for status in status_order:
        cell = fin.get(status) or {"overall": set(), "india": set(), "sea": set(), "by_strategy": {}}
        by_strategy_sets = cell["by_strategy"]
        status_rows.append(
            TimelineStatusRow(
                status=status,
                overall=len(cell["overall"]),
                india=len(cell["india"]),
                sea=len(cell["sea"]),
                by_strategy={s: len(by_strategy_sets.get(s, set())) for s in strategies},
            )
        )
        tot_overall |= cell["overall"]
        tot_india |= cell["india"]
        tot_sea |= cell["sea"]
        for s in strategies:
            tot_by_strategy[s] |= by_strategy_sets.get(s, set())
    status_table = TimelineStatusTable(
        strategies=strategies,
        rows=status_rows,
        total=TimelineStatusRow(
            status="Total",
            overall=len(tot_overall),
            india=len(tot_india),
            sea=len(tot_sea),
            by_strategy={s: len(tot_by_strategy[s]) for s in strategies},
        ),
    )

    # Audit-timeline cross-tabs (scoped-in cohort): status (deal_level_stage_1) × fy_end.
    # Emitted for EVERY scope (Overall + each strategy) × geo (India and SEA / India /
    # SEA) and every canonical status row / cycle month, so the UI is always present
    # (0 where empty). Months = the cycle's Jun→May span + NA. Set-union totals dedupe a
    # multi-strategy CID so the grand total stays a unique-company count.
    months = months_for_cycle(review_cycle_id) + [_TIMELINE_NA_MONTH]
    matrices: list[TimelineMatrix] = []
    scopes = ["Overall"] + strategies
    for scope in scopes:
        for geo_key in ("India and SEA", "India", "SEA"):
            acc = matrix_acc.get((scope, geo_key)) or {}
            out_rows: list[TimelineMatrixRow] = []
            column_totals = {m: set() for m in months}
            grand: set = set()
            for status in status_order:
                per_month = acc.get(status) or {}
                by_month = {m: len(per_month.get(m, set())) for m in months}
                row_total: set = set()
                for m in months:
                    column_totals[m] |= per_month.get(m, set())
                    row_total |= per_month.get(m, set())
                grand |= row_total
                out_rows.append(
                    TimelineMatrixRow(status=status, by_month=by_month, total=len(row_total))
                )
            matrices.append(
                TimelineMatrix(
                    scope=scope,
                    geo=geo_key,
                    months=months,
                    rows=out_rows,
                    column_totals={m: len(column_totals[m]) for m in months},
                    grand_total=len(grand),
                )
            )

    return TimelineSummaryRead(
        review_cycle_id=review_cycle_id,
        strategy_all=_strategy_table(strat_all),
        strategy_included=_strategy_table(strat_inc),
        analysis_all=_analysis(ana_all),
        analysis_included=_analysis(ana_inc),
        status_table=status_table,
        matrices=matrices,
        status_rows=status_order,
    )


# audit_status (lower-cased) → spec matrix row. Adjust when real taxonomy is confirmed.
_TENTATIVE_STATUS_MAP: dict[str, str] = {
    **_identity(TENTATIVE_STATUS_ROWS),
    "completed": "Completed",
    "approved": "Completed",
    "not applicable": "Not applicable",
    "excluded": "Others/Excluded",
}
_ACTUAL_STATUS_MAP: dict[str, str] = {
    **_identity(ACTUAL_STATUS_ROWS),
    "completed": "Completed within due date",
    "approved": "Completed within due date",
    "due now": "Due now",
    "not yet due": "Not yet due",
    "overdue": "Overdue companies",
    "not applicable": "Not applicable",
    "excluded": "Excluded",
}


def _cycle_clause(review_cycle_id: Optional[str]):
    return PortfolioCompany.review_cycle_id == review_cycle_id if review_cycle_id else None


def _dim_clauses(*, fund=None, geography=None, sector=None, strategy=None,
                 category=None, investment_lead=None) -> list:
    """WHERE clauses for the dashboard dimension filters (spec R1: filter the
    summaries/matrices by strategy, fund, geography, …)."""
    cl = []
    if fund:
        cl.append(PortfolioCompany.fund == fund)
    if geography:
        cl.append(PortfolioCompany.geography == geography)
    if sector:
        cl.append(PortfolioCompany.company_category_1 == sector)
    if strategy:
        cl.append(PortfolioCompany.investment_stage == strategy)
    if category:
        cl.append(PortfolioCompany.company_phase_category == category)
    if investment_lead:
        cl.append(PortfolioCompany.investment_lead == investment_lead)
    return cl


def _pcm_dim_clauses(*, fund=None, geography=None, sector=None, strategy=None,
                     category=None, investment_lead=None) -> list:
    """Same dimension filters as ``_dim_clauses`` but against the master scoping table
    (PortfolioCompanyMetadata), so the cards/matrices that read PCM actually respond to
    the Fund / Geography / Sector / Strategy / Category / IL filters."""
    cl = []
    if fund:
        cl.append(func.trim(PortfolioCompanyMetadata.fund) == fund.strip())
    if geography:
        # geo_l1 (India / SEA, case-insensitive, SEA spelling variants).
        g = geography.strip().lower()
        gcol = func.lower(func.trim(func.coalesce(PortfolioCompanyMetadata.geo_l1, "")))
        cl.append(gcol.in_(_SEA_GEO_VARIANTS) if g == "sea" else gcol == g)
    if sector:
        cl.append(func.trim(PortfolioCompanyMetadata.sector_l1) == sector.strip())
    if strategy:
        cl.append(func.trim(PortfolioCompanyMetadata.strategy) == strategy.strip())
    if category:
        cl.append(func.trim(PortfolioCompanyMetadata.category) == category.strip())
    if investment_lead:
        cl.append(func.trim(PortfolioCompanyMetadata.il_main) == investment_lead.strip())
    return cl


# Spec sheet 3: the variance cohort is companies whose financials are uploaded for
# the cycle. "Long overdue" = past the tentative completion date + grace (spec: TBD).
_OVERDUE_GRACE_DAYS = 0


# Review lifecycle state now lives on entities.status (EntityReviewStatus); the
# company-level review_stage is deprecated and no longer written. The dashboard reads
# entity status, rolling up to the company where a single label is needed.
_ENTITY_STATUS_RANK = {s.value: i for i, s in enumerate(EntityReviewStatus)}


def _entity_status_exists(wanted: list[str]):
    """A company has >=1 entity whose status is one of ``wanted`` (whitespace-insensitive)."""
    cleaned = [w.strip() for w in wanted if w and w.strip()]
    return exists(
        select(1).where(
            Entity.portfolio_company_id == PortfolioCompany.id,
            func.trim(Entity.status).in_(cleaned),
        )
    )


def _representative_status(statuses: list[Optional[str]]) -> Optional[str]:
    """Roll a company's entity statuses up to one label: the earliest lifecycle stage
    present (the bottleneck), so a company badge reflects work still pending."""
    vals = [s.strip() for s in statuses if s and s.strip()]
    if not vals:
        return None
    return min(vals, key=lambda s: _ENTITY_STATUS_RANK.get(s, len(_ENTITY_STATUS_RANK)))


async def _entity_statuses_by_company(db: AsyncSession, company_ids: list[int]) -> dict[int, list[str]]:
    """Map portfolio_company_id -> [entity statuses] for a set of companies (one query)."""
    if not company_ids:
        return {}
    rows = (await db.execute(
        select(Entity.portfolio_company_id, Entity.status).where(
            Entity.portfolio_company_id.in_(company_ids), Entity.status.isnot(None)
        )
    )).all()
    out: dict[int, list[str]] = defaultdict(list)
    for pcid, st in rows:
        out[pcid].append(st)
    return out


def _scoped_in_clause(review_cycle_id: Optional[str]):
    """Companies that have ≥1 uploaded financials File for the selected cycle.

    This is the spec definition of "scoped in" for the variance dashboard, and
    replaces the earlier ``review_stage IS NOT NULL`` proxy (review_stage is not
    populated until the in-review tracker runs)."""
    sub = select(File.portfolio_company_id).where(File.portfolio_company_id.isnot(None))
    if review_cycle_id:
        sub = sub.where(File.review_cycle_id == review_cycle_id)
    return PortfolioCompany.id.in_(sub)


async def _distinct(db: AsyncSession, column) -> list[str]:
    stmt = select(column).where(column.isnot(None)).distinct().order_by(column)
    return [v for (v,) in (await db.execute(stmt)).all() if v is not None and str(v).strip()]


def _bucket_expr():
    """SQL CASE → 'nc' (no comparable %) | 'gt' (breaches threshold) | 'lt' (within).

    Uses the precomputed reconciliation fields on FinancialMetricReconciliation
    (``percentage_difference`` / ``is_within_threshold``).
    """
    return case(
        (FMR.percentage_difference.is_(None), "nc"),
        (FMR.is_within_threshold.is_(True), "lt"),
        else_="gt",
    )


def _gt_condition():
    """Row has a comparable % difference that breaches the threshold (>±10%)."""
    return and_(FMR.percentage_difference.isnot(None), FMR.is_within_threshold.isnot(True))


def _present(val) -> bool:
    """A qualitative paragraph counts as 'Available' when it carries real content."""
    if not isinstance(val, str):
        return bool(val)
    s = val.strip().lower()
    return s != "" and s not in ("not available", "not applicable", "none")


class DbDashboardProvider(DashboardProvider):
    # ── Timeline dashboard ───────────────────────────────────────────────────
    async def timeline_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None
    ) -> TimelineSummaryRead:
        """Full Timeline payload from the master scoping table.

        One SELECT over PortfolioCompanyMetadata (the master scoping table) feeds a
        pure-Python aggregation (:func:`_build_timeline_summary`). The set is small
        (~one row per deal×strategy), so distinct-CID counting is done in Python.
        """
        stmt = select(
            PortfolioCompanyMetadata.deal_id,
            PortfolioCompanyMetadata.strategy,
            PortfolioCompanyMetadata.geo_l1,
            PortfolioCompanyMetadata.scoping_for_audit,
            PortfolioCompanyMetadata.deal_level_stage_1,
            PortfolioCompanyMetadata.fy_end,
        )
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
        rows = (await db.execute(stmt)).all()
        return _build_timeline_summary(rows, review_cycle_id)

    async def timeline_chart(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None
    ) -> TimelineChartFeed:
        """Row-level feed for the filterable audit-timeline chart.

        Pulls just the dimensions the chart filters/buckets on from the master scoping
        table; normalisation + filter-option discovery happen in pure Python
        (:func:`_build_timeline_chart`). Filtering/bucketing is done client-side.
        """
        stmt = select(
            PortfolioCompanyMetadata.deal_id,
            PortfolioCompanyMetadata.strategy,
            PortfolioCompanyMetadata.geo_l1,
            PortfolioCompanyMetadata.scoping_for_audit,
            PortfolioCompanyMetadata.deal_level_stage_1,
            PortfolioCompanyMetadata.fy_end,
            PortfolioCompanyMetadata.tentative_audit_completion_date,
        )
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
        rows = (await db.execute(stmt)).all()
        return _build_timeline_chart(rows, review_cycle_id)

    async def filters(self, db: AsyncSession) -> DashboardFiltersRead:
        cycles = (
            await db.execute(select(ReviewCycle.id, ReviewCycle.name).order_by(ReviewCycle.id))
        ).all()
        # Options come from the master scoping table (PCM) — the same columns the
        # scoping dashboard filters on — so a selected value always exists in the data.
        return DashboardFiltersRead(
            review_cycles=[ReviewCycleOption(id=str(cid), name=name) for cid, name in cycles],
            funds=await _distinct(db, PortfolioCompanyMetadata.fund),
            geographies=await _distinct(db, PortfolioCompanyMetadata.geo_l1),
            sectors=await _distinct(db, PortfolioCompanyMetadata.sector_l1),
            strategies=await _distinct(db, PortfolioCompanyMetadata.strategy),
            categories=await _distinct(db, PortfolioCompanyMetadata.category),
            investment_leads=await _distinct(db, PortfolioCompanyMetadata.il_main),
        )

    async def pcm_deals_by_stage(
        self,
        db: AsyncSession,
        *,
        deal_level_stage_1: Optional[str] = None,
        deal_level_stage_2: Optional[str] = None,
        scoping_for_audit: Optional[bool] = None,
        fy_end: Optional[str] = None,
        category_of_auditor: Optional[str] = None,
        strategy: Optional[str] = None,
        geo_l1: Optional[str] = None,
        india_sea_only: bool = False,
        deal_ids: Optional[list[str]] = None,
        unique: bool = False,
        fund: Optional[str] = None,
        sector: Optional[str] = None,
        category: Optional[str] = None,
        investment_lead: Optional[str] = None,
        review_cycle_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[PCMDealRow], int]:
        stmt = select(PortfolioCompanyMetadata)
        # Dashboard dimension filters (so a drill respects the active Fund / Sector /
        # Category / IL filters too); strategy + geo_l1 are handled separately below.
        for c in _pcm_dim_clauses(fund=fund, sector=sector, category=category, investment_lead=investment_lead):
            stmt = stmt.where(c)
        if deal_level_stage_1:
            # "Not Applicable" is the synthetic label for a blank/null stage (it's not a
            # stored value), so drill into the empties rather than an exact match.
            if deal_level_stage_1.strip() == _DEAL_STAGE_1_NA:
                stmt = stmt.where(
                    or_(
                        PortfolioCompanyMetadata.deal_level_stage_1.is_(None),
                        func.trim(PortfolioCompanyMetadata.deal_level_stage_1) == "",
                    )
                )
            else:
                stmt = stmt.where(func.trim(PortfolioCompanyMetadata.deal_level_stage_1) == deal_level_stage_1.strip())
        if deal_level_stage_2:
            stmt = stmt.where(func.trim(PortfolioCompanyMetadata.deal_level_stage_2) == deal_level_stage_2.strip())
        if scoping_for_audit is not None:
            stmt = stmt.where(PortfolioCompanyMetadata.scoping_for_audit.is_(scoping_for_audit))
        if fy_end:
            # Timeline matrices use "NA" for the blank-FYE column.
            if fy_end.strip() == _TIMELINE_NA_MONTH:
                stmt = stmt.where(
                    or_(
                        PortfolioCompanyMetadata.fy_end.is_(None),
                        func.trim(PortfolioCompanyMetadata.fy_end) == "",
                    )
                )
            else:
                # The clicked matrix column is canonical (e.g. "Mar-26"), but master
                # scoping may store the same FY-end month as a placeholder ("Mar FY")
                # or bare month ("Mar"). Match any spelling that denotes that month so
                # the drill list lines up with the cell count.
                target = normalize_fy_end(fy_end) or fy_end.strip()
                month_abbr = target[:3]
                candidates = {target.lower(), month_abbr.lower(), f"{month_abbr} FY".lower()}
                stmt = stmt.where(
                    func.lower(func.trim(PortfolioCompanyMetadata.fy_end)).in_(candidates)
                )
        if strategy:
            stmt = stmt.where(func.trim(PortfolioCompanyMetadata.strategy) == strategy.strip())
        if geo_l1:
            # Match the India / SEA buckets the Timeline tables count (case-insensitive,
            # SEA spelling variants), mirroring _timeline_geo.
            g = geo_l1.strip().lower()
            gcol = func.lower(func.trim(func.coalesce(PortfolioCompanyMetadata.geo_l1, "")))
            if g == "sea":
                stmt = stmt.where(gcol.in_(_SEA_GEO_VARIANTS))
            else:
                stmt = stmt.where(gcol == g)
        if india_sea_only:
            # The Timeline tables only count India/SEA companies, so a Total/Overall
            # drill (no specific geo) must exclude any other geo to match the cell.
            gcol = func.lower(func.trim(func.coalesce(PortfolioCompanyMetadata.geo_l1, "")))
            stmt = stmt.where(gcol.in_(("india", *_SEA_GEO_VARIANTS)))
        if deal_ids:
            stmt = stmt.where(PortfolioCompanyMetadata.deal_id.in_(deal_ids))
        if category_of_auditor:
            if category_of_auditor.strip() == _AUDITOR_CATEGORY_NA:
                stmt = stmt.where(
                    or_(
                        PortfolioCompanyMetadata.category_of_auditor.is_(None),
                        func.trim(PortfolioCompanyMetadata.category_of_auditor) == "",
                    )
                )
            else:
                stmt = stmt.where(
                    func.trim(PortfolioCompanyMetadata.category_of_auditor) == category_of_auditor.strip()
                )
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)

        if unique:
            # Collapse to one representative record per CID (deal_id) so the list count
            # matches the Timeline cells, which count UNIQUE companies (a multi-strategy
            # company has several rows but is one company).
            filtered = stmt.subquery()
            total = (
                await db.execute(select(func.count(func.distinct(filtered.c.deal_id))))
            ).scalar_one() or 0
            rep_ids = select(func.min(filtered.c.id)).group_by(filtered.c.deal_id)
            rows = (
                await db.execute(
                    select(PortfolioCompanyMetadata)
                    .where(PortfolioCompanyMetadata.id.in_(rep_ids))
                    .order_by(PortfolioCompanyMetadata.deal_name)
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
        else:
            total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0
            rows = (await db.execute(stmt.order_by(PortfolioCompanyMetadata.deal_name).limit(limit).offset(offset))).scalars().all()
        return [
            PCMDealRow(
                id=r.id,
                deal_id=r.deal_id,
                deal_name=r.deal_name,
                fund=r.fund,
                strategy=r.strategy,
                category=r.category,
                fy_end=coerce_fy_end(r.fy_end, review_cycle_id=r.review_cycle_id),
                il_main=r.il_main,
                deal_level_stage_1=r.deal_level_stage_1,
                deal_level_stage_2=r.deal_level_stage_2,
                auditor=r.auditor,
                category_of_auditor=r.category_of_auditor,
                consolidated_cost=float(r.consolidated_cost) if r.consolidated_cost is not None else None,
                consolidated_fmv=float(r.consolidated_fmv) if r.consolidated_fmv is not None else None,
                scoping_for_audit=r.scoping_for_audit,
                reason_for_exclusion=r.reason_for_exclusion,
            )
            for r in rows
        ], total

    async def scoping_overview(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> ScopingOverviewRead:
        # The whole overview is sourced from the master scoping table so every card
        # (total / scoped in-out / stage breakdowns / new-removed) lives in one place
        # and responds to the same dimension filters. PCM = PortfolioCompanyMetadata.
        extra = _pcm_dim_clauses(**dims)

        def _pcm(*conds):
            s = select(*conds).select_from(PortfolioCompanyMetadata)
            if review_cycle_id:
                s = s.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
            for c in extra:
                s = s.where(c)
            return s

        # Total companies = unique CIDs (a multi-strategy company is one company).
        total = (
            await db.execute(_pcm(func.count(func.distinct(PortfolioCompanyMetadata.deal_id))))
        ).scalar_one() or 0

        async def _breakdown(stage_col):
            s = _pcm(func.trim(stage_col), func.count()).where(stage_col.isnot(None)).group_by(
                func.trim(stage_col)
            )
            return {stage: int(count) for stage, count in (await db.execute(s)).all()}

        stage_1_breakdown = await _breakdown(PortfolioCompanyMetadata.deal_level_stage_1)
        stage_2_breakdown = await _breakdown(PortfolioCompanyMetadata.deal_level_stage_2)

        # Scoped in = True, scoped out = explicitly False (NULL = undecided → neither).
        scoped_in = (
            await db.execute(_pcm(func.count()).where(PortfolioCompanyMetadata.scoping_for_audit.is_(True)))
        ).scalar_one() or 0
        scoped_out = (
            await db.execute(_pcm(func.count()).where(PortfolioCompanyMetadata.scoping_for_audit.is_(False)))
        ).scalar_one() or 0

        # new_added = CIDs in this cycle absent from the prior cycle; removed = the
        # reverse. Compared on deal_id (CID), honouring the same dimension filters.
        new_added, removed = 0, 0
        prior_id = _prior_cycle_id(review_cycle_id)
        if review_cycle_id and prior_id:
            def _cids(cycle: str):
                s = select(PortfolioCompanyMetadata.deal_id).where(
                    PortfolioCompanyMetadata.review_cycle_id == cycle,
                    PortfolioCompanyMetadata.deal_id.isnot(None),
                    *extra,
                )
                return s

            cur_cids, prior_cids = _cids(review_cycle_id), _cids(prior_id)
            new_added = (await db.execute(
                select(func.count(func.distinct(PortfolioCompanyMetadata.deal_id))).where(
                    PortfolioCompanyMetadata.review_cycle_id == review_cycle_id,
                    PortfolioCompanyMetadata.deal_id.isnot(None),
                    PortfolioCompanyMetadata.deal_id.notin_(prior_cids),
                    *extra,
                )
            )).scalar_one() or 0
            removed = (await db.execute(
                select(func.count(func.distinct(PortfolioCompanyMetadata.deal_id))).where(
                    PortfolioCompanyMetadata.review_cycle_id == prior_id,
                    PortfolioCompanyMetadata.deal_id.isnot(None),
                    PortfolioCompanyMetadata.deal_id.notin_(cur_cids),
                    *extra,
                )
            )).scalar_one() or 0
        return ScopingOverviewRead(
            review_cycle_id=review_cycle_id,
            total_companies=int(total),
            new_added=int(new_added),
            removed=int(removed),
            scoped_in=int(scoped_in),
            scoped_out=int(scoped_out),
            status_breakdown={},
            stage_1_breakdown=stage_1_breakdown,
            stage_2_breakdown=stage_2_breakdown,
        )

    async def _grouped_matrix(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str],
        rows: tuple[str, ...],
        status_map: dict[str, str],
        others_row: str,
        include_due_dates: bool,
        status_col=None,
        extra_clauses: Optional[list] = None,
    ) -> StatusMatrixRead:
        months = months_for_cycle(review_cycle_id)
        clause = _cycle_clause(review_cycle_id)
        extra = extra_clauses or []
        # The actual matrix groups by extra_data->>'actual_status'; the tentative
        # matrix (default) groups by audit_status. Single GROUP BY either way.
        if status_col is None:
            status_col = PortfolioCompany.audit_status
        stmt = select(
            PortfolioCompany.fy_end, status_col, func.count()
        ).group_by(PortfolioCompany.fy_end, status_col)
        if clause is not None:
            stmt = stmt.where(clause)
        for c in extra:
            stmt = stmt.where(c)

        acc = {r: {m: 0 for m in months} for r in rows}
        for fy_end, audit_status, count in (await db.execute(stmt)).all():
            if fy_end not in acc[rows[0]]:  # month outside this cycle's columns
                continue
            label = status_map.get((audit_status or "").strip().lower(), others_row)
            if label not in acc:
                label = others_row
            acc[label][fy_end] += int(count)

        out_rows: list[StatusMatrixRow] = []
        column_totals = {m: 0 for m in months}
        grand_total = 0
        for r in rows:
            by_month = acc[r]
            row_total = sum(by_month.values())
            for m in months:
                column_totals[m] += by_month[m]
            grand_total += row_total
            out_rows.append(StatusMatrixRow(status=r, by_month=by_month, total=row_total))

        due_dates: dict[str, str] = {}
        if include_due_dates:
            # One representative due_date per FYE month for this cycle.
            dd_stmt = select(PortfolioCompany.fy_end, func.min(PortfolioCompany.due_date)).group_by(
                PortfolioCompany.fy_end
            )
            if clause is not None:
                dd_stmt = dd_stmt.where(clause)
            for c in extra:
                dd_stmt = dd_stmt.where(c)
            month_dd = {fy: (dd or "") for fy, dd in (await db.execute(dd_stmt)).all()}
            due_dates = {m: month_dd.get(m, "") for m in months}

        return StatusMatrixRead(
            review_cycle_id=review_cycle_id,
            months=months,
            rows=out_rows,
            column_totals=column_totals,
            grand_total=grand_total,
            due_dates_by_month=due_dates,
        )

    async def _pcm_stage1_fye_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], dims: Optional[dict] = None
    ) -> StatusMatrixRead:
        """Scoping status by FYE month from the master scoping table: deal_level_stage_1
        (rows) × fy_end (cols) for the scoped-in cohort, distinct-CID counts. FYE months
        are matched exactly (Mmm-YY); blank/unknown stage → 'Not Applicable'."""
        months = months_for_cycle(review_cycle_id)
        rows_def = _DEAL_STAGE_1_ROWS + (_DEAL_STAGE_1_NA,)
        stmt = select(
            func.trim(PortfolioCompanyMetadata.fy_end),
            func.trim(PortfolioCompanyMetadata.deal_level_stage_1),
            PortfolioCompanyMetadata.deal_id,
        ).where(PortfolioCompanyMetadata.scoping_for_audit.is_(True))
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
        for c in _pcm_dim_clauses(**(dims or {})):
            stmt = stmt.where(c)

        acc = {r: {m: set() for m in months} for r in rows_def}
        for fy_end, stage_val, deal_id in (await db.execute(stmt)).all():
            month = coerce_fy_end(fy_end, valid_months=months)
            if month not in acc[rows_def[0]]:  # month outside this cycle's columns
                continue
            label = _DEAL_STAGE_1_LOWER.get((stage_val or "").strip().lower(), _DEAL_STAGE_1_NA)
            acc[label][month].add(deal_id)

        out_rows: list[StatusMatrixRow] = []
        column_totals = {m: set() for m in months}
        grand: set = set()
        for r in rows_def:
            by_month = {m: len(acc[r][m]) for m in months}
            row_total: set = set()
            for m in months:
                column_totals[m] |= acc[r][m]
                row_total |= acc[r][m]
            grand |= row_total
            out_rows.append(StatusMatrixRow(status=r, by_month=by_month, total=len(row_total)))
        return StatusMatrixRead(
            review_cycle_id=review_cycle_id,
            months=months,
            rows=out_rows,
            column_totals={m: len(column_totals[m]) for m in months},
            grand_total=len(grand),
            due_dates_by_month={},
        )

    async def scoping_tentative_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> StatusMatrixRead:
        # Scoping status now comes from the master scoping table (deal_level_stage_1),
        # not portfolio_companies.audit_status. PCM has a single audit-stage field, so
        # the Tentative and Actual tabs render the same deal-stage-1 × FYE-month grid.
        return await self._pcm_stage1_fye_matrix(db, review_cycle_id=review_cycle_id, dims=dims)

    async def scoping_actual_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> StatusMatrixRead:
        return await self._pcm_stage1_fye_matrix(db, review_cycle_id=review_cycle_id, dims=dims)

    async def deal_stage_matrix(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str],
        stage: str,
        basis: str,
        **dims,
    ) -> StatusMatrixRead:
        """Master-scoping deal-stage (rows) x FYE-month (cols) matrix.

        ``stage`` selects ``deal_level_stage_1`` / ``deal_level_stage_2``; ``basis``
        selects the count basis — ``"cid"`` sums ``unique_by_company_id`` (distinct
        CIDs) and ``"cid_strategy"`` sums ``unique_by_company_id_strategy`` (distinct
        CID x Strategy). Reuses the StatusMatrix shape so the existing chart/table
        render it unchanged. FYE months are matched exactly (Mmm-YY), like the
        company matrices.
        """
        months = months_for_cycle(review_cycle_id)
        stage_col = (
            PortfolioCompanyMetadata.deal_level_stage_2 if stage == "2"
            else PortfolioCompanyMetadata.deal_level_stage_1
        )
        rows_def = _DEAL_STAGE_2_ROWS if stage == "2" else _DEAL_STAGE_1_ROWS
        value_col = (
            PortfolioCompanyMetadata.unique_by_company_id_strategy if basis == "cid_strategy"
            else PortfolioCompanyMetadata.unique_by_company_id
        )
        others = "Others"
        all_rows = rows_def + (others,)
        label_map = {r.strip().lower(): r for r in rows_def}

        stmt = (
            select(
                func.trim(PortfolioCompanyMetadata.fy_end),
                func.trim(stage_col),
                func.coalesce(func.sum(value_col), 0),
            )
            .where(stage_col.isnot(None))
            .group_by(func.trim(PortfolioCompanyMetadata.fy_end), func.trim(stage_col))
        )
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
        for c in _pcm_dim_clauses(**dims):
            stmt = stmt.where(c)

        acc = {r: {m: 0 for m in months} for r in all_rows}
        for fy_end, stage_val, total in (await db.execute(stmt)).all():
            month = coerce_fy_end(fy_end, valid_months=months)
            if month not in acc[all_rows[0]]:  # month outside this cycle's columns
                continue
            label = label_map.get((stage_val or "").strip().lower(), others)
            acc[label][month] += int(total or 0)

        out_rows: list[StatusMatrixRow] = []
        column_totals = {m: 0 for m in months}
        grand_total = 0
        for r in all_rows:
            by_month = acc[r]
            row_total = sum(by_month.values())
            if r == others and row_total == 0:
                continue  # hide the synthetic catch-all row when empty
            for m in months:
                column_totals[m] += by_month[m]
            grand_total += row_total
            out_rows.append(StatusMatrixRow(status=r, by_month=by_month, total=row_total))

        return StatusMatrixRead(
            review_cycle_id=review_cycle_id,
            months=months,
            rows=out_rows,
            column_totals=column_totals,
            grand_total=grand_total,
            due_dates_by_month={},
        )

    async def scoping_companies(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        status: Optional[str] = None,
        fy_end: Optional[str] = None,
        fund: Optional[str] = None,
        geography: Optional[str] = None,
        sector: Optional[str] = None,
        strategy: Optional[str] = None,
        category: Optional[str] = None,
        investment_lead: Optional[str] = None,
        auditor_category: Optional[str] = None,
        q: Optional[str] = None,
        completion_month: Optional[str] = None,
        scoped_in: Optional[bool] = None,
        flagged: Optional[bool] = None,
        entity_status: Optional[str] = None,
        audit_status: Optional[str] = None,
        actual_status: Optional[str] = None,
        opinion: Optional[str] = None,
        delta: Optional[str] = None,
        deal_level_stage_1: Optional[str] = None,
        deal_level_stage_2: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[DashboardCompanyRow], int]:
        stmt = select(PortfolioCompany)
        # completion_month: precise filtering needs a normalised completion-month column;
        # left as a no-op until the real tentative_completion_date format is confirmed.
        prior_id = _prior_cycle_id(review_cycle_id) if review_cycle_id else None
        if delta == "removed" and review_cycle_id and prior_id:
            # Removed = companies in the PRIOR cycle whose CID is absent from the current one.
            cur_cids = select(PortfolioCompany.company_id).where(
                PortfolioCompany.review_cycle_id == review_cycle_id, PortfolioCompany.company_id.isnot(None)
            )
            stmt = stmt.where(
                PortfolioCompany.review_cycle_id == prior_id,
                PortfolioCompany.company_id.isnot(None),
                PortfolioCompany.company_id.notin_(cur_cids),
            )
        else:
            if review_cycle_id:
                stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
            if delta == "added" and review_cycle_id and prior_id:
                # Newly added = current-cycle CIDs absent from the prior cycle.
                prior_cids = select(PortfolioCompany.company_id).where(
                    PortfolioCompany.review_cycle_id == prior_id, PortfolioCompany.company_id.isnot(None)
                )
                stmt = stmt.where(
                    PortfolioCompany.company_id.isnot(None),
                    PortfolioCompany.company_id.notin_(prior_cids),
                )
        if scoped_in:
            # Variance "scoped-in" cohort = companies with financials uploaded this cycle.
            stmt = stmt.where(_scoped_in_clause(review_cycle_id))
        if flagged:
            # Highlighted-to-investor cohort = companies with a flagged reconciliation row.
            fl = select(1).where(
                FMR.portfolio_company_id == PortfolioCompany.id, FMR.flagged.is_(True)
            )
            if review_cycle_id:
                fl = fl.where(FMR.review_cycle == review_cycle_id)
            stmt = stmt.where(exists(fl))
        if entity_status:
            # Precise review-status drill (Variance): match ONLY the per-entity status,
            # not the scoping (audit_status / actual_status) taxonomies.
            stmt = stmt.where(_entity_status_exists([s for s in entity_status.split(",")]))
        if audit_status:
            # Precise tentative-scoping status (overall-status pie, tentative matrix).
            stmt = stmt.where(
                PortfolioCompany.audit_status.in_([s.strip() for s in audit_status.split(",") if s.strip()])
            )
        if actual_status:
            # Precise actual-completion status (actual matrix, IL-wise).
            stmt = stmt.where(
                PortfolioCompany.extra_data.op("->>")("actual_status").in_(
                    [s.strip() for s in actual_status.split(",") if s.strip()]
                )
            )
        if opinion:
            # Compliance drill: companies whose audited-financials file carries this
            # auditor opinion (audit_qualitative.opinion_type → Clean/Qualified/…).
            wanted_types = [k for k, v in _OPINION_LABEL.items() if v == opinion]
            if wanted_types:
                oq = (
                    select(1)
                    .select_from(File)
                    .join(FileOCRMetadata, FileOCRMetadata.file_id == File.id)
                    .where(
                        File.portfolio_company_id == PortfolioCompany.id,
                        func.lower(FileOCRMetadata.audit_qualitative.op("->>")("opinion_type")).in_(wanted_types),
                    )
                )
                if review_cycle_id:
                    oq = oq.where(File.review_cycle_id == review_cycle_id)
                stmt = stmt.where(exists(oq))
        if fy_end:
            stmt = stmt.where(PortfolioCompany.fy_end == fy_end)
        if fund:
            stmt = stmt.where(PortfolioCompany.fund == fund)
        if geography:
            stmt = stmt.where(PortfolioCompany.geography == geography)
        if sector:
            stmt = stmt.where(PortfolioCompany.company_category_1 == sector)
        if strategy:
            stmt = stmt.where(PortfolioCompany.investment_stage == strategy)
        if category:
            # Category-wise (spec iv) supports multi-select.
            cats = [c.strip() for c in category.split(",") if c.strip()]
            if cats:
                stmt = stmt.where(PortfolioCompany.company_phase_category.in_(cats))
        if investment_lead:
            stmt = stmt.where(PortfolioCompany.investment_lead == investment_lead)
        if auditor_category:
            # Match the SAME band the auditor-summary pie counted this company under
            # (explicit extra_data.auditor_category, else derived from auditor name).
            # An exact column match alone returns nothing, since that column is unset.
            stmt = stmt.where(_has_auditor_clause(), _auditor_bucket_expr() == auditor_category)
        if status:
            wanted = [s.strip() for s in status.split(",") if s.strip()]
            # A status label can be a tentative (audit_status) or actual-completion
            # (extra_data.actual_status) scoping label, or a review-lifecycle stage —
            # which now lives on entities.status (company matches if any entity is in it).
            stmt = stmt.where(
                or_(
                    PortfolioCompany.audit_status.in_(wanted),
                    PortfolioCompany.extra_data.op("->>")("actual_status").in_(wanted),
                    _entity_status_exists(wanted),
                )
            )
        if deal_level_stage_1:
            stage1_deals = select(PortfolioCompanyMetadata.deal_id).where(
                func.trim(PortfolioCompanyMetadata.deal_level_stage_1) == deal_level_stage_1.strip()
            )
            stmt = stmt.where(PortfolioCompany.company_id.in_(stage1_deals))
        if deal_level_stage_2:
            stage2_deals = select(PortfolioCompanyMetadata.deal_id).where(
                func.trim(PortfolioCompanyMetadata.deal_level_stage_2) == deal_level_stage_2.strip()
            )
            stmt = stmt.where(PortfolioCompany.company_id.in_(stage2_deals))
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(PortfolioCompany.name.ilike(like), PortfolioCompany.company_id.ilike(like))
            )

        total = (
            await db.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one() or 0
        companies = (
            await db.execute(
                stmt.order_by(PortfolioCompany.name, PortfolioCompany.id).limit(limit).offset(offset)
            )
        ).scalars().all()

        ent_status_by_co = await _entity_statuses_by_company(db, [pc.id for pc in companies])
        rows = [
            DashboardCompanyRow(
                portfolio_company_id=pc.id,
                company_id=pc.company_id,
                company_name=pc.name,
                fund=pc.fund,
                geography=pc.geography,
                fy_end=pc.fy_end,
                strategy=pc.investment_stage,
                investment_lead_1=pc.investment_lead,
                investment_lead_2=(pc.extra_data or {}).get("investment_lead_2"),
                consolidated_cost=pc.consolidated_cost,
                consolidated_fmv=pc.consolidated_fmv,
                latest_category=pc.company_phase_category,
                audit_status=pc.audit_status,
                review_stage=_representative_status(ent_status_by_co.get(pc.id, [])),
                last_year_audit_status=(pc.extra_data or {}).get("last_year_audit_status"),
                flagged_continuous_overdue=_flagged_continuous_overdue(
                    (pc.extra_data or {}).get("actual_status"),
                    pc.audit_status,
                    (pc.extra_data or {}).get("last_year_audit_status"),
                ),
            )
            for pc in companies
        ]
        return rows, int(total)

    async def _audit_status_counts(
        self, db: AsyncSession, cycle_id: Optional[str], extra: Optional[list] = None
    ) -> dict[str, int]:
        stmt = select(PortfolioCompany.audit_status, func.count()).group_by(PortfolioCompany.audit_status)
        if cycle_id:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == cycle_id)
        for c in extra or []:
            stmt = stmt.where(c)
        out = {label: 0 for label in _OVERALL_STATUS_LABELS}
        for status, count in (await db.execute(stmt)).all():
            label = _OVERALL_STATUS_MAP.get((status or "").strip().lower(), "Others/Excluded")
            out[label] += int(count)
        return out

    async def _stage1_status_counts(
        self, db: AsyncSession, cycle_id: Optional[str], dims: Optional[dict] = None
    ) -> dict[str, int]:
        """deal_level_stage_1 distribution from the master scoping table for a cycle
        (row counts, matching the Deal-Stage-1 chips). Blank/unknown → 'Not Applicable'."""
        out = {label: 0 for label in _OVERALL_STAGE1_LABELS}
        if not cycle_id:
            return out
        stmt = (
            select(func.trim(PortfolioCompanyMetadata.deal_level_stage_1), func.count())
            .where(PortfolioCompanyMetadata.review_cycle_id == cycle_id)
            .group_by(func.trim(PortfolioCompanyMetadata.deal_level_stage_1))
        )
        for c in _pcm_dim_clauses(**(dims or {})):
            stmt = stmt.where(c)
        for status, count in (await db.execute(stmt)).all():
            label = _DEAL_STAGE_1_LOWER.get((status or "").strip().lower(), _DEAL_STAGE_1_NA)
            out[label] += int(count)
        return out

    async def scoping_overall_status(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None, **dims
    ) -> YoYSummaryRead:
        # Overall status = the master-scoping deal_level_stage_1 distribution (current
        # vs prior cycle). Sourced from PortfolioCompanyMetadata, not the legacy
        # portfolio_companies.audit_status.
        prior_id = _prior_cycle_id(review_cycle_id)
        cur = await self._stage1_status_counts(db, review_cycle_id, dims)
        pri = await self._stage1_status_counts(db, prior_id, dims)
        rows = [YoYRow(category=l, current=cur[l], prior=pri[l]) for l in _OVERALL_STAGE1_LABELS]
        return YoYSummaryRead(
            review_cycle_id=review_cycle_id,
            prior_review_cycle_id=prior_id,
            current_label=review_cycle_id or "Current",
            prior_label=prior_id or "Prior",
            rows=rows,
            current_total=sum(cur.values()),
            prior_total=sum(pri.values()),
        )

    async def _auditor_counts(
        self, db: AsyncSession, cycle_id: Optional[str], extra: Optional[list] = None
    ) -> dict[str, int]:
        # Bucket each company by auditor band using the shared expression so these
        # counts and the drill-down filter (scoping_companies) classify identically.
        # Companies with no auditor on record are excluded.
        bucket = _auditor_bucket_expr()
        stmt = select(bucket, func.count()).where(_has_auditor_clause()).group_by(bucket)
        if cycle_id:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == cycle_id)
        for c in extra or []:
            stmt = stmt.where(c)
        out = {label: 0 for label in _AUDITOR_LABELS}
        for label, count in (await db.execute(stmt)).all():
            out[label if label in out else "Others"] += int(count)
        return out

    async def _pcm_auditor_category_counts(
        self, db: AsyncSession, cycle_id: Optional[str], dims: Optional[dict] = None
    ) -> dict[str, int]:
        """Auditor-category distribution from the master scoping table for a cycle.
        Groups by PortfolioCompanyMetadata.category_of_auditor; blank → 'Not specified'."""
        out: dict[str, int] = {}
        if not cycle_id:
            return out
        stmt = (
            select(func.trim(PortfolioCompanyMetadata.category_of_auditor), func.count())
            .where(PortfolioCompanyMetadata.review_cycle_id == cycle_id)
            .group_by(func.trim(PortfolioCompanyMetadata.category_of_auditor))
        )
        for c in _pcm_dim_clauses(**(dims or {})):
            stmt = stmt.where(c)
        for cat, count in (await db.execute(stmt)).all():
            label = (cat or "").strip() or _AUDITOR_CATEGORY_NA
            out[label] = out.get(label, 0) + int(count)
        return out

    async def scoping_auditor_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None, **dims
    ) -> YoYSummaryRead:
        # Auditor + category from the master scoping table (category_of_auditor),
        # not the legacy portfolio_companies.auditor banding.
        prior_id = _prior_cycle_id(review_cycle_id)
        cur = await self._pcm_auditor_category_counts(db, review_cycle_id, dims)
        pri = await self._pcm_auditor_category_counts(db, prior_id, dims)
        labels = _ordered_auditor_categories(set(cur) | set(pri))
        rows = [YoYRow(category=l, current=cur.get(l, 0), prior=pri.get(l, 0)) for l in labels]
        return YoYSummaryRead(
            review_cycle_id=review_cycle_id,
            prior_review_cycle_id=prior_id,
            current_label=review_cycle_id or "Current",
            prior_label=prior_id or "Prior",
            rows=rows,
            current_total=sum(cur.values()),
            prior_total=sum(pri.values()),
        )

    async def scoping_completion_timeline(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        status: Optional[str] = None,
        fy_end: Optional[str] = None,
    ) -> CompletionTimelineRead:
        stmt = select(PortfolioCompany.tentative_completion_date).where(
            PortfolioCompany.tentative_completion_date.isnot(None)
        )
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
        if fy_end:
            stmt = stmt.where(PortfolioCompany.fy_end == fy_end)
        if status:
            stmt = stmt.where(
                or_(
                    PortfolioCompany.audit_status == status,
                    _entity_status_exists([status]),
                )
            )
        counts: dict[str, int] = {}
        for (tcd,) in (await db.execute(stmt)).all():
            label = fy_end_from_legacy_date(tcd) or normalize_fy_end(tcd)
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
        months = order_month_labels(list(counts.keys()))
        return CompletionTimelineRead(
            review_cycle_id=review_cycle_id,
            status=status,
            fy_end=fy_end,
            months=months,
            counts={m: counts[m] for m in months},
            total=sum(counts.values()),
        )

    # ── Variance / Review dashboard ──────────────────────────────────────────
    async def variance_actionables(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> VarianceActionablesRead:
        # Spec (i): current-status counts for LONG-OVERDUE scoped-in companies —
        # i.e. companies with financials uploaded for the cycle whose tentative
        # completion date is in the past (+ grace). Dates are stored as free-text
        # strings, so the overdue test is done in Python via _pending_days().
        clause = _cycle_clause(review_cycle_id)
        # Current status is the per-entity review status (entities.status); count
        # entities of scoped-in companies that are past their tentative date.
        stmt = (
            select(Entity.status, PortfolioCompany.tentative_completion_date)
            .join(PortfolioCompany, PortfolioCompany.id == Entity.portfolio_company_id)
            .where(_scoped_in_clause(review_cycle_id), Entity.status.isnot(None))
        )
        if clause is not None:
            stmt = stmt.where(clause)
        counts: dict[str, int] = {}
        for stage, tcd in (await db.execute(stmt)).all():
            days = _pending_days(tcd)
            if days is None or days <= _OVERDUE_GRACE_DAYS:
                continue
            key = (stage or "").strip()
            counts[key] = counts.get(key, 0) + 1
        rows = [
            StatusCount(status=s, count=c)
            for s, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return VarianceActionablesRead(
            review_cycle_id=review_cycle_id, rows=rows, total=sum(counts.values())
        )

    async def variance_cycle_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> VarianceCycleSummaryRead:
        clause = _cycle_clause(review_cycle_id)
        # Variance cohort = scoped-in companies = those with financials uploaded for
        # the cycle (spec sheet 3), not those merely carrying a review_stage.
        scoped = _scoped_in_clause(review_cycle_id)
        total_stmt = select(func.count()).select_from(PortfolioCompany).where(scoped)
        m_stmt = select(PortfolioCompany.fy_end, func.count()).where(
            PortfolioCompany.fy_end.isnot(None), scoped
        ).group_by(PortfolioCompany.fy_end)
        # Status summary is keyed by per-entity review status (entities.status) but
        # counts DISTINCT deals (companies), not entities, so each row reconciles with
        # the company drill-down (which lists distinct companies having an entity in
        # that status). Counting entities previously inflated e.g. "Not applicable"
        # far above the drill-down deal count.
        s_stmt = (
            select(func.trim(Entity.status), func.count(func.distinct(PortfolioCompany.id)))
            .join(PortfolioCompany, PortfolioCompany.id == Entity.portfolio_company_id)
            .where(scoped, Entity.status.isnot(None))
            .group_by(func.trim(Entity.status))
        )
        if clause is not None:
            total_stmt = total_stmt.where(clause)
            m_stmt = m_stmt.where(clause)
            s_stmt = s_stmt.where(clause)
        scoped_in_total = (await db.execute(total_stmt)).scalar_one() or 0
        by_month = {fy: int(c) for fy, c in (await db.execute(m_stmt)).all()}
        status = [
            StatusCount(status=(s or "Unspecified"), count=int(c))
            for s, c in (await db.execute(s_stmt)).all()
        ]
        return VarianceCycleSummaryRead(
            review_cycle_id=review_cycle_id,
            scoped_in_total=int(scoped_in_total),
            scoped_in_by_month=by_month,
            status_summary=status,
            # Header reads "<total> companies": the distinct scoped-in deal count.
            # (status_summary counts can sum higher when a deal has entities in
            # multiple statuses, so don't sum them here.)
            total=int(scoped_in_total),
        )

    async def variance_compliance(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> ComplianceSummaryRead:
        # Bounded set (completed audit files) — fetch the qualitative JSON once, tally in Python.
        stmt = (
            select(FileOCRMetadata.audit_qualitative)
            .select_from(FileOCRMetadata)
            .join(File, File.id == FileOCRMetadata.file_id)
        )
        if review_cycle_id:
            stmt = stmt.where(File.review_cycle_id == review_cycle_id)
        opinions = {"Clean": 0, "Qualified": 0, "Adverse": 0, "Disclaimer of Opinion": 0}
        eom = {"Available": 0, "Not Available": 0}
        om = {"Available": 0, "Not Available": 0}
        for (aq,) in (await db.execute(stmt)).all():
            if not isinstance(aq, dict):
                continue
            label = _OPINION_LABEL.get(str(aq.get("opinion_type") or "").lower())
            if label:
                opinions[label] += 1
            eom["Available" if _present(aq.get("emphasis_of_matter")) else "Not Available"] += 1
            om["Available" if _present(aq.get("other_matters")) else "Not Available"] += 1

        flagged_stmt = select(func.count(func.distinct(FMR.portfolio_company_id))).where(FMR.flagged.is_(True))
        if review_cycle_id:
            flagged_stmt = flagged_stmt.where(FMR.review_cycle == review_cycle_id)
        highlighted = (await db.execute(flagged_stmt)).scalar_one() or 0

        return ComplianceSummaryRead(
            review_cycle_id=review_cycle_id,
            completed_highlighted_to_investor=int(highlighted),
            opinions=opinions,
            emphasis_of_matter=eom,
            other_matters=om,
        )

    async def variance_discrepancy_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> DiscrepancySummaryRead:
        bucket = _bucket_expr()
        stmt = select(FMR.metric_key, bucket.label("b"), func.count()).group_by(FMR.metric_key, bucket)
        if review_cycle_id:
            stmt = stmt.where(FMR.review_cycle == review_cycle_id)
        agg = {m: {"gt": 0, "lt": 0, "nc": 0} for m in _VARIANCE_METRICS}
        for metric_key, b, c in (await db.execute(stmt)).all():
            mk = (metric_key or "").strip().lower()
            if mk in agg and b in agg[mk]:
                agg[mk][b] += int(c)
        # Per-metric variance threshold from the settings page (ParameterThreshold);
        # fall back to the hardcoded default when a metric isn't configured.
        pct_by_metric, *_ = await load_variance_threshold_maps(db)
        metrics = [
            MetricDiscrepancyRow(
                metric=m, gt_10=agg[m]["gt"], lt_10=agg[m]["lt"],
                not_comparable=agg[m]["nc"], total=sum(agg[m].values()),
                threshold_pct=pct_by_metric.get(m, _VARIANCE_PCT),
            )
            for m in _VARIANCE_METRICS
        ]
        # Entities breaching >10% on ≥3 distinct metrics.
        sub = select(FMR.entity_id).where(_gt_condition())
        if review_cycle_id:
            sub = sub.where(FMR.review_cycle == review_cycle_id)
        sub = sub.group_by(FMR.entity_id).having(func.count(func.distinct(FMR.metric_key)) >= 3).subquery()
        diff3 = (await db.execute(select(func.count()).select_from(sub))).scalar_one() or 0
        return DiscrepancySummaryRead(
            review_cycle_id=review_cycle_id, metrics=metrics, diff_more_than_3_params=int(diff3)
        )

    async def variance_discrepancy_subcategory(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], metric: str
    ) -> DiscrepancySubcategoryRead:
        mk = (metric or "").strip().lower()
        # Count ALL above-threshold rows; rows with no reason fall under "Uncategorized"
        # so the total reconciles with the discrepancy-summary >threshold count.
        stmt = select(FMR.variance_category, func.count()).where(
            FMR.metric_key == mk, _gt_condition()
        ).group_by(FMR.variance_category)
        if review_cycle_id:
            stmt = stmt.where(FMR.review_cycle == review_cycle_id)
        merged: dict[str, int] = {}
        for vc, c in (await db.execute(stmt)).all():
            label = str(vc).strip() if vc and str(vc).strip() else _VARIANCE_UNCATEGORIZED
            merged[label] = merged.get(label, 0) + int(c)
        rows = [
            SubcategoryCount(subcategory=k, count=v)
            for k, v in sorted(merged.items(), key=lambda kv: (kv[0] == _VARIANCE_UNCATEGORIZED, -kv[1], kv[0]))
        ]
        return DiscrepancySubcategoryRead(
            review_cycle_id=review_cycle_id, metric=mk, total=sum(r.count for r in rows), rows=rows
        )

    async def variance_discrepancy_subcategory_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> DiscrepancySubcategoryMatrixRead:
        """Above-threshold variance reasons for EVERY metric in one query (the
        combined metric x reason matrix from the spec)."""
        # Count ALL above-threshold rows; rows with no reason fall under "Uncategorized"
        # so each metric's total reconciles with the discrepancy-summary >threshold count.
        stmt = select(FMR.metric_key, FMR.variance_category, func.count()).where(
            _gt_condition()
        ).group_by(FMR.metric_key, FMR.variance_category)
        if review_cycle_id:
            stmt = stmt.where(FMR.review_cycle == review_cycle_id)

        agg: dict[str, dict[str, int]] = {m: {} for m in _VARIANCE_METRICS}
        for metric_key, vc, c in (await db.execute(stmt)).all():
            mk = (metric_key or "").strip().lower()
            if mk in agg:
                label = str(vc).strip() if vc and str(vc).strip() else _VARIANCE_UNCATEGORIZED
                agg[mk][label] = agg[mk].get(label, 0) + int(c)

        metrics = []
        for m in _VARIANCE_METRICS:
            rows = [
                SubcategoryCount(subcategory=k, count=v)
                for k, v in sorted(
                    agg[m].items(), key=lambda kv: (kv[0] == _VARIANCE_UNCATEGORIZED, -kv[1], kv[0])
                )
            ]
            metrics.append(
                DiscrepancySubcategoryRead(
                    review_cycle_id=review_cycle_id,
                    metric=m,
                    total=sum(r.count for r in rows),
                    rows=rows,
                )
            )
        return DiscrepancySubcategoryMatrixRead(review_cycle_id=review_cycle_id, metrics=metrics)

    async def variance_report_view(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        kind: str = "long_overdue",
        current_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[ReportViewRow], int]:
        stmt = select(PortfolioCompany)
        if kind == "previous_years_pending":
            # Spec Filter 1: "CY24/FY25 and before" — every cycle strictly earlier
            # than the current one (by parsed CY/FY), not just "!= current".
            if review_cycle_id:
                cur = parse_cycle_id(review_cycle_id.strip())
                cyc_ids = (await db.execute(
                    select(PortfolioCompany.review_cycle_id)
                    .where(PortfolioCompany.review_cycle_id.isnot(None))
                    .distinct()
                )).scalars().all()
                prev_ids = [
                    c for c in cyc_ids
                    if (p := parse_cycle_id((c or "").strip())) and (cur is None or p < cur)
                ]
                stmt = stmt.where(PortfolioCompany.review_cycle_id.in_(prev_ids))
        elif review_cycle_id:  # long_overdue → current cycle
            stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
        if current_status:
            # Current status is the per-entity review status now.
            stmt = stmt.where(_entity_status_exists([current_status]))

        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0
        companies = (
            await db.execute(
                stmt.order_by(PortfolioCompany.name, PortfolioCompany.id).limit(limit).offset(offset)
            )
        ).scalars().all()
        ent_status_by_co = await _entity_statuses_by_company(db, [pc.id for pc in companies])
        rows = [
            ReportViewRow(
                no=offset + i + 1,
                portfolio_company_id=pc.id,
                cid=pc.company_id,
                company=pc.name,
                fy_end=pc.fy_end,
                current_status=_representative_status(ent_status_by_co.get(pc.id, [])),
                strategy=pc.investment_stage,
                category=pc.company_phase_category,
                consolidated_cost=pc.consolidated_cost,
                consolidated_fmv=pc.consolidated_fmv,
                investment_lead_1=pc.investment_lead,
                investment_lead_2=(pc.extra_data or {}).get("investment_lead_2"),
                pending_since_days=_pending_days(pc.tentative_completion_date),
                auditor=pc.auditor,
                auditor_category=_auditor_category(pc.auditor),
            )
            for i, pc in enumerate(companies)
        ]
        return rows, int(total)

    async def variance_company_details(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[VarianceCompanyDetailRow], int]:
        """Per-entity detail for scoped-in companies (financials uploaded this cycle):
        identity + review-workflow TATs + compliance + per-metric reconciliation.
        Mirrors the spec's per-company master table (sheet 3, rows 4-9)."""
        file_ent = select(File.entity_id).where(File.entity_id.isnot(None))
        if review_cycle_id:
            file_ent = file_ent.where(File.review_cycle_id == review_cycle_id)
        base = (
            select(Entity, PortfolioCompany)
            .join(PortfolioCompany, PortfolioCompany.id == Entity.portfolio_company_id)
            .where(Entity.id.in_(file_ent))
        )
        total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0
        page = (await db.execute(
            base.order_by(PortfolioCompany.name, Entity.id).limit(limit).offset(offset)
        )).all()
        if not page:
            return [], int(total)

        entity_ids = [ent.id for ent, _ in page]

        # Per-metric reconciliation, currency and the investor-highlight flag per entity.
        fmr_stmt = select(FMR).where(FMR.entity_id.in_(entity_ids))
        if review_cycle_id:
            fmr_stmt = fmr_stmt.where(FMR.review_cycle == review_cycle_id)
        fmr_by_entity: dict[int, dict[str, Any]] = {}
        flagged_entities: set[int] = set()
        currency_by_entity: dict[int, Optional[str]] = {}
        for fmr in (await db.execute(fmr_stmt)).scalars().all():
            fmr_by_entity.setdefault(fmr.entity_id, {})[(fmr.metric_key or "").lower()] = fmr
            if fmr.flagged:
                flagged_entities.add(fmr.entity_id)
            if currency_by_entity.get(fmr.entity_id) is None:
                currency_by_entity[fmr.entity_id] = fmr.target_currency

        # Compliance (audit_qualitative) + file status + financials-added date per entity;
        # prefer the reconciliation-source file.
        src_stmt = (
            select(File.entity_id, File.status, File.created_at, File.is_reconciliation_source,
                   FileOCRMetadata.audit_qualitative)
            .select_from(File)
            .join(FileOCRMetadata, FileOCRMetadata.file_id == File.id, isouter=True)
            .where(File.entity_id.in_(entity_ids))
        )
        if review_cycle_id:
            src_stmt = src_stmt.where(File.review_cycle_id == review_cycle_id)
        aq_by_entity: dict[int, dict] = {}
        fstatus_by_entity: dict[int, Optional[str]] = {}
        added_by_entity: dict[int, Any] = {}
        for eid, fstatus, created, is_src, aq in (await db.execute(src_stmt)).all():
            cur = added_by_entity.get(eid)
            if created is not None and (cur is None or created < cur):
                added_by_entity[eid] = created
            if is_src or eid not in aq_by_entity:
                if isinstance(aq, dict):
                    aq_by_entity[eid] = aq
                fstatus_by_entity[eid] = fstatus

        rows: list[VarianceCompanyDetailRow] = []
        for i, (ent, pc) in enumerate(page):
            metrics_map = fmr_by_entity.get(ent.id, {})
            cells: list[MetricCell] = []
            for mk in _VARIANCE_METRICS:
                f = metrics_map.get(mk)
                if f is None:
                    cells.append(MetricCell(metric=mk))
                    continue
                afs = (
                    float(f.extracted_value_normalized) if f.extracted_value_normalized is not None
                    else (float(f.afs_amount) if f.afs_amount is not None else None)
                )
                cells.append(MetricCell(
                    metric=mk,
                    afs=afs,
                    mis=float(f.snowflake_value_normalized) if f.snowflake_value_normalized is not None else None,
                    diff_value=float(f.absolute_difference) if f.absolute_difference is not None else None,
                    diff_pct=f.percentage_difference,
                    bucket=("NC" if f.percentage_difference is None else ("<10%" if f.is_within_threshold else ">10%")),
                    to_be_sent=("Yes" if f.enable else None),
                    status=f.status,
                    company_remarks=f.company_response,
                    subcategory=f.variance_category,
                ))

            aq = aq_by_entity.get(ent.id) or {}
            caro = aq.get("caro") if isinstance(aq.get("caro"), dict) else {}
            ifc = aq.get("ifc") if isinstance(aq.get("ifc"), dict) else {}
            cat = (pc.extra_data or {}).get("auditor_category")
            auditor_cat = cat if cat in _AUDITOR_LABELS else _auditor_category(pc.auditor)
            etype = (ent.entity_type or "").lower()
            added = added_by_entity.get(ent.id)

            rows.append(VarianceCompanyDetailRow(
                no=offset + i + 1,
                portfolio_company_id=pc.id,
                entity_id=ent.id,
                cid=pc.company_id,
                company=pc.name,
                legal_name=ent.name,
                entity=ent.name,
                currency=currency_by_entity.get(ent.id),
                holding_or_subsidiary=ent.entity_type,
                consolidated_or_standalone=(
                    "Consolidated" if etype == "holding"
                    else ("Standalone" if etype in ("subsidiary", "subsdiary") else None)
                ),
                current_status=ent.status,
                financials_added=_iso_date(added),
                in_review=_iso_date(added),  # review begins when financials are added
                tat_review=_days_between(added, added),
                queries_sent=_iso_date(ent.discrepancy_email_sent_at),
                reminder_1=_iso_date(ent.reminder_1_sent_at),
                reminder_2=_iso_date(ent.reminder_2_sent_at),
                tat_queries_sent=_days_between(added, ent.discrepancy_email_sent_at),
                responded=_iso_date(ent.first_reply_received_at),
                tat_response=_days_between(ent.discrepancy_email_sent_at, ent.first_reply_received_at),
                approved_rejected_on=_iso_date(ent.resolved_at),
                tat_approval=_days_between(ent.first_reply_received_at, ent.resolved_at),
                overall_tat=_days_between(added, ent.resolved_at),
                highlighted_to_investor=ent.id in flagged_entities,
                auditor_name=pc.auditor,
                auditor_category=auditor_cat,
                status_of_financials=fstatus_by_entity.get(ent.id),
                auditor_opinion=(
                    _OPINION_LABEL.get(str(aq.get("opinion_type") or "").lower()) or aq.get("opinion_type")
                ),
                emphasis_of_matter=aq.get("emphasis_of_matter"),
                other_matters=aq.get("other_matters"),
                going_concern=aq.get("going_concern_material_uncertainty"),
                caro_availability=("Available" if caro.get("available") else "Not Available") if caro else None,
                caro_notes=caro.get("overall_assessment"),
                caro_gaps=caro.get("gaps"),
                internal_financial_control=("Available" if ifc.get("available") else "Not Available") if ifc else None,
                ifc_gaps=ifc.get("gaps"),
                metrics=cells,
                overall_status=ent.status,
                flagged_continuous_overdue=_flagged_continuous_overdue(
                    (pc.extra_data or {}).get("actual_status"),
                    pc.audit_status,
                    (pc.extra_data or {}).get("last_year_audit_status"),
                ),
            ))
        return rows, int(total)

    async def variance_discrepancy_companies(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        metric: Optional[str] = None,
        bucket: Optional[str] = None,
        subcategory: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[DiscrepancyDetailRow], int]:
        stmt = (
            select(FMR, Entity.name, PortfolioCompany.name)
            .join(Entity, Entity.id == FMR.entity_id)
            .join(PortfolioCompany, PortfolioCompany.id == FMR.portfolio_company_id)
        )
        if review_cycle_id:
            stmt = stmt.where(FMR.review_cycle == review_cycle_id)
        if metric:
            stmt = stmt.where(FMR.metric_key == metric.strip().lower())
        if subcategory:
            if subcategory.strip() == _VARIANCE_UNCATEGORIZED:
                # The synthetic "Uncategorized" reason = above-threshold rows with no
                # variance_category recorded.
                stmt = stmt.where(
                    or_(FMR.variance_category.is_(None), func.trim(FMR.variance_category) == ""),
                    _gt_condition(),
                )
            else:
                stmt = stmt.where(FMR.variance_category == subcategory)
        if bucket == "gt":
            stmt = stmt.where(_gt_condition())
        elif bucket == "lt":
            stmt = stmt.where(and_(FMR.percentage_difference.isnot(None), FMR.is_within_threshold.is_(True)))
        elif bucket == "nc":
            stmt = stmt.where(FMR.percentage_difference.is_(None))

        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0
        result = (await db.execute(stmt.order_by(PortfolioCompany.name).limit(limit).offset(offset))).all()
        bucket_display = {"gt": ">10%", "lt": "<10%", "nc": "Not Comparable"}.get(bucket or "")
        rows = []
        for fmr, ent_name, pc_name in result:
            mis = float(fmr.snowflake_value_normalized) if fmr.snowflake_value_normalized is not None else None
            afs = (
                float(fmr.extracted_value_normalized) if fmr.extracted_value_normalized is not None
                else (float(fmr.afs_amount) if fmr.afs_amount is not None else None)
            )
            diff = float(fmr.percentage_difference) if fmr.percentage_difference is not None else None
            rows.append(
                DiscrepancyDetailRow(
                    portfolio_company_id=fmr.portfolio_company_id, entity_id=fmr.entity_id,
                    company=pc_name, entity=ent_name, metric=fmr.metric_key,
                    mis_amount=mis, afs_amount=afs, diff_pct=diff, bucket=bucket_display,
                    variance_category=fmr.variance_category, reviewer_remarks=fmr.reviewer_remarks,
                    company_response=fmr.company_response, status=fmr.status,
                )
            )
        return rows, int(total)
