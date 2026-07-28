from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DraftEmailGenerateRequest(BaseModel):
    portfolio_company_id: int = Field(..., ge=1)
    template_name: Optional[str] = None
    overwrite: bool = False


class DraftEmailUpdateRequest(BaseModel):
    subject: Optional[str] = None
    to_add: Optional[list[str]] = None
    cc: Optional[list[str]] = None
    email_body: Optional[str] = None
    attachments: Optional[list[str]] = None
    attachments_id: Optional[str] = None


class DraftEmailRead(BaseModel):
    id: str
    portfolio_company_id: Optional[int] = None
    subject: Optional[str] = None
    to_add: Optional[list[str]] = None
    cc: Optional[list[str]] = None
    email_body: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    attachments: Optional[list[str]] = None
    attachments_id: Optional[str] = None
    affected_entity_ids: Optional[list[int]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DraftEmailSendResponse(BaseModel):
    queued: bool = True
    draft_id: str

