from __future__ import annotations

from typing import Literal, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.common import Page
from src.schema.sync_alerts import (
    SyncAlertAcknowledgeAllResult,
    SyncAlertRead,
    SyncAlertUnreadCount,
)
from src.services.sync_alert_service import SyncAlertService

router = APIRouter()


def _user_email(request: Request) -> str:
    claims = getattr(request.state, "jwt_claims", {}) or {}
    email = claims.get("email") or claims.get("sub") or ""
    return str(email)


def _pagination(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Tuple[int, int]:
    return limit, offset


@router.get("", response_model=Page[SyncAlertRead])
async def list_sync_alerts(
    request: Request,
    status: Literal["all", "unread", "read"] = Query("all"),
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await SyncAlertService(db).list_alerts(
        status=status, limit=limit, offset=offset
    )
    return Page(items=items, total=total)


@router.get("/unread-count", response_model=SyncAlertUnreadCount)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
):
    count = await SyncAlertService(db).get_unread_count()
    return SyncAlertUnreadCount(count=count)


@router.post("/{alert_id}/acknowledge", response_model=SyncAlertRead)
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = _user_email(request)
    try:
        notification = await SyncAlertService(db).acknowledge(alert_id, user_email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    await db.commit()
    await db.refresh(notification)
    return notification


@router.post("/acknowledge-all", response_model=SyncAlertAcknowledgeAllResult)
async def acknowledge_all_alerts(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = _user_email(request)
    count = await SyncAlertService(db).acknowledge_all(user_email)
    await db.commit()
    return SyncAlertAcknowledgeAllResult(acknowledged_count=count)
