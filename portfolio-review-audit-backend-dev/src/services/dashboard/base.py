"""Dashboard data-provider interface.

Implemented by :class:`DbDashboardProvider`, which aggregates live data from the
database. Methods return Pydantic schemas consumed directly by the v1 routes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.schema.dashboard import (
    CompletionTimelineRead,
    ComplianceSummaryRead,
    DashboardCompanyRow,
    DashboardFiltersRead,
    DiscrepancyDetailRow,
    DiscrepancySubcategoryRead,
    DiscrepancySummaryRead,
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


class DashboardProvider(ABC):
    """One async method per dashboard endpoint. ``db`` is always passed (the dummy
    provider ignores it) so the call sites are identical across sources."""

    @abstractmethod
    async def filters(self, db: AsyncSession) -> DashboardFiltersRead: ...

    # ── Timeline dashboard ───────────────────────────────────────────────────
    @abstractmethod
    async def timeline_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None
    ) -> TimelineSummaryRead: ...

    @abstractmethod
    async def timeline_chart(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None
    ) -> TimelineChartFeed: ...

    @abstractmethod
    async def scoping_overview(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> ScopingOverviewRead: ...

    @abstractmethod
    async def scoping_tentative_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> StatusMatrixRead: ...

    @abstractmethod
    async def scoping_actual_matrix(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], **dims
    ) -> StatusMatrixRead: ...

    @abstractmethod
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
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[DashboardCompanyRow], int]: ...

    @abstractmethod
    async def scoping_completion_timeline(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        status: Optional[str] = None,
        fy_end: Optional[str] = None,
    ) -> CompletionTimelineRead: ...

    @abstractmethod
    async def scoping_overall_status(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None, **dims
    ) -> "YoYSummaryRead": ...

    @abstractmethod
    async def scoping_auditor_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str] = None, **dims
    ) -> "YoYSummaryRead": ...

    # ── Variance / Review dashboard ──────────────────────────────────────────
    @abstractmethod
    async def variance_actionables(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> VarianceActionablesRead: ...

    @abstractmethod
    async def variance_cycle_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> VarianceCycleSummaryRead: ...

    @abstractmethod
    async def variance_compliance(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> ComplianceSummaryRead: ...

    @abstractmethod
    async def variance_discrepancy_summary(
        self, db: AsyncSession, *, review_cycle_id: Optional[str]
    ) -> DiscrepancySummaryRead: ...

    @abstractmethod
    async def variance_discrepancy_subcategory(
        self, db: AsyncSession, *, review_cycle_id: Optional[str], metric: str
    ) -> DiscrepancySubcategoryRead: ...

    @abstractmethod
    async def variance_report_view(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        kind: str = "long_overdue",
        current_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[ReportViewRow], int]: ...

    @abstractmethod
    async def variance_discrepancy_companies(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        metric: Optional[str] = None,
        bucket: Optional[str] = None,         # 'gt' | 'lt' | 'nc'
        subcategory: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[DiscrepancyDetailRow], int]: ...

    @abstractmethod
    async def variance_company_details(
        self,
        db: AsyncSession,
        *,
        review_cycle_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[VarianceCompanyDetailRow], int]: ...
