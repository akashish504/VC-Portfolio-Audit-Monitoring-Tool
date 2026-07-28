"""REST `/api/v1/users/*` (merged). SPA bootstrap stays on `/api/v1/user/get-user`."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.user_profile import UserProfileResponse, UserProfileUpdate
from src.services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter()


def _claims(request: Request) -> dict:
    return getattr(request.state, "jwt_claims", {})


@router.get("/me", response_model=UserProfileResponse, tags=["User profiles"])
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    claims = _claims(request)
    try:
        return await UserService(db).get_or_create_user(claims)
    except ValueError as e:
        logger.info("get_or_create_user validation: %s", e)
        raise HTTPException(status_code=400, detail="Bad request") from e


@router.get("/list", response_model=list[UserProfileResponse], tags=["User profiles"])
async def list_users(skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db)):
    return await UserService(db).list_users(skip=skip, limit=limit)


@router.get("/{user_id}", response_model=UserProfileResponse, tags=["User profiles"])
async def get_user_by_uuid(user_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await UserService(db).get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserProfileResponse, tags=["User profiles"])
async def update_user_profile(
    user_id: UUID, data: UserProfileUpdate, db: AsyncSession = Depends(get_db)
):
    payload = data.model_dump(exclude_unset=True)
    user = await UserService(db).update_user(user_id, payload)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
