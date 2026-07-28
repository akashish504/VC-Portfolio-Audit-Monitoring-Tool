"""
Async XLSX audit-report export endpoints.

POST   /api/v1/export/audit-report          — start a job
GET    /api/v1/export/audit-report/{job_id} — poll status
GET    /api/v1/export/audit-report/{job_id}/download — download XLSX
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExportJob
from src.db.session import get_db
from src.services.audit_report_export import (
    REPORT_TYPE_EXTRACTED_FINANCIALS,
    REPORT_TYPE_RECONCILIATION,
    SUPPORTED_OUTPUT_CURRENCIES,
    SUPPORTED_REPORT_TYPES,
    _REPORT_TYPE_TO_JOB_TYPE,
    run_export_job,
)

router = APIRouter()


class StartExportRequest(BaseModel):
    review_cycle_id: Optional[str] = None  # None = all cycles
    report_type: str = Field(..., description="reconciliation | extracted_financials")
    output_currency: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=8,
        description="Required when report_type == 'extracted_financials'; rejected otherwise.",
    )

    @field_validator("report_type")
    @classmethod
    def _validate_report_type(cls, v: str) -> str:
        normalized = (v or "").strip().lower()
        if normalized not in SUPPORTED_REPORT_TYPES:
            raise ValueError(
                f"report_type must be one of {SUPPORTED_REPORT_TYPES}"
            )
        return normalized

    @field_validator("output_currency")
    @classmethod
    def _validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip().upper()
        if not normalized:
            return None
        if normalized not in SUPPORTED_OUTPUT_CURRENCIES:
            raise ValueError(
                f"output_currency must be one of {SUPPORTED_OUTPUT_CURRENCIES}"
            )
        return normalized

    @model_validator(mode="after")
    def _enforce_currency_contract(self) -> "StartExportRequest":
        if self.report_type == REPORT_TYPE_EXTRACTED_FINANCIALS and not self.output_currency:
            raise ValueError(
                "output_currency is required for report_type='extracted_financials'"
            )
        return self


class ExportJobStatus(BaseModel):
    job_id: str
    status: str
    report_type: Optional[str] = None
    output_currency: Optional[str] = None
    filename: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.post(
    "/export/audit-report",
    response_model=ExportJobStatus,
    tags=["Export"],
    summary="Start async XLSX audit-report export job",
)
async def start_audit_report_export(
    payload: StartExportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ExportJobStatus:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    job = ExportJob(
        id=job_id,
        job_type=_REPORT_TYPE_TO_JOB_TYPE[payload.report_type],
        status="pending",
        review_cycle_id=payload.review_cycle_id or None,
        output_currency=payload.output_currency,
        meta={},
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()

    background_tasks.add_task(run_export_job, job_id)

    return ExportJobStatus(
        job_id=job_id,
        status="pending",
        report_type=payload.report_type,
        output_currency=payload.output_currency,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )


@router.get(
    "/export/audit-report/{job_id}",
    response_model=ExportJobStatus,
    tags=["Export"],
    summary="Poll audit-report export job status",
)
async def get_export_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExportJobStatus:
    job = (
        await db.execute(select(ExportJob).where(ExportJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    # Map persisted job_type back to the public report_type string.
    job_type_to_report_type = {v: k for k, v in _REPORT_TYPE_TO_JOB_TYPE.items()}
    return ExportJobStatus(
        job_id=job.id,
        status=job.status,
        report_type=job_type_to_report_type.get(job.job_type),
        output_currency=job.output_currency,
        filename=job.filename,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
    )


@router.get(
    "/export/audit-report/{job_id}/download",
    tags=["Export"],
    summary="Download the generated XLSX for a completed export job",
)
async def download_export_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = (
        await db.execute(select(ExportJob).where(ExportJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Export job is not ready (status={job.status})",
        )

    xlsx_b64 = (job.meta or {}).get("xlsx_b64")
    if not xlsx_b64:
        raise HTTPException(status_code=500, detail="Export file data missing")

    xlsx_bytes = base64.b64decode(xlsx_b64)
    filename = job.filename or f"audit_report_{job_id}.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
