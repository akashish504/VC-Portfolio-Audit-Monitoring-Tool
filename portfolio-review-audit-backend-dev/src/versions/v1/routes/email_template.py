from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.email_template import (
    EmailTemplateActivateRequest,
    EmailTemplateCreate,
    EmailTemplateHistoryRead,
    EmailTemplateRead,
    EmailTemplateUpdate,
)
from src.services.email_template import EmailTemplateService

router = APIRouter()


@router.get("/email/templates", response_model=list[EmailTemplateRead])
async def list_active_email_templates(db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    return await svc.list_active()


@router.get("/email/templates/list", response_model=list[EmailTemplateRead])
async def list_all_email_template_versions(db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    return await svc.list_all_versions()


@router.get("/email/templates/active", response_model=Optional[EmailTemplateRead])
async def get_active_email_template(request: Request, db: AsyncSession = Depends(get_db)):
    template_name = request.query_params.get("template_name")
    if not template_name:
        raise HTTPException(status_code=400, detail={"message": "template_name is required"})
    svc = EmailTemplateService(db)
    return await svc.get_active(template_name)


@router.post("/email/templates", response_model=EmailTemplateRead, status_code=201)
async def create_email_template(payload: EmailTemplateCreate, db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    try:
        return await svc.create(
            template_name=payload.template_name,
            subject=payload.subject,
            body=payload.body,
            version_name=payload.version_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})


@router.post("/email/templates/activate")
async def activate_email_template(payload: EmailTemplateActivateRequest, db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    try:
        await svc.activate(template_name=payload.template_name, version_id=payload.version_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})


@router.put("/email/templates/{template_id}", response_model=EmailTemplateRead)
async def update_email_template_version(template_id: str, payload: EmailTemplateUpdate, db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    try:
        return await svc.update_version(
            template_id=template_id,
            subject=payload.subject,
            body=payload.body,
            version_name=payload.version_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})


@router.get("/email/templates/{template_id}/history", response_model=list[EmailTemplateHistoryRead])
async def list_email_template_history(template_id: str, db: AsyncSession = Depends(get_db)):
    svc = EmailTemplateService(db)
    return await svc.list_history_for_template(template_id=template_id)

