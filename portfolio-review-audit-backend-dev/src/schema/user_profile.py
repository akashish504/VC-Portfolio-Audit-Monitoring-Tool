"""User Pydantic schemas for `/api/v1/users/*` (merged from portfolio-review-audit-backend-main)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserProfileResponse(BaseModel):
    id: UUID
    okta_id: str
    email: EmailStr
    full_name: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
