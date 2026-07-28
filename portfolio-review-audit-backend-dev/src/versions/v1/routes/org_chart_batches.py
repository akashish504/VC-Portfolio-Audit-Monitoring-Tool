"""
API routes for bulk org-chart ZIP upload / mapping / extraction.

Endpoints (all under /api/v1 prefix added in main.py):
  POST   /org-chart-batches/upload
  GET    /org-chart-batches
  GET    /org-chart-batches/{batch_id}
  GET    /org-chart-batches/{batch_id}/mapping-template
  GET    /org-chart-batches/{batch_id}/status-report
  POST   /org-chart-batches/{batch_id}/mapping-upload
  GET    /org-chart-batches/{batch_id}/records
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.org_chart_batch import (
    BatchListResponse,
    BatchUploadResponse,
    MappingUploadResponse,
    OrgChartUploadBatchDetail,
    OrgChartUploadRecordRead,
)
from src.services.org_chart_batch_upload import (
    build_mapping_template,
    build_status_report,
    create_pending_batch,
    get_batch,
    get_batch_records,
    list_batches,
    process_batch_entries,
    process_mapping_upload,
    reset_record_for_retry,
    validate_zip,
)
from src.services.org_chart_extraction import run_org_chart_batch_record_extraction

logger = logging.getLogger(__name__)
router = APIRouter()

_ZIP_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
    "multipart/form-data",
}

_XLSX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
}

MAX_ZIP_BYTES = 500 * 1024 * 1024  # 500 MB ZIP ceiling


def _require_batch(batch):
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


# ---------------------------------------------------------------------------
# POST /org-chart-batches/upload
# ---------------------------------------------------------------------------

@router.post("/org-chart-batches/upload", response_model=BatchUploadResponse, tags=["OrgChartBatches"])
async def upload_org_chart_zip(
    background_tasks: BackgroundTasks,
    review_cycle_id: Optional[str] = Query(None, description="Review cycle ID to associate with this batch (can be set later via mapping upload)"),
    file: UploadFile = File(..., description="ZIP file containing org chart files"),
    db: AsyncSession = Depends(get_db),
):
    zip_bytes = await file.read()
    if not zip_bytes:
        raise HTTPException(status_code=422, detail="Uploaded ZIP file is empty")
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=422, detail=f"ZIP file too large (max {MAX_ZIP_BYTES // 1024 // 1024} MB)")

    # Validate ZIP contents synchronously — fast, no I/O, raises 422 on bad input.
    try:
        entries = validate_zip(zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Create the batch row in 'processing' status and return immediately.
    try:
        batch = await create_pending_batch(
            db=db,
            review_cycle_id=review_cycle_id,
            zip_filename=file.filename or "upload.zip",
            file_count=len(entries),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # S3 uploads + File/Record rows run in the background — no request timeout risk.
    background_tasks.add_task(
        process_batch_entries,
        batch.id,
        review_cycle_id,
        entries,
    )

    return BatchUploadResponse(
        batch=batch,  # type: ignore[arg-type]
        records=[],   # no records yet — frontend polls GET /org-chart-batches/{batch_id}
    )


# ---------------------------------------------------------------------------
# GET /org-chart-batches
# ---------------------------------------------------------------------------

@router.get("/org-chart-batches", response_model=BatchListResponse, tags=["OrgChartBatches"])
async def list_org_chart_batches(
    review_cycle_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_batches(db, review_cycle_id=review_cycle_id, limit=limit, offset=offset)
    return BatchListResponse(items=items, total=total)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GET /org-chart-batches/{batch_id}
# ---------------------------------------------------------------------------

@router.get("/org-chart-batches/{batch_id}", response_model=OrgChartUploadBatchDetail, tags=["OrgChartBatches"])
async def get_org_chart_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    batch = _require_batch(await get_batch(db, batch_id))
    records = await get_batch_records(db, batch_id)
    return OrgChartUploadBatchDetail.model_validate(
        {**batch.__dict__, "records": records[:6]}
    )


# ---------------------------------------------------------------------------
# GET /org-chart-batches/{batch_id}/records
# ---------------------------------------------------------------------------

@router.get("/org-chart-batches/{batch_id}/records", response_model=list[OrgChartUploadRecordRead], tags=["OrgChartBatches"])
async def get_org_chart_batch_records(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    _require_batch(await get_batch(db, batch_id))
    return await get_batch_records(db, batch_id)


# ---------------------------------------------------------------------------
# GET /org-chart-batches/{batch_id}/mapping-template
# ---------------------------------------------------------------------------

@router.get("/org-chart-batches/{batch_id}/mapping-template", tags=["OrgChartBatches"])
async def download_mapping_template(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    _require_batch(await get_batch(db, batch_id))
    records = await get_batch_records(db, batch_id)
    xlsx_bytes = build_mapping_template(records)
    filename = f"org_chart_mapping_batch_{batch_id}.xlsx"
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /org-chart-batches/{batch_id}/status-report
# ---------------------------------------------------------------------------

@router.get("/org-chart-batches/{batch_id}/status-report", tags=["OrgChartBatches"])
async def download_status_report(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
):
    _require_batch(await get_batch(db, batch_id))
    records = await get_batch_records(db, batch_id)
    xlsx_bytes = build_status_report(records)
    filename = f"org_chart_status_report_batch_{batch_id}.xlsx"
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /org-chart-batches/{batch_id}/mapping-upload
# ---------------------------------------------------------------------------

async def _run_extractions_background(record_ids: list[int]) -> None:
    """Fire-and-forget LLM extraction for all resolved records."""
    from src.db.session import AsyncSessionLocal

    for record_id in record_ids:
        try:
            async with AsyncSessionLocal() as session:
                await run_org_chart_batch_record_extraction(session, record_id)
        except Exception:  # noqa: BLE001
            logger.exception("Background extraction failed for record_id=%s", record_id)


@router.post("/org-chart-batches/{batch_id}/mapping-upload", response_model=MappingUploadResponse, tags=["OrgChartBatches"])
async def upload_mapping_xlsx(
    batch_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Filled mapping XLSX"),
    review_cycle_id: Optional[str] = Query(None, description="Override review cycle for company resolution (defaults to batch cycle)"),
    db: AsyncSession = Depends(get_db),
):
    batch = _require_batch(await get_batch(db, batch_id))

    if batch.mapping_uploaded_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Mapping has already been uploaded for this batch. Create a new batch to upload a different mapping.",
        )

    xlsx_bytes = await file.read()
    if not xlsx_bytes:
        raise HTTPException(status_code=422, detail="Uploaded XLSX file is empty")

    try:
        result, mapped_ids = await process_mapping_upload(db, batch, xlsx_bytes, review_cycle_id=review_cycle_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if mapped_ids:
        background_tasks.add_task(_run_extractions_background, mapped_ids)

    return MappingUploadResponse(
        rows_processed=result.rows_processed,
        rows_mapped=result.rows_mapped,
        rows_skipped=result.rows_skipped,
        errors=result.errors,
        extraction_queued=len(mapped_ids),
    )


# ---------------------------------------------------------------------------
# POST /org-chart-records/{record_id}/retry-extraction
# ---------------------------------------------------------------------------

@router.post("/org-chart-records/{record_id}/retry-extraction", tags=["OrgChartBatches"])
async def retry_org_chart_record_extraction(
    record_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Re-queue extraction for a failed or stuck OrgChartUploadRecord.

    Allowed from status: ``failed``, ``mapped``, ``processing``.
    Resets the record to ``mapped`` and fires extraction in the background,
    which on success sets ``extraction_status=completed`` and
    ``reconciliation_status=pending`` so the pending-update dialog activates.
    """
    try:
        record = await reset_record_for_retry(db, record_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(_run_extractions_background, [record.id])
    return {"record_id": record.id, "status": "queued"}
