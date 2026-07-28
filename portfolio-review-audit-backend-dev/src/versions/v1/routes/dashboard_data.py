"""
Dashboard data download / upload endpoints.

GET  /api/v1/dashboard-data/download?review_cycle_id=<id>
     → XLSX file (one row per entity per company for the given cycle)

POST /api/v1/dashboard-data/upload?review_cycle_id=<id>  (optional, for validation)
     → JSON summary {rows_processed, rows_updated, rows_skipped, errors}
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File as FastAPIFile, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.services.dashboard_data_export import (
    UploadResult,
    build_dashboard_xlsx,
    process_dashboard_upload,
)

router = APIRouter()


@router.get(
    "/dashboard-data/download",
    tags=["Dashboard Data"],
    summary="Download dashboard table as XLSX for a specific review cycle",
)
async def download_dashboard_data(
    review_cycle_id: str = Query(..., description="Review cycle ID to export"),
    scoped_in: Optional[bool] = Query(
        default=None,
        description="Filter by scoping: true = scoped-in companies only, false = scoped-out only, omit = all",
    ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not review_cycle_id or not review_cycle_id.strip():
        raise HTTPException(status_code=422, detail="review_cycle_id is required")

    try:
        xlsx_bytes, filename = await build_dashboard_xlsx(db, review_cycle_id.strip(), scoped_in=scoped_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/dashboard-data/upload",
    tags=["Dashboard Data"],
    summary="Upload edited dashboard XLSX and update existing records",
)
async def upload_dashboard_data(
    review_cycle_id: Optional[str] = Query(
        default=None,
        description="Expected review cycle ID (optional, validated against file contents)",
    ),
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    content_type = (file.content_type or "").lower()
    valid_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/octet-stream",
    }
    filename_lower = (file.filename or "").lower()
    if content_type not in valid_types and not filename_lower.endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=422,
            detail="Uploaded file must be an Excel workbook (.xlsx)",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    expected_cycle = review_cycle_id.strip() if review_cycle_id else None

    try:
        result: UploadResult = await process_dashboard_upload(
            db,
            file_bytes,
            expected_review_cycle_id=expected_cycle,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.to_dict()
