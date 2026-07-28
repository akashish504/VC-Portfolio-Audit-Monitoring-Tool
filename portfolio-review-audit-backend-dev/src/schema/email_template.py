from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EmailTemplateCreate(BaseModel):
    template_name: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    version_name: Optional[str] = None


class EmailTemplateActivateRequest(BaseModel):
    template_name: str = Field(..., min_length=1)
    version_id: str = Field(..., min_length=1)


class EmailTemplateUpdate(BaseModel):
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    version_name: Optional[str] = None


class EmailTemplateRead(BaseModel):
    id: str
    template_name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    version_id: Optional[str] = None
    version_name: Optional[str] = None
    is_active: Optional[bool] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmailTemplateHistoryRead(BaseModel):
    id: int
    template_id: Optional[str] = None
    event: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True

