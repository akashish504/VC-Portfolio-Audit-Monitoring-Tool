from __future__ import annotations

import json
from typing import Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.email_threads import (
    AttachmentStorageProbe,
    AuditedFinancialsEmailRead,
    AuditedFinancialsFileRead,
    StorageCheckResponse,
    CompanyContactsRead,
    EmailMessageRead,
    EmailThreadRead,
    EmailThreadSummary,
    ReminderSendRequest,
    ReminderSendResponse,
    ReminderThreadSelectionRequired,
    SendEmailResponse,
    SuggestedEmailsRead,
    SuggestedEmailsWrite,
    TagThreadRequest,
    TagThreadResponse,
    TagWithEntityRequest,
    TagWithEntityResponse,
    ThreadCandidateRead,
)
from src.services.email_template_config import ConfigurationError
from src.services.email_threads import EmailThreadsService
from src.services.reminder_send import (
    DuplicateReminderError,
    ThreadSelectionRequired,
    send_reminder,
)


router = APIRouter()


@router.get("/email/company-contacts", response_model=CompanyContactsRead)
async def get_company_contacts(
    portfolio_company_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Return the stored poc_email_ids and poc_cc_email_ids for a company."""
    svc = EmailThreadsService(db)
    try:
        data = await svc.get_company_contacts(portfolio_company_id=portfolio_company_id)
        return CompanyContactsRead(**data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})


@router.get("/email/suggested-emails", response_model=SuggestedEmailsRead)
async def get_suggested_emails(db: AsyncSession = Depends(get_db)):
    """Return the global list of suggested email addresses for autocomplete."""
    svc = EmailThreadsService(db)
    emails = await svc.get_suggested_emails()
    return SuggestedEmailsRead(emails=emails)


@router.put("/email/suggested-emails", response_model=SuggestedEmailsRead)
async def put_suggested_emails(payload: SuggestedEmailsWrite, db: AsyncSession = Depends(get_db)):
    """Overwrite the global suggested-email list."""
    svc = EmailThreadsService(db)
    try:
        emails = await svc.put_suggested_emails(payload.emails)
        return SuggestedEmailsRead(emails=emails)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Failed to update suggested emails", "error": str(e)})


@router.get("/email/threads", response_model=list[EmailThreadRead])
async def list_email_threads(
    portfolio_company_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    svc = EmailThreadsService(db)
    rows = await svc.list_threads_for_company(portfolio_company_id=portfolio_company_id)
    # Convert EmailHistory ORM objects into EmailMessageRead
    out: list[EmailThreadRead] = []
    for t in rows:
        out.append(
            EmailThreadRead(
                thread_id=t["thread_id"],
                latest_sent_at=t["latest_sent_at"],
                subject=t.get("subject"),
                email_count=t["email_count"],
                emails=[EmailMessageRead.model_validate(e) for e in t["emails"]],
            )
        )
    return out


@router.post("/email/tag-with-entity", response_model=TagWithEntityResponse)
async def tag_email_with_entity(
    payload: TagWithEntityRequest,
    db: AsyncSession = Depends(get_db),
):
    """Attach a single audited-financials email to a portfolio company + optional entity and
    immediately trigger attachment extraction so files appear in the Files tab.

    Accepts explicit ``entity_id`` and ``review_cycle_id`` — no subject-parsing required,
    so it works for both conventional and non-conventional email subjects.
    """
    from src.schema.email_threads import AttachmentIngestResult as _AttIngestResult

    svc = EmailThreadsService(db)
    try:
        result = await svc.tag_email_with_entity(
            email_id=payload.email_id,
            portfolio_company_id=payload.portfolio_company_id,
            entity_id=payload.entity_id,
            review_cycle_id=payload.review_cycle_id,
            force_reprocess=payload.force_reprocess,
        )
        n = result["attachments_processed"]
        raw_results = result.get("attachment_results", [])
        att_results = [_AttIngestResult(**r) for r in raw_results]
        failed = [r for r in att_results if r.status == "failed"]
        skipped = [r for r in att_results if r.status == "skipped"]
        if failed:
            msg = f"Tagged. {n} processed, {len(failed)} failed, {len(skipped)} skipped."
        elif skipped:
            msg = f"Tagged. {n} processed, {len(skipped)} skipped (already extracted)."
        else:
            msg = f"Tagged successfully. {n} attachment(s) processed."
        return TagWithEntityResponse(
            success=True,
            email_id=payload.email_id,
            portfolio_company_id=payload.portfolio_company_id,
            entity_id=payload.entity_id,
            review_cycle_id=result.get("review_cycle_id"),
            attachments_processed=n,
            attachment_results=att_results,
            message=msg,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Failed to tag email", "error": str(e)})


@router.get("/email/audited-financials", response_model=list[AuditedFinancialsEmailRead])
async def list_audited_financials_emails(db: AsyncSession = Depends(get_db)):
    """Return all emails classified (by system or user) as carrying audited financials,
    along with linked file records for the associated portfolio company."""
    svc = EmailThreadsService(db)
    rows = await svc.list_audited_financials_emails()
    out: list[AuditedFinancialsEmailRead] = []
    for r in rows:
        out.append(
            AuditedFinancialsEmailRead(
                id=r["id"],
                thread_id=r["thread_id"],
                portfolio_company_id=r["portfolio_company_id"],
                portfolio_company_name=r["portfolio_company_name"],
                sender=r["sender"],
                subject=r["subject"],
                email_type=r["email_type"],
                sent_at=r["sent_at"],
                is_inbound=r["is_inbound"],
                attachments=r["attachments"],
                classified_by=r["classified_by"],
                files=[AuditedFinancialsFileRead.model_validate(f) for f in r["files"]],
                diagnostics=r.get("diagnostics"),
            )
        )
    return out


@router.get(
    "/email/audited-financials/{email_id}/storage-check",
    response_model=StorageCheckResponse,
)
async def check_audited_financials_storage(
    email_id: str,
    db: AsyncSession = Depends(get_db),
):
    """LIVE check: actually try to read this email's attachments from S3 and return AWS's
    verbatim answer (accessible / AccessDenied / NoSuchKey). Definitive, not inferred — run it
    against the deployed environment to confirm whether it's the AWS/IAM permission issue."""
    svc = EmailThreadsService(db)
    try:
        data = await svc.check_attachment_storage(email_id)
        return StorageCheckResponse(
            email_id=data["email_id"],
            bucket=data["bucket"],
            overall=data["overall"],
            summary=data["summary"],
            probes=[AttachmentStorageProbe(**p) for p in data["probes"]],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})


@router.get("/email/threads/untagged", response_model=list[EmailThreadSummary])
async def list_untagged_email_threads(db: AsyncSession = Depends(get_db)):
    """Lightweight list of untagged thread cards — no email bodies."""
    svc = EmailThreadsService(db)
    rows = await svc.list_untagged_threads()
    return [
        EmailThreadSummary(
            thread_id=t["thread_id"],
            subject=t.get("subject"),
            latest_sent_at=t.get("latest_sent_at"),
            email_count=t["email_count"],
            sender=t.get("sender"),
        )
        for t in rows
    ]


@router.get("/email/threads/untagged/{thread_id}", response_model=EmailThreadRead)
async def get_untagged_thread_detail(thread_id: str, db: AsyncSession = Depends(get_db)):
    """Full thread with all email bodies — called when the user opens a thread card."""
    svc = EmailThreadsService(db)
    emails = await svc.get_untagged_thread_detail(thread_id)
    if emails is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    latest_sent_at = max((e.sent_at for e in emails if e.sent_at), default=None)
    subject = next((e.subject for e in emails if e.subject), None)
    return EmailThreadRead(
        thread_id=thread_id,
        latest_sent_at=latest_sent_at,
        subject=subject,
        email_count=len(emails),
        emails=[EmailMessageRead.model_validate(e) for e in emails],
    )


@router.post("/email/threads/tag", response_model=TagThreadResponse)
async def tag_email_thread(
    payload: TagThreadRequest,
    db: AsyncSession = Depends(get_db),
):
    """Tag an untagged email thread (or single message) with a portfolio company.

    Supply either ``thread_id`` or ``message_id`` (not both required, but at
    least one must be present).  The entire thread sharing the resolved
    ``thread_id`` is updated.  If the message has no thread, only that single
    message is tagged.
    """
    if not payload.thread_id and not payload.message_id:
        raise HTTPException(
            status_code=400,
            detail={"message": "Either thread_id or message_id must be provided"},
        )
    svc = EmailThreadsService(db)
    try:
        result = await svc.tag_thread(
            portfolio_company_id=payload.portfolio_company_id,
            thread_id=payload.thread_id,
            message_id=payload.message_id,
        )
        return TagThreadResponse(
            success=True,
            thread_id=result["thread_id"],
            portfolio_company_id=payload.portfolio_company_id,
            affected_rows=result["affected_rows"],
            message=f"Successfully tagged {result['affected_rows']} email(s) with company",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Failed to tag thread", "error": str(e)})


def _split_emails(raw: Optional[str]) -> list[str]:
    """Split a ',,'-delimited email string into a list, filtering blanks."""
    if not raw:
        return []
    return [e.strip() for e in raw.split(",,") if e.strip()]


@router.post("/email/send", response_model=SendEmailResponse)
async def send_email(
    portfolio_company_id: int = Form(...),
    to_addrs: str = Form(...),
    cc_addrs: Optional[str] = Form(None),
    subject: str = Form(...),
    body_html: str = Form(...),
    thread_id: Optional[str] = Form(None),
    reply_to_message_id: Optional[str] = Form(None),
    attachment_keys: Optional[str] = Form(None),
    attachments_id: Optional[str] = Form(None),
    attachments: Optional[list[UploadFile]] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Send a single email with optional file attachments via multipart/form-data.

    - ``to_addrs`` / ``cc_addrs``: email addresses joined with ``,,``
    - ``attachment_keys``: JSON array string of existing S3 keys (already uploaded)
    - ``attachments``: new files to upload to S3 inline before sending
    - ``reply_to_message_id``: UUID of the EmailHistory record being replied to
    """
    svc = EmailThreadsService(db)
    try:
        parsed_keys: list[str] = json.loads(attachment_keys) if attachment_keys else []
        thread_id, message_id = await svc.send_email(
            portfolio_company_id=portfolio_company_id,
            to_addrs=_split_emails(to_addrs),
            cc_addrs=_split_emails(cc_addrs),
            subject=subject,
            body_html=body_html,
            thread_id=thread_id or None,
            reply_to_message_id=reply_to_message_id or None,
            attachment_keys=parsed_keys,
            attachments_id=attachments_id or None,
            new_attachments=attachments or [],
        )
        return SendEmailResponse(success=True, thread_id=thread_id, message_id=message_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Failed to send email", "error": str(e)})


@router.post(
    "/email/reminders/send",
    response_model=Union[ReminderSendResponse, ReminderThreadSelectionRequired],
)
async def send_reminder_email(
    payload: ReminderSendRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send Reminder 1 or Reminder 2 into the original discrepancy thread.

    If ``thread_id`` is omitted and multiple eligible discrepancy threads exist,
    returns HTTP 200 with ``selection_required: true`` and ``candidates`` list so
    the frontend can present a chooser.  Re-send with the chosen ``thread_id``.
    """
    try:
        result = await send_reminder(
            db,
            portfolio_company_id=payload.portfolio_company_id,
            reminder_number=payload.reminder_number,
            to_addrs=payload.to_addrs,
            cc_addrs=payload.cc_addrs,
            subject=payload.subject,
            body_html=payload.body_html,
            attachment_keys=payload.attachment_keys,
            attachments_id=payload.attachments_id,
            thread_id=payload.thread_id,
        )
        return ReminderSendResponse(
            success=True,
            thread_id=result["thread_id"],
            message_id=result["message_id"],
            affected_entity_ids=result["affected_entity_ids"],
        )
    except ThreadSelectionRequired as exc:
        return ReminderThreadSelectionRequired(
            selection_required=True,
            candidates=[
                ThreadCandidateRead(
                    thread_id=c.thread_id,
                    subject=c.subject,
                    latest_sent_at=c.latest_sent_at,
                    affected_entity_ids=c.affected_entity_ids,
                )
                for c in exc.candidates
            ],
        )
    except (ConfigurationError, DuplicateReminderError, ValueError) as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Failed to send reminder", "error": str(e)})

