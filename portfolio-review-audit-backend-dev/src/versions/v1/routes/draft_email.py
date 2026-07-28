from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionLocal
from src.db.session import get_db
from src.schema.draft_email import (
    DraftEmailGenerateRequest,
    DraftEmailRead,
    DraftEmailSendResponse,
    DraftEmailUpdateRequest,
)
from src.services.discrepancy_send_post_process import (
    apply_discrepancy_send_post_process,
    assert_can_send_discrepancy_draft,
    check_duplicate_discrepancy_send,
    is_discrepancy_template_id,
)
from src.services.draft_email import DraftEmailService
from src.services.email_template_config import ConfigurationError, get_discrepancy_template_id
from src.services.email_threads import EmailThreadsService


router = APIRouter()


@router.get("/email/drafts", response_model=Optional[DraftEmailRead])
async def get_company_draft_email(
    portfolio_company_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    svc = DraftEmailService(db)
    row = await svc.get_for_company(portfolio_company_id=portfolio_company_id)
    if not row:
        return None
    return await svc.draft_to_read(row)


@router.post("/email/drafts/generate", response_model=DraftEmailRead)
async def generate_company_draft_email(payload: DraftEmailGenerateRequest, db: AsyncSession = Depends(get_db)):
    svc = DraftEmailService(db)
    try:
        return await svc.generate_for_company(
            portfolio_company_id=payload.portfolio_company_id,
            template_name=payload.template_name,
            overwrite=payload.overwrite,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})


@router.put("/email/drafts/{draft_id}", response_model=DraftEmailRead)
async def update_company_draft_email(draft_id: str, payload: DraftEmailUpdateRequest, db: AsyncSession = Depends(get_db)):
    svc = DraftEmailService(db)
    try:
        return await svc.update_draft(
            draft_id=draft_id,
            subject=payload.subject,
            to_add=payload.to_add,
            cc=payload.cc,
            email_body=payload.email_body,
            attachments=payload.attachments,
            attachments_id=payload.attachments_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})


async def _send_draft_email_job(*, draft_id: str) -> None:
    async with AsyncSessionLocal() as db:
        draft_svc = DraftEmailService(db)
        draft = await draft_svc.get_by_id(draft_id=draft_id)
        if not draft:
            return
        if not draft.portfolio_company_id:
            return
        if not (draft.subject and draft.email_body):
            return
        if not draft.to_add:
            logger.error("Draft %s has no recipients; skipping send.", draft_id)
            return

        pc_id = int(draft.portfolio_company_id)
        template_id = draft.template_id

        # Use the snapshot stored on the draft at generation time — do not recompute.
        affected_entity_ids: list[int] = list(draft.affected_entity_ids or [])

        # For discrepancy emails: validate the snapshot is present and run duplicate check
        if await is_discrepancy_template_id(db, template_id):
            if not affected_entity_ids:
                logger.error(
                    "Discrepancy draft %s has no affected_entity_ids snapshot; "
                    "regenerate the draft before sending.",
                    draft_id,
                )
                return
            try:
                configured_disc_id = await get_discrepancy_template_id(db)
            except ConfigurationError:
                configured_disc_id = template_id
            await check_duplicate_discrepancy_send(
                db,
                portfolio_company_id=pc_id,
                discrepancy_template_id=configured_disc_id,
                newly_affected_entity_ids=set(affected_entity_ids),
            )

        svc = EmailThreadsService(db)
        _tid, message_id = await svc.send_email(
            portfolio_company_id=pc_id,
            to_addrs=list(draft.to_add or []),
            cc_addrs=list(draft.cc or []),
            subject=str(draft.subject),
            body_html=str(draft.email_body),
            thread_id=None,
            reply_to_message_id=None,
            attachment_keys=list(draft.attachments or []),
            attachments_id=(draft.attachments_id or None),
            template_id=template_id,
            affected_entity_ids=affected_entity_ids or None,
        )
        await apply_discrepancy_send_post_process(
            db,
            portfolio_company_id=pc_id,
            template_id=template_id,
            affected_entity_ids=affected_entity_ids or None,
            email_history_id=message_id,
        )


@router.post("/email/drafts/{draft_id}/send", response_model=DraftEmailSendResponse)
async def send_company_draft_email(
    draft_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    svc = DraftEmailService(db)
    draft = await svc.get_by_id(draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail={"message": "Draft not found"})
    if not draft.portfolio_company_id:
        raise HTTPException(status_code=400, detail={"message": "Draft missing portfolio_company_id"})
    if not (draft.subject and draft.email_body):
        raise HTTPException(status_code=400, detail={"message": "Draft missing subject/body"})
    if not draft.to_add:
        raise HTTPException(status_code=400, detail={"message": "Draft has no recipients (To). Add at least one email address before sending."})

    try:
        await assert_can_send_discrepancy_draft(db, draft)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})

    background.add_task(_send_draft_email_job, draft_id=draft_id)
    return DraftEmailSendResponse(queued=True, draft_id=draft_id)

