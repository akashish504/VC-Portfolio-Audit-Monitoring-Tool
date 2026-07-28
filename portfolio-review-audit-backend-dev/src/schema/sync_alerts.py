from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class SyncAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: str
    company_name: str
    investment_stage: Optional[str] = None
    message: str
    is_read: bool
    read_at: Optional[datetime] = None
    acknowledged_by_user_email: Optional[str] = None
    created_at: datetime


class SyncAlertUnreadCount(BaseModel):
    count: int


class SyncAlertAcknowledgeAllResult(BaseModel):
    acknowledged_count: int
