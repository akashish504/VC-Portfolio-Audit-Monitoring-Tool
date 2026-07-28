from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi_csrf_protect import CsrfProtect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DataSyncProcessBatch
from src.db.session import get_db


router = APIRouter(prefix="/user")


@router.get("/get-user")
async def get_user(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Session bootstrap endpoint for the SPA.

    - Requires Okta Bearer token (handled by JWT middleware).
    - Issues a CSRF cookie and returns the CSRF token in the body so the SPA can
      send it on subsequent write requests.
    - Includes batch_process_status from the latest row on data_sync_process_batch
      (same contract as portfolio-review-app-api-develop get-user).
    """
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    # Identity must come from verified JWT claims only — never from client headers.
    claims = getattr(request.state, "jwt_claims", {}) or {}
    soha_user = claims.get("email") or claims.get("sub") or ""

    batch_process_status = "SUCCESS"
    try:
        result = await db.execute(
            select(DataSyncProcessBatch)
            .order_by(DataSyncProcessBatch.created_at.desc())
            .limit(1)
        )
        latest = result.scalars().first()
        if latest is not None:
            batch_process_status = latest.status
    except Exception:
        batch_process_status = "SUCCESS"

    response = JSONResponse(
        {
            "success": True,
            "data": {
                "soha_user": soha_user,
                "csrf_token": csrf_token,
                "batch_process_status": batch_process_status,
            },
            "message": None,
        }
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response

