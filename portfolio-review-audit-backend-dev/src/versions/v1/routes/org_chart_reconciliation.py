"""
API routes for org-chart reconciliation phase.

Endpoints (all under /api/v1 prefix added in main.py):
  GET  /portfolio-companies/{portfolio_company_id}/org-chart/pending-update
  GET  /portfolio-companies/{portfolio_company_id}/org-chart/download
  POST /org-chart-records/{record_id}/apply
  POST /org-chart-records/{record_id}/reconcile
  POST /org-chart-records/{record_id}/dismiss
"""
from __future__ import annotations

import io
import logging
from typing import Any, Optional

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select as sa_select

from src.db.models import Entity, OrgChartUploadRecord
from src.db.session import get_db
from src.services.org_chart_reconciliation import (
    apply_org_chart_record,
    dismiss_org_chart_record,
    get_pending_update,
    reconcile_org_chart_record,
)
from src.services.portfolio import PortfolioService

logger = logging.getLogger(__name__)
router = APIRouter()


# ── GET /portfolio-companies/{portfolio_company_id}/org-chart/pending-update ───

@router.get(
    "/portfolio-companies/{portfolio_company_id}/org-chart/pending-update",
    tags=["OrgChartReconciliation"],
)
async def get_org_chart_pending_update(
    portfolio_company_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await get_pending_update(db, portfolio_company_id)
    if result is None:
        return {"has_pending_update": False}
    return {"has_pending_update": True, **result}


# ── GET /portfolio-companies/{portfolio_company_id}/org-chart/download ───────────

@router.get(
    "/portfolio-companies/{portfolio_company_id}/org-chart/download",
    tags=["OrgChartReconciliation"],
)
async def download_org_chart_xlsx(
    portfolio_company_id: int,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    company = await PortfolioService.get_portfolio_company(db, portfolio_company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    entities = await PortfolioService.list_entities_for_company(db, portfolio_company_id)

    # Build a lookup so we can resolve parent names
    id_to_name: dict[int, str] = {e.id: e.name for e in entities}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Org Chart"

    headers = [
        "ID",
        "Name",
        "Geolocation",
        "Entity Type",
        "Is Root",
        "Parent Entity ID",
        "Parent Entity Name",
        "Status",
        "Review Cycle",
        "FY End",
        "Region",
    ]
    ws.append(headers)

    for e in entities:
        parent_name = id_to_name.get(e.parent_entity_id) if e.parent_entity_id else None
        ws.append([
            e.id,
            e.name,
            e.geolocation or "",
            e.entity_type or "",
            "Yes" if e.is_parent else "No",
            e.parent_entity_id or "",
            parent_name or "",
            e.status or "",
            e.review_cycle or "",
            e.fy_end or "",
            e.region or "",
        ])

    safe_name = (company.name or str(portfolio_company_id)).replace(" ", "_").replace("/", "-")
    filename = f"org_chart_{safe_name}.xlsx"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /org-chart-records/{record_id}/apply ──────────────────────────────────

class ApplyRequest(BaseModel):
    applied_by: Optional[str] = None


@router.post(
    "/org-chart-records/{record_id}/apply",
    tags=["OrgChartReconciliation"],
)
async def apply_org_chart(
    record_id: int,
    body: ApplyRequest = ApplyRequest(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        entities = await apply_org_chart_record(
            db,
            record_id=record_id,
            applied_by=body.applied_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"applied": True, "entities": entities, "entity_count": len(entities)}


# ── POST /org-chart-records/{record_id}/reconcile ─────────────────────────────

class EntityMappingItem(BaseModel):
    existing_entity_id: Optional[int] = None
    extracted_temp_id: Optional[int] = None
    action: str  # "match" | "create" | "keep" | "archive"
    final_name: Optional[str] = None
    final_geolocation: Optional[str] = None
    final_entity_type: Optional[str] = None
    final_is_parent: Optional[bool] = None


class ParentLinkItem(BaseModel):
    child_ref: str
    parent_ref: Optional[str] = None


class FileMoveItem(BaseModel):
    file_id: int
    target_entity_ref: Optional[str] = None
    acknowledge_detached: bool = False


class ReconcileRequest(BaseModel):
    portfolio_company_id: int
    entity_mappings: list[EntityMappingItem] = []
    parent_links: list[ParentLinkItem] = []
    file_moves: list[FileMoveItem] = []
    applied_by: Optional[str] = None


@router.post(
    "/org-chart-records/{record_id}/reconcile",
    tags=["OrgChartReconciliation"],
)
async def reconcile_org_chart(
    record_id: int,
    body: ReconcileRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await reconcile_org_chart_record(
            db,
            record_id=record_id,
            portfolio_company_id=body.portfolio_company_id,
            entity_mappings=[m.model_dump() for m in body.entity_mappings],
            parent_links=[p.model_dump() for p in body.parent_links],
            file_moves=[f.model_dump() for f in body.file_moves],
            applied_by=body.applied_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    entities = result["entities"]
    return {
        "reconciled": True,
        "entities": entities,
        "entity_count": len(entities),
        "new_org_chart_file_id": result["new_org_chart_file_id"],
    }


# ── GET /org-chart-records/{record_id}/comparison-download ───────────────────

@router.get(
    "/org-chart-records/{record_id}/comparison-download",
    tags=["OrgChartReconciliation"],
)
async def download_reconciliation_comparison_xlsx(
    record_id: int,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    result = await db.execute(
        sa_select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    extracted: list[dict] = record.extracted_org_chart or []

    existing_q = await db.execute(
        sa_select(Entity).where(
            Entity.portfolio_company_id == record.portfolio_company_id
        )
    )
    existing_entities = list(existing_q.scalars())
    existing_id_to_name: dict[int, str] = {e.id: e.name for e in existing_entities}

    wb = openpyxl.Workbook()

    # Sheet 1: Existing (current DB entities)
    ws_existing = wb.active
    ws_existing.title = "Existing (Current)"
    ws_existing.append([
        "ID", "Name", "Geolocation", "Entity Type", "Is Root",
        "Parent Entity ID", "Parent Entity Name", "Status",
        "Review Cycle", "FY End", "Region",
    ])
    for e in existing_entities:
        parent_name = existing_id_to_name.get(e.parent_entity_id) if e.parent_entity_id else None
        ws_existing.append([
            e.id,
            e.name,
            e.geolocation or "",
            e.entity_type or "",
            "Yes" if e.is_parent else "No",
            e.parent_entity_id or "",
            parent_name or "",
            e.status or "",
            e.review_cycle or "",
            e.fy_end or "",
            e.region or "",
        ])

    # Sheet 2: Incoming (extracted from uploaded file)
    ws_extracted = wb.create_sheet(title="Incoming (Extracted)")
    ws_extracted.append([
        "LLM ID", "Name", "Geolocation", "Entity Type", "Is Root",
        "Parent LLM IDs (children references)",
    ])
    llm_id_to_name: dict[int, str] = {
        int(e["llm_id"]): e.get("name", "") for e in extracted
    }
    for e in extracted:
        llm_id = int(e.get("llm_id", ""))
        children_ids = e.get("children_ids") or []
        children_names = ", ".join(
            llm_id_to_name.get(int(c), str(c)) for c in children_ids
        )
        ws_extracted.append([
            llm_id,
            e.get("name") or "",
            e.get("geolocation") or "",
            e.get("entity_type") or "",
            "Yes" if e.get("is_parent") else "No",
            children_names,
        ])

    filename = f"org_chart_reconciliation_{record_id}.xlsx"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /org-chart-records/{record_id}/dismiss ────────────────────────────────

class DismissRequest(BaseModel):
    applied_by: Optional[str] = None


@router.post(
    "/org-chart-records/{record_id}/dismiss",
    tags=["OrgChartReconciliation"],
)
async def dismiss_org_chart(
    record_id: int,
    body: DismissRequest = DismissRequest(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        await dismiss_org_chart_record(db, record_id=record_id, applied_by=body.applied_by)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"dismissed": True}
