from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CompanySyncNotification


class SyncAlertService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_alerts(
        self,
        status: Literal["all", "unread", "read"] = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[CompanySyncNotification], int]:
        q = (
            select(CompanySyncNotification)
            .order_by(CompanySyncNotification.created_at.desc())
        )
        count_q = select(func.count()).select_from(CompanySyncNotification)

        if status == "unread":
            q = q.where(CompanySyncNotification.is_read == False)  # noqa: E712
            count_q = count_q.where(CompanySyncNotification.is_read == False)  # noqa: E712
        elif status == "read":
            q = q.where(CompanySyncNotification.is_read == True)  # noqa: E712
            count_q = count_q.where(CompanySyncNotification.is_read == True)  # noqa: E712

        total = (await self.db.execute(count_q)).scalar_one()
        items = (await self.db.execute(q.offset(offset).limit(limit))).scalars().all()
        return list(items), total

    async def get_unread_count(self) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(CompanySyncNotification)
            .where(CompanySyncNotification.is_read == False)  # noqa: E712
        )
        return result.scalar_one()

    async def acknowledge(self, alert_id: int, user_email: str) -> CompanySyncNotification:
        notification = (
            await self.db.execute(
                select(CompanySyncNotification)
                .where(CompanySyncNotification.id == alert_id)
            )
        ).scalar_one_or_none()

        if notification is None:
            raise ValueError(f"Notification {alert_id} not found")

        if notification.is_read:
            return notification

        notification.is_read = True
        notification.read_at = datetime.now(tz=timezone.utc)
        notification.acknowledged_by_user_email = user_email
        await self.db.flush()
        await self.db.refresh(notification)
        return notification

    async def acknowledge_all(self, user_email: str) -> int:
        now = datetime.now(tz=timezone.utc)
        result = await self.db.execute(
            update(CompanySyncNotification)
            .where(CompanySyncNotification.is_read == False)  # noqa: E712
            .values(
                is_read=True,
                read_at=now,
                acknowledged_by_user_email=user_email,
            )
            .returning(CompanySyncNotification.id)
        )
        return len(result.fetchall())
