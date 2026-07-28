"""User service — public.users (merged from portfolio-review-audit-backend-main)."""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_user(self, claims: dict) -> User:
        okta_sub = claims.get("sub") or claims.get("okta_id")
        email = claims.get("email")
        if not okta_sub or not email:
            raise ValueError("JWT must include sub and email for user provisioning")

        existing = (
            await self.db.execute(select(User).where(User.okta_id == str(okta_sub)))
        ).scalar_one_or_none()
        if existing:
            return existing

        name = claims.get("name") or claims.get("full_name")
        user = User(
            okta_id=str(okta_sub),
            email=str(email),
            full_name=name,
            is_active=True,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        logger.info("Created user id=%s email=%s", user.id, user.email)
        return user

    async def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        return (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    async def list_users(self, skip: int = 0, limit: int = 50) -> list:
        q = select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
        return list((await self.db.execute(q)).scalars().all())

    async def update_user(self, user_id: UUID, data: dict) -> Optional[User]:
        user = await self.get_user_by_id(user_id)
        if not user:
            return None
        if "full_name" in data and data["full_name"] is not None:
            user.full_name = data["full_name"]
        if "is_active" in data and data["is_active"] is not None:
            user.is_active = data["is_active"]
        await self.db.commit()
        await self.db.refresh(user)
        return user
