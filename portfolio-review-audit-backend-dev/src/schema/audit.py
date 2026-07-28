"""Pydantic schemas for audit file routes (merged from portfolio-review-audit-backend-main)."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class UploadUrlRequest(BaseModel):
    company_id: int = Field(..., description="portfolio_companies.id (FK)")
    file_name: str = Field(..., min_length=1, max_length=255)
    mime_type: Optional[str] = Field(None)
    checksum_sha256: Optional[str] = Field(None, description="Client SHA-256 hex for integrity check")


class ConfirmUploadRequest(BaseModel):
    size_bytes: Optional[int] = Field(None, ge=1)
    page_count: Optional[int] = Field(None, ge=1)
    language: Optional[str] = Field(None, max_length=50)


class FileLinkRequest(BaseModel):
    entity_type: str = Field(..., description="entity | review_cycle | discrepancy | email_message")
    entity_id: int = Field(..., ge=1)


class UploadUrlResponse(BaseModel):
    """After registering a pending file, upload bytes only via POST /api/v1/files/{file_id}/upload."""

    file_id: int
    s3_key: str
    model_config = {"from_attributes": True}


class ConfirmUploadResponse(BaseModel):
    file_id: int
    upload_status: str
    model_config = {"from_attributes": True}


class AuditFileResponse(BaseModel):
    id: int
    portfolio_company_id: int
    filename: str
    content_type: Optional[str] = None
    storage_uri: Optional[str] = None
    size_bytes: Optional[int] = None
    status: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    entity_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AuditFileListResponse(BaseModel):
    items: List[AuditFileResponse]
    total: int
    page: int
    page_size: int
    pages: int
    model_config = {"from_attributes": True}


class FileLinkResponse(BaseModel):
    file_id: int
    entity_type: str
    entity_id: int
    status: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    model_config = {"from_attributes": True}
