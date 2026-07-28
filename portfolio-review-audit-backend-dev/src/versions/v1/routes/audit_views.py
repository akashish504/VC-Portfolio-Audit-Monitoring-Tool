from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    CompanyViewAudit,
    FinancialExtractionMappingViewAudit,
    ParameterThresholdViewAudit,
    ReviewCycleViewAudit,
    SnowflakePRFinancialMappingViewAudit,
)
from src.db.session import get_db

router = APIRouter()


def _serialize_settings_audit_row(row: Any) -> dict[str, Any]:
    meta = dict(row.meta or {})
    out: dict[str, Any] = {
        "id": row.id,
        "user_id": row.user_id,
        "action": row.action,
        "meta": meta,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "occurred_at": meta.get("occurred_at") or (row.updated_at.isoformat() if row.updated_at else None),
        "summary": meta.get("summary") or row.action,
    }
    if hasattr(row, "review_cycle_id"):
        out["review_cycle_id"] = row.review_cycle_id
    if hasattr(row, "company_id"):
        out["company_id"] = row.company_id
    return out


def _fuzzy_filter(model: Any, q: Optional[str]):
    if not q or not str(q).strip():
        return None
    term = f"%{str(q).strip()}%"
    parts = [
        model.action.ilike(term),
        cast(model.meta, String).ilike(term),
    ]
    if hasattr(model, "meta"):
        search_col = model.meta.op("->>")("search_text")
        parts.append(search_col.ilike(term))
    return or_(*parts)


@router.get("/audit/review-cycle-view", tags=["Audit views"])
async def list_review_cycle_view(
    review_cycle_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Fuzzy search on action and details"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ReviewCycleViewAudit)
    count_stmt = select(func.count(ReviewCycleViewAudit.id))
    if review_cycle_id:
        stmt = stmt.where(ReviewCycleViewAudit.review_cycle_id == review_cycle_id)
        count_stmt = count_stmt.where(ReviewCycleViewAudit.review_cycle_id == review_cycle_id)
    fuzzy = _fuzzy_filter(ReviewCycleViewAudit, q)
    if fuzzy is not None:
        stmt = stmt.where(fuzzy)
        count_stmt = count_stmt.where(fuzzy)

    total = (await db.execute(count_stmt)).scalar_one()
    occurred_at = ReviewCycleViewAudit.meta.op("->>")("occurred_at")
    sort_expr = func.coalesce(
        occurred_at,
        cast(ReviewCycleViewAudit.updated_at, String),
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(sort_expr)).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize_settings_audit_row(r) for r in rows], "total": total}


@router.get("/audit/parameter-threshold-view", tags=["Audit views"])
async def list_parameter_threshold_view(
    q: Optional[str] = Query(default=None, description="Fuzzy search on action and details"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ParameterThresholdViewAudit)
    count_stmt = select(func.count(ParameterThresholdViewAudit.id))
    fuzzy = _fuzzy_filter(ParameterThresholdViewAudit, q)
    if fuzzy is not None:
        stmt = stmt.where(fuzzy)
        count_stmt = count_stmt.where(fuzzy)

    total = (await db.execute(count_stmt)).scalar_one()
    occurred_at = ParameterThresholdViewAudit.meta.op("->>")("occurred_at")
    sort_expr = func.coalesce(
        occurred_at,
        cast(ParameterThresholdViewAudit.updated_at, String),
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(sort_expr)).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize_settings_audit_row(r) for r in rows], "total": total}


@router.get("/audit/financial-extraction-mapping-view", tags=["Audit views"])
async def list_financial_extraction_mapping_view(
    q: Optional[str] = Query(default=None, description="Fuzzy search on action and details"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(FinancialExtractionMappingViewAudit)
    count_stmt = select(func.count(FinancialExtractionMappingViewAudit.id))
    fuzzy = _fuzzy_filter(FinancialExtractionMappingViewAudit, q)
    if fuzzy is not None:
        stmt = stmt.where(fuzzy)
        count_stmt = count_stmt.where(fuzzy)

    total = (await db.execute(count_stmt)).scalar_one()
    occurred_at = FinancialExtractionMappingViewAudit.meta.op("->>")("occurred_at")
    sort_expr = func.coalesce(
        occurred_at,
        cast(FinancialExtractionMappingViewAudit.updated_at, String),
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(sort_expr)).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize_settings_audit_row(r) for r in rows], "total": total}


@router.get("/audit/snowflake-pr-financial-mapping-view", tags=["Audit views"])
async def list_snowflake_pr_financial_mapping_view(
    q: Optional[str] = Query(default=None, description="Fuzzy search on action and details"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SnowflakePRFinancialMappingViewAudit)
    count_stmt = select(func.count(SnowflakePRFinancialMappingViewAudit.id))
    fuzzy = _fuzzy_filter(SnowflakePRFinancialMappingViewAudit, q)
    if fuzzy is not None:
        stmt = stmt.where(fuzzy)
        count_stmt = count_stmt.where(fuzzy)

    total = (await db.execute(count_stmt)).scalar_one()
    occurred_at = SnowflakePRFinancialMappingViewAudit.meta.op("->>")("occurred_at")
    sort_expr = func.coalesce(
        occurred_at,
        cast(SnowflakePRFinancialMappingViewAudit.updated_at, String),
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(sort_expr)).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize_settings_audit_row(r) for r in rows], "total": total}


@router.get("/audit/company-view", tags=["Audit views"])
async def list_company_view(
    company_id: Optional[str] = Query(default=None, description="portfolio_companies.id as string"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CompanyViewAudit)
    count_stmt = select(func.count(CompanyViewAudit.id))
    if company_id:
        stmt = stmt.where(CompanyViewAudit.company_id == company_id)
        count_stmt = count_stmt.where(CompanyViewAudit.company_id == company_id)

    total = (await db.execute(count_stmt)).scalar_one()
    occurred_at = CompanyViewAudit.meta.op("->>")("occurred_at")
    sort_expr = func.coalesce(
        occurred_at,
        cast(CompanyViewAudit.updated_at, String),
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(sort_expr)).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_serialize_settings_audit_row(r) for r in rows], "total": total}
