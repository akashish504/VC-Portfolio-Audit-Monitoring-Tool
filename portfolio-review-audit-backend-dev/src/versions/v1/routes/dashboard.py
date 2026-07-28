"""Analytics dashboard routes (Scoping + Variance).

Read-only aggregation endpoints backed by :func:`get_dashboard_provider` (live DB
aggregation). Mounted under ``/v1/dashboard`` (distinct from the "Dashboard Data"
XLSX routes).
"""
from __future__ import annotations

from typing import Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.common import Page
from src.schema.dashboard import (
    CompletionTimelineRead,
    ComplianceSummaryRead,
    DashboardCompanyRow,
    DashboardFiltersRead,
    DiscrepancyDetailRow,
    DiscrepancySubcategoryMatrixRead,
    DiscrepancySubcategoryRead,
    DiscrepancySummaryRead,
    PCMDealRow,
    ReportViewRow,
    ScopingOverviewRead,
    StatusMatrixRead,
    TimelineChartFeed,
    TimelineSummaryRead,
    VarianceActionablesRead,
    VarianceCompanyDetailRow,
    VarianceCycleSummaryRead,
    YoYSummaryRead,
)
from src.services.dashboard import get_dashboard_provider
from src.services.dashboard_grid_export import (
    build_auditor_summary_xlsx,
    build_deal_stage_matrix_xlsx,
    build_discrepancy_reasons_xlsx,
    build_discrepancy_summary_xlsx,
    build_overall_status_xlsx,
    build_status_matrix_xlsx,
)
from src.services.scoping_export import build_pcm_deals_xlsx, build_scoping_companies_xlsx
from src.services.timeline_export import build_timeline_xlsx
from src.services.variance_status_export import build_variance_status_breakdown_xlsx


def _xlsx_response(payload: tuple[bytes, str]) -> Response:
    xlsx_bytes, filename = payload
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

router = APIRouter()


def _pagination(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return limit, offset


def _dimensions(
    fund: Optional[str] = Query(default=None),
    geography: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    investment_lead: Optional[str] = Query(default=None),
) -> dict:
    """Dimension filters (spec R1) shared by the scoping summary endpoints."""
    raw = dict(fund=fund, geography=geography, sector=sector, strategy=strategy,
               category=category, investment_lead=investment_lead)
    return {k: v for k, v in raw.items() if v}


def _scoping_filters(
    review_cycle_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    fy_end: Optional[str] = Query(default=None),
    fund: Optional[str] = Query(default=None),
    geography: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    investment_lead: Optional[str] = Query(default=None),
    auditor_category: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    completion_month: Optional[str] = Query(default=None),
    scoped_in: Optional[bool] = Query(default=None),
    flagged: Optional[bool] = Query(default=None),
    entity_status: Optional[str] = Query(default=None),
    audit_status: Optional[str] = Query(default=None),
    actual_status: Optional[str] = Query(default=None),
    opinion: Optional[str] = Query(default=None),
    delta: Optional[str] = Query(default=None),
    deal_level_stage_1: Optional[str] = Query(default=None),
    deal_level_stage_2: Optional[str] = Query(default=None),
) -> dict:
    """The full scoping-company filter set, shared by the list + download routes."""
    return dict(
        review_cycle_id=review_cycle_id, status=status, fy_end=fy_end, fund=fund,
        geography=geography, sector=sector, strategy=strategy, category=category,
        investment_lead=investment_lead, auditor_category=auditor_category, q=q,
        completion_month=completion_month, scoped_in=scoped_in, flagged=flagged,
        entity_status=entity_status, audit_status=audit_status, actual_status=actual_status,
        opinion=opinion, delta=delta, deal_level_stage_1=deal_level_stage_1,
        deal_level_stage_2=deal_level_stage_2,
    )


@router.get("/filters", response_model=DashboardFiltersRead)
async def get_dashboard_filters(db: AsyncSession = Depends(get_db)):
    return await get_dashboard_provider().filters(db)


@router.get("/timeline/summary", response_model=TimelineSummaryRead)
async def get_timeline_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Full Timeline page payload (master-scoping strategy/geo/included counts +
    the audit-timeline status cross-tabs) for a review cycle."""
    return await get_dashboard_provider().timeline_summary(db, review_cycle_id=review_cycle_id)


@router.get("/timeline/chart", response_model=TimelineChartFeed)
async def get_timeline_chart(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Row-level feed + filter options for the filterable audit-timeline chart
    (tentative-completion month × unique-company count). Filtering happens client-side."""
    return await get_dashboard_provider().timeline_chart(db, review_cycle_id=review_cycle_id)


@router.get(
    "/timeline/summary/download",
    summary="Download a Timeline table (strategy / analysis / status / matrices) as XLSX",
)
async def download_timeline_table(
    review_cycle_id: Optional[str] = Query(default=None),
    table: str = Query(default="all", description="strategy_all|strategy_included|analysis|status|matrices|all"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(
        await build_timeline_xlsx(db, review_cycle_id=review_cycle_id, table=table)
    )


# ── Aggregated-grid XLSX downloads (the summary tables, as shown) ─────────────
@router.get("/scoping/status-matrix/download", summary="Download the scoping status matrix as XLSX")
async def download_status_matrix(
    review_cycle_id: Optional[str] = Query(default=None),
    view: str = Query(default="tentative", description="tentative|actual"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_status_matrix_xlsx(db, view=view, review_cycle_id=review_cycle_id))


@router.get("/scoping/deal-stage-matrix/download", summary="Download a deal-stage FYE matrix as XLSX")
async def download_deal_stage_matrix(
    review_cycle_id: Optional[str] = Query(default=None),
    stage: str = Query(default="1", description="1|2"),
    basis: str = Query(default="cid", description="cid|cid_strategy"),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(
        await build_deal_stage_matrix_xlsx(db, stage=stage, basis=basis, review_cycle_id=review_cycle_id, **dims)
    )


@router.get("/scoping/overall-status/download", summary="Download the overall-status table as XLSX")
async def download_overall_status(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_overall_status_xlsx(db, review_cycle_id=review_cycle_id, **dims))


@router.get("/scoping/auditor-summary/download", summary="Download the auditor-summary table as XLSX")
async def download_auditor_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_auditor_summary_xlsx(db, review_cycle_id=review_cycle_id, **dims))


@router.get("/variance/discrepancy-summary/download", summary="Download the discrepancy-summary table as XLSX")
async def download_discrepancy_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_discrepancy_summary_xlsx(db, review_cycle_id=review_cycle_id))


@router.get("/variance/discrepancy-reasons/download", summary="Download the discrepancy-reasons matrix as XLSX")
async def download_discrepancy_reasons(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_discrepancy_reasons_xlsx(db, review_cycle_id=review_cycle_id))


@router.get("/scoping/overview", response_model=ScopingOverviewRead)
async def get_scoping_overview(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_overview(db, review_cycle_id=review_cycle_id, **dims)


@router.get("/scoping/tentative-matrix", response_model=StatusMatrixRead)
async def get_scoping_tentative_matrix(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_tentative_matrix(db, review_cycle_id=review_cycle_id, **dims)


@router.get("/scoping/actual-matrix", response_model=StatusMatrixRead)
async def get_scoping_actual_matrix(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_actual_matrix(db, review_cycle_id=review_cycle_id, **dims)


@router.get("/scoping/deal-stage-matrix", response_model=StatusMatrixRead)
async def get_scoping_deal_stage_matrix(
    stage: Literal["1", "2"] = Query("1", description="Deal level stage 1 or 2"),
    basis: Literal["cid", "cid_strategy"] = Query(
        "cid", description="Count basis: unique CID, or unique CID + Strategy"
    ),
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    """Master-scoping deal-stage x FYE-month matrix (counts by CID / CID+Strategy)."""
    return await get_dashboard_provider().deal_stage_matrix(
        db, review_cycle_id=review_cycle_id, stage=stage, basis=basis, **dims
    )


@router.get("/scoping/pcm-deals", response_model=Page[PCMDealRow])
async def list_pcm_deals(
    deal_level_stage_1: Optional[str] = Query(default=None),
    deal_level_stage_2: Optional[str] = Query(default=None),
    scoping_for_audit: Optional[bool] = Query(
        default=None, description="True = scoped in for audit, False = scoped out"
    ),
    fy_end: Optional[str] = Query(default=None, description="FYE month label, e.g. 'Dec-25'"),
    category_of_auditor: Optional[str] = Query(
        default=None, description="Master-scoping auditor category, e.g. 'Big 4'"
    ),
    strategy: Optional[str] = Query(default=None),
    geo_l1: Optional[str] = Query(default=None, description="India / SEA (Timeline drills)"),
    india_sea_only: bool = Query(default=False, description="Restrict to India/SEA (Timeline totals)"),
    deal_ids: Optional[str] = Query(default=None, description="Comma-separated CIDs (chart drills)"),
    unique: bool = Query(default=False, description="One row per CID (Timeline unique-count drills)"),
    fund: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    investment_lead: Optional[str] = Query(default=None),
    review_cycle_id: Optional[str] = Query(default=None),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await get_dashboard_provider().pcm_deals_by_stage(
        db,
        deal_level_stage_1=deal_level_stage_1,
        deal_level_stage_2=deal_level_stage_2,
        scoping_for_audit=scoping_for_audit,
        fy_end=fy_end,
        category_of_auditor=category_of_auditor,
        strategy=strategy,
        geo_l1=geo_l1,
        india_sea_only=india_sea_only,
        deal_ids=[d.strip() for d in deal_ids.split(",") if d.strip()] if deal_ids else None,
        unique=unique,
        fund=fund,
        sector=sector,
        category=category,
        investment_lead=investment_lead,
        review_cycle_id=review_cycle_id,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total)


@router.get(
    "/scoping/pcm-deals/download",
    summary="Download the complete (non-paginated) master-scoping deal list as XLSX",
)
async def download_pcm_deals(
    deal_level_stage_1: Optional[str] = Query(default=None),
    deal_level_stage_2: Optional[str] = Query(default=None),
    scoping_for_audit: Optional[bool] = Query(default=None),
    fy_end: Optional[str] = Query(default=None),
    category_of_auditor: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    geo_l1: Optional[str] = Query(default=None),
    india_sea_only: bool = Query(default=False),
    deal_ids: Optional[str] = Query(default=None),
    unique: bool = Query(default=False),
    fund: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    investment_lead: Optional[str] = Query(default=None),
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(
        await build_pcm_deals_xlsx(
            db,
            deal_level_stage_1=deal_level_stage_1,
            deal_level_stage_2=deal_level_stage_2,
            scoping_for_audit=scoping_for_audit,
            fy_end=fy_end,
            category_of_auditor=category_of_auditor,
            strategy=strategy,
            geo_l1=geo_l1,
            india_sea_only=india_sea_only,
            deal_ids=[d.strip() for d in deal_ids.split(",") if d.strip()] if deal_ids else None,
            unique=unique,
            fund=fund,
            sector=sector,
            category=category,
            investment_lead=investment_lead,
            review_cycle_id=review_cycle_id,
        )
    )


@router.get("/scoping/companies", response_model=Page[DashboardCompanyRow])
async def list_scoping_companies(
    review_cycle_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="Match audit/in-review/stage status"),
    fy_end: Optional[str] = Query(default=None, description="FYE month label, e.g. 'Dec-25'"),
    fund: Optional[str] = Query(default=None),
    geography: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    investment_lead: Optional[str] = Query(default=None),
    auditor_category: Optional[str] = Query(default=None, description="BIG 4 | BIG 6 | BIG 10 | Others"),
    q: Optional[str] = Query(default=None, description="Search company name or id"),
    completion_month: Optional[str] = Query(default=None, description="Filter by tentative completion month, e.g. 'Dec-25'"),
    scoped_in: Optional[bool] = Query(default=None, description="Restrict to companies with financials uploaded this cycle"),
    flagged: Optional[bool] = Query(default=None, description="Restrict to companies flagged/highlighted to investor"),
    entity_status: Optional[str] = Query(default=None, description="Match the per-entity review status (entities.status); comma-separated"),
    audit_status: Optional[str] = Query(default=None, description="Match the tentative scoping status (audit_status); comma-separated"),
    actual_status: Optional[str] = Query(default=None, description="Match the actual-completion status (extra_data.actual_status); comma-separated"),
    opinion: Optional[str] = Query(default=None, description="Auditor opinion label, e.g. 'Clean' | 'Qualified'"),
    delta: Optional[str] = Query(default=None, description="'added' (new vs prior cycle) | 'removed' (dropped since prior cycle)"),
    deal_level_stage_1: Optional[str] = Query(default=None, description="Filter by PortfolioCompanyMetadata.deal_level_stage_1"),
    deal_level_stage_2: Optional[str] = Query(default=None, description="Filter by PortfolioCompanyMetadata.deal_level_stage_2"),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await get_dashboard_provider().scoping_companies(
        db,
        review_cycle_id=review_cycle_id,
        status=status,
        fy_end=fy_end,
        fund=fund,
        geography=geography,
        sector=sector,
        strategy=strategy,
        category=category,
        investment_lead=investment_lead,
        auditor_category=auditor_category,
        q=q,
        completion_month=completion_month,
        scoped_in=scoped_in,
        flagged=flagged,
        entity_status=entity_status,
        audit_status=audit_status,
        actual_status=actual_status,
        opinion=opinion,
        delta=delta,
        deal_level_stage_1=deal_level_stage_1,
        deal_level_stage_2=deal_level_stage_2,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total)


@router.get(
    "/scoping/companies/download",
    summary="Download the complete (non-paginated) company list as XLSX",
)
async def download_scoping_companies(
    filters: dict = Depends(_scoping_filters),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_scoping_companies_xlsx(db, **filters))


@router.get("/scoping/overall-status", response_model=YoYSummaryRead)
async def get_scoping_overall_status(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_overall_status(db, review_cycle_id=review_cycle_id, **dims)


@router.get("/scoping/auditor-summary", response_model=YoYSummaryRead)
async def get_scoping_auditor_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    dims: dict = Depends(_dimensions),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_auditor_summary(db, review_cycle_id=review_cycle_id, **dims)


@router.get("/scoping/completion-timeline", response_model=CompletionTimelineRead)
async def get_scoping_completion_timeline(
    review_cycle_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="Matrix row status of the clicked cell"),
    fy_end: Optional[str] = Query(default=None, description="FYE month of the clicked cell, e.g. 'Jun-25'"),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().scoping_completion_timeline(
        db, review_cycle_id=review_cycle_id, status=status, fy_end=fy_end
    )


# ── Variance / Review dashboard ──────────────────────────────────────────────
@router.get("/variance/actionables-summary", response_model=VarianceActionablesRead)
async def get_variance_actionables(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().variance_actionables(db, review_cycle_id=review_cycle_id)


@router.get("/variance/cycle-summary", response_model=VarianceCycleSummaryRead)
async def get_variance_cycle_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().variance_cycle_summary(db, review_cycle_id=review_cycle_id)


@router.get(
    "/variance/status-breakdown/download",
    summary="Download the current-cycle review-status breakdown (deal-level) as XLSX",
)
async def download_variance_status_breakdown(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _xlsx_response(await build_variance_status_breakdown_xlsx(db, review_cycle_id))


@router.get("/variance/compliance-summary", response_model=ComplianceSummaryRead)
async def get_variance_compliance(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().variance_compliance(db, review_cycle_id=review_cycle_id)


@router.get("/variance/discrepancy-summary", response_model=DiscrepancySummaryRead)
async def get_variance_discrepancy_summary(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().variance_discrepancy_summary(db, review_cycle_id=review_cycle_id)


@router.get("/variance/discrepancy-subcategory", response_model=DiscrepancySubcategoryRead)
async def get_variance_discrepancy_subcategory(
    metric: str = Query(..., description="One of: revenue, ebitda, pbt, pat, cash, debt"),
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await get_dashboard_provider().variance_discrepancy_subcategory(
        db, review_cycle_id=review_cycle_id, metric=metric
    )


@router.get("/variance/discrepancy-subcategory-matrix", response_model=DiscrepancySubcategoryMatrixRead)
async def get_variance_discrepancy_subcategory_matrix(
    review_cycle_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Above-threshold variance reasons broken down per metric (combined matrix)."""
    return await get_dashboard_provider().variance_discrepancy_subcategory_matrix(
        db, review_cycle_id=review_cycle_id
    )


@router.get("/variance/discrepancy-companies", response_model=Page[DiscrepancyDetailRow])
async def get_variance_discrepancy_companies(
    metric: Optional[str] = Query(default=None, description="revenue|ebitda|pbt|pat|cash|debt"),
    bucket: Optional[str] = Query(default=None, description="gt | lt | nc"),
    subcategory: Optional[str] = Query(default=None, description="variance sub-category"),
    review_cycle_id: Optional[str] = Query(default=None),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await get_dashboard_provider().variance_discrepancy_companies(
        db, review_cycle_id=review_cycle_id, metric=metric, bucket=bucket,
        subcategory=subcategory, limit=limit, offset=offset,
    )
    return Page(items=items, total=total)


@router.get("/variance/company-details", response_model=Page[VarianceCompanyDetailRow])
async def get_variance_company_details(
    review_cycle_id: Optional[str] = Query(default=None),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    """Per-entity detail for scoped-in companies (spec sheet 3 master table)."""
    limit, offset = pagination
    items, total = await get_dashboard_provider().variance_company_details(
        db, review_cycle_id=review_cycle_id, limit=limit, offset=offset,
    )
    return Page(items=items, total=total)


@router.get("/variance/report-view", response_model=Page[ReportViewRow])
async def get_variance_report_view(
    kind: str = Query("long_overdue", description="long_overdue | previous_years_pending"),
    current_status: Optional[str] = Query(default=None, description="Filter long-overdue by current status"),
    review_cycle_id: Optional[str] = Query(default=None),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await get_dashboard_provider().variance_report_view(
        db, review_cycle_id=review_cycle_id, kind=kind, current_status=current_status,
        limit=limit, offset=offset,
    )
    return Page(items=items, total=total)
