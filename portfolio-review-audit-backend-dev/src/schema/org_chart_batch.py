"""Pydantic schemas for the org chart batch upload feature."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrgChartUploadRecordRead(_ORMBase):
    id: int
    batch_id: int
    review_cycle_id: Optional[str] = None
    file_id: Optional[int] = None
    file_name: str
    storage_uri: Optional[str] = None
    company_id: Optional[str] = None
    name: Optional[str] = None
    portfolio_company_id: Optional[int] = None
    extracted_org_chart: Optional[Any] = None
    extraction_status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class OrgChartUploadBatchRead(_ORMBase):
    id: int
    review_cycle_id: Optional[str] = None
    original_zip_filename: str
    status: str
    file_count: int
    mapping_uploaded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class OrgChartUploadBatchDetail(OrgChartUploadBatchRead):
    records: list[OrgChartUploadRecordRead] = []


class BatchUploadResponse(BaseModel):
    """
    Returned immediately after ZIP upload.
    batch.status will be 'processing' until the background task completes,
    at which point it becomes 'uploaded' (success) or 'failed'.
    Poll GET /org-chart-batches/{batch_id} until status != 'processing'.
    records is empty on initial response — populated once processing completes.
    """
    batch: OrgChartUploadBatchRead
    records: list[OrgChartUploadRecordRead]


class BatchListResponse(BaseModel):
    items: list[OrgChartUploadBatchRead]
    total: int


class MappingUploadResponse(BaseModel):
    rows_processed: int
    rows_mapped: int
    rows_skipped: int
    errors: list[dict[str, Any]]
    extraction_queued: int
