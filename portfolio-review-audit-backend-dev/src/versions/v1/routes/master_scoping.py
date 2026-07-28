"""
Master Scoping endpoints.

GET  /api/v1/master-scoping/download  → XLSX of all portfolio_company_metadata rows
POST /api/v1/master-scoping/upload    → upsert from uploaded XLSX
GET  /api/v1/master-scoping           → JSON list of all rows
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Entity, PortfolioCompany, PortfolioCompanyMetadata, ReviewCycle
from src.db.session import get_db
from src.schema.portfolio import CompanyReviewStage
from src.services.company_audit_recorder import SYSTEM_ACTOR, CompanyAuditRecorder
from src.services.company_audit_context import get_audit_actor_email
from src.services.master_scoping_export import (
    _EDITABLE_COLS,
    _parse_cell,
    BulkUpdateResult,
    UploadResult,
    build_master_scoping_xlsx,
    process_master_scoping_bulk_update,
    process_master_scoping_upload,
    recompute_aggregates,
)

router = APIRouter()

# Fields editable via the per-record PATCH endpoint (inline UI editing), mapped
# to (header label, col_type) for validation reuse. review_cycle_id is excluded —
# cycle assignment is an upload-time concern, not a per-cell edit.
_EDITABLE_FIELD_TYPES: dict[str, tuple[str, str]] = {
    field: (label, col_type)
    for field, label, col_type in _EDITABLE_COLS
    if field != "review_cycle_id"
}


@router.get(
    "/master-scoping/download",
    tags=["Master Scoping"],
    summary="Download full master scoping table as XLSX",
)
async def download_master_scoping(
    review_cycle_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        xlsx_bytes, filename = await build_master_scoping_xlsx(db, review_cycle_id=review_cycle_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/master-scoping/upload",
    tags=["Master Scoping"],
    summary="Upload XLSX to upsert master scoping records",
)
async def upload_master_scoping(
    file: UploadFile = FastAPIFile(...),
    review_cycle_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    review_cycle_id = (review_cycle_id or "").strip()
    if not review_cycle_id:
        raise HTTPException(status_code=422, detail="A review cycle must be selected before uploading")

    cycle = await db.get(ReviewCycle, review_cycle_id)
    if cycle is None:
        raise HTTPException(status_code=422, detail=f"Review cycle '{review_cycle_id}' does not exist")

    content_type = (file.content_type or "").lower()
    valid_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/octet-stream",
    }
    filename_lower = (file.filename or "").lower()
    if content_type not in valid_types and not filename_lower.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Uploaded file must be an Excel workbook (.xlsx)")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    try:
        result: UploadResult = await process_master_scoping_upload(db, file_bytes, review_cycle_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.to_dict()


@router.get(
    "/master-scoping",
    tags=["Master Scoping"],
    summary="List all master scoping records as JSON",
)
async def list_master_scoping(
    review_cycle_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(PortfolioCompanyMetadata).order_by(
        PortfolioCompanyMetadata.fund,
        PortfolioCompanyMetadata.deal_id,
        PortfolioCompanyMetadata.strategy,
    )
    if review_cycle_id:
        stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
    records = (await db.execute(stmt)).scalars().all()
    return [_serialize_metadata(r) for r in records]


@router.delete(
    "/master-scoping/{record_id}",
    tags=["Master Scoping"],
    summary="Delete a single master scoping record",
    status_code=204,
)
async def delete_master_scoping(
    record_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    rec = await db.get(PortfolioCompanyMetadata, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Master scoping record {record_id} not found")
    await db.delete(rec)
    await db.commit()


@router.post(
    "/master-scoping/bulk",
    tags=["Master Scoping"],
    summary="Set one dropdown field across many master scoping records",
)
async def bulk_update_master_scoping(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    ids = payload.get("ids")
    field = payload.get("field")
    value = payload.get("value")

    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=422, detail="'ids' must be a non-empty list")
    if not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=422, detail="'ids' must contain integers")
    if not isinstance(field, str) or not field:
        raise HTTPException(status_code=422, detail="'field' is required")

    try:
        result: BulkUpdateResult = await process_master_scoping_bulk_update(db, ids, field, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result.to_dict()


@router.patch(
    "/master-scoping/{record_id}",
    tags=["Master Scoping"],
    summary="Update editable fields of a single master scoping record",
)
async def patch_master_scoping(
    record_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rec = await db.get(PortfolioCompanyMetadata, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Master scoping record {record_id} not found")

    if not payload:
        raise HTTPException(status_code=422, detail="No fields to update")

    affects_aggregates = False
    field_changes: dict = {}
    for field, raw in payload.items():
        meta = _EDITABLE_FIELD_TYPES.get(field)
        if meta is None:
            raise HTTPException(status_code=422, detail=f"Field '{field}' is not editable")
        label, col_type = meta
        parsed, err = _parse_cell(raw, field, label, col_type, 0)
        if err is not None:
            raise HTTPException(status_code=422, detail=err.reason)
        current = getattr(rec, field, None)
        current_s = "" if current is None else str(current).strip()
        new_s = "" if parsed is None else str(parsed).strip()
        if current_s != new_s:
            field_changes[field] = (current, parsed)
        setattr(rec, field, parsed)
        if field in ("cost", "fmv"):
            affects_aggregates = True

    if affects_aggregates and rec.review_cycle_id:
        await db.flush()
        await recompute_aggregates(db, rec.review_cycle_id)

    actor = get_audit_actor_email() or SYSTEM_ACTOR
    recorder = CompanyAuditRecorder(db)

    if field_changes:
        pc_stmt = select(PortfolioCompany.id).where(
            PortfolioCompany.company_id == rec.deal_id,
            PortfolioCompany.review_cycle_id == rec.review_cycle_id,
        )
        pc_id = (await db.execute(pc_stmt)).scalars().first()
        if pc_id:
            await recorder.log_company_field_changes(
                portfolio_company_id=pc_id,
                changes=field_changes,
                entity_type="portfolio_company_metadata",
                actor_email=actor,
            )

    # When scoping_for_audit flips False → True via inline edit, advance all
    # entities for this company/cycle from "Not applicable" → "Financials to be received",
    # matching the same transition that the XLSX upload path performs.
    scoping_change = field_changes.get("scoping_for_audit")
    if scoping_change is not None:
        old_scoping, new_scoping = scoping_change
        if new_scoping is True and not old_scoping:
            pc_ids_stmt = select(PortfolioCompany.id).where(
                PortfolioCompany.company_id == rec.deal_id,
            )
            if rec.review_cycle_id:
                pc_ids_stmt = pc_ids_stmt.where(
                    PortfolioCompany.review_cycle_id == rec.review_cycle_id
                )
            pc_ids = (await db.execute(pc_ids_stmt)).scalars().all()
            if pc_ids:
                ent_stmt = select(Entity).where(
                    Entity.portfolio_company_id.in_(pc_ids),
                    or_(
                        Entity.status == CompanyReviewStage.NOT_APPLICABLE.value,
                        Entity.status.is_(None),
                    ),
                )
                for ent in (await db.execute(ent_stmt)).scalars().all():
                    before_status = ent.status
                    ent.status = CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value
                    for pc_id in pc_ids:
                        if ent.portfolio_company_id == pc_id:
                            await recorder.log_company_field_changes(
                                portfolio_company_id=pc_id,
                                changes={"status": (before_status, CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value)},
                                entity_type="entity",
                                entity_id=ent.id,
                                actor_email=actor,
                            )
                            break

    await db.commit()
    await db.refresh(rec)
    return _serialize_metadata(rec)


def _serialize_metadata(rec: PortfolioCompanyMetadata) -> dict:
    return {
        "id": rec.id,
        "fund": rec.fund,
        "deal_id": rec.deal_id,
        "deal_id_for_analysis": rec.deal_id_for_analysis,
        "deal_id_for_analysis_and_strategy": rec.deal_id_for_analysis_and_strategy,
        "deal_name": rec.deal_name,
        "strategy": rec.strategy,
        "il_main": rec.il_main,
        "sector_l1": rec.sector_l1,
        "sector_l2": rec.sector_l2,
        "geo_l1": rec.geo_l1,
        "geo_l2": rec.geo_l2,
        "cost": str(rec.cost) if rec.cost is not None else None,
        "distributed": str(rec.distributed) if rec.distributed is not None else None,
        "proceeds": str(rec.proceeds) if rec.proceeds is not None else None,
        "fmv": str(rec.fmv) if rec.fmv is not None else None,
        "ownership": str(rec.ownership) if rec.ownership is not None else None,
        "scoping_for_audit": rec.scoping_for_audit,
        "reason_for_exclusion": rec.reason_for_exclusion,
        "category": rec.category,
        "unique_by_company_id": int(rec.unique_by_company_id) if rec.unique_by_company_id is not None else None,
        "unique_by_company_id_strategy": int(rec.unique_by_company_id_strategy) if rec.unique_by_company_id_strategy is not None else None,
        "consolidated_cost": str(rec.consolidated_cost) if rec.consolidated_cost is not None else None,
        "consolidated_fmv": str(rec.consolidated_fmv) if rec.consolidated_fmv is not None else None,
        "deal_level_stage_1": rec.deal_level_stage_1,
        "deal_level_stage_2": rec.deal_level_stage_2,
        "tentative_audit_completion_date": rec.tentative_audit_completion_date,
        "fy_end": rec.fy_end,
        "auditor": rec.auditor,
        "category_of_auditor": rec.category_of_auditor,
        "py_audit_status": rec.py_audit_status,
        "review_cycle_id": rec.review_cycle_id,
        "comments": rec.comments,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }
