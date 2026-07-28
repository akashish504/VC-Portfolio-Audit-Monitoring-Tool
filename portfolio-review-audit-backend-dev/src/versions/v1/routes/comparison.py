"""Comparison / analysis stubs — v1 (merged from portfolio-review-audit-backend-main)."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status

logger = logging.getLogger(__name__)
router = APIRouter()

_NOT_IMPLEMENTED = (
    "Financial comparison runs are not implemented for the current portfolio data model. "
    "Use `financial_data` and related tables directly, or wait for a dedicated comparison API."
)


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED, tags=["Analysis runs"])
async def create_analysis_run(request: Request):
    del request
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("", status_code=status.HTTP_501_NOT_IMPLEMENTED, tags=["Analysis runs"])
async def list_analysis_runs(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    del request, status_filter, page, page_size
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/{run_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED, tags=["Analysis runs"])
async def get_analysis_run(run_id: int, request: Request):
    del run_id, request
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
