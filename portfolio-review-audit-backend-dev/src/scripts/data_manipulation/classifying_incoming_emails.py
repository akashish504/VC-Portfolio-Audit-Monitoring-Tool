"""Classify staged inbound emails (SourceTempEmailHistory → EmailHistory), portfolio-review parity."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Optional, Tuple, List

from pytz import timezone as pytz_timezone
from sqlalchemy.orm import Session

from pathlib import Path

from src.configs.env import settings
from src.db.models import (
    CompanyViewAudit,
    EmailClassificationBatchLog,
    EmailHistory,
    EmailProcessingCheckPoint,
    Entity,
    PortfolioCompany,
    SourceTempEmailHistory,
)

from src.db.session import get_sync_db, get_pr_source_db
from src.schema.portfolio import CompanyReviewStage
from src.services.email_template_config import get_discrepancy_template_id_sync
from src.utils.s3 import get_s3_client

logger = logging.getLogger(__name__)

TZ_IST = pytz_timezone("Asia/Kolkata")


def _copy_attachments_to_our_bucket(
    source_keys: List[str],
    thread_id: str,
) -> List[str]:
    """Copy source-system attachment keys into our email bucket under email-attachments/{thread_id}/.

    Returns the new keys. Falls back to the original key on any per-file error so classification
    always succeeds even if S3 is misconfigured.
    """
    bucket = settings.AWS_S3_EMAIL_BUCKET
    attach_path = settings.AWS_S3_EMAIL_ATTACH_PATH
    if not bucket or not source_keys:
        return list(source_keys)

    _SAFE_RE = re.compile(r"[^a-zA-Z0-9.\-_]+")

    client = get_s3_client()
    new_keys: List[str] = []
    for src_key in source_keys:
        raw_name = Path(src_key).name or "attachment"
        filename = _SAFE_RE.sub("_", raw_name.replace(" ", "_"))[:180]
        dest_key = f"{attach_path}/{thread_id}/{filename}"
        try:
            client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": src_key},
                Key=dest_key,
            )
            new_keys.append(dest_key)
        except Exception as exc:
            logger.warning(
                "classify_incoming_emails: could not copy attachment %r to %r — keeping original key. Error: %s",
                src_key,
                dest_key,
                exc,
            )
            new_keys.append(src_key)
    return new_keys


def _last_id_from_checkpoint_row(row: Optional[EmailProcessingCheckPoint]) -> int:
    if row is None or not row.checkpoint_value:
        return 0
    try:
        return int(row.checkpoint_value.get("last_processed_id", 0))
    except (TypeError, ValueError):
        return 0


def get_last_processed_id(db: Session) -> int:
    ck = (
        db.query(EmailProcessingCheckPoint)
        .filter(EmailProcessingCheckPoint.checkpoint_key == "email_classifier")
        .first()
    )
    if ck is None:
        initial = EmailProcessingCheckPoint(
            checkpoint_key="email_classifier",
            checkpoint_value={"last_processed_id": 0},
        )
        db.add(initial)
        db.commit()
        return 0
    return _last_id_from_checkpoint_row(ck)


def update_checkpoint(db: Session, last_processed_id: int) -> None:
    ck = (
        db.query(EmailProcessingCheckPoint)
        .filter(EmailProcessingCheckPoint.checkpoint_key == "email_classifier")
        .first()
    )
    merged = {"last_processed_id": last_processed_id}
    if ck is not None:
        base = dict(ck.checkpoint_value or {})
        base.update(merged)
        ck.checkpoint_value = base
    else:
        db.add(
            EmailProcessingCheckPoint(
                checkpoint_key="email_classifier",
                checkpoint_value=merged,
            )
        )
    db.commit()


def clean_subject(subject: Optional[str]) -> str:
    if not subject:
        return ""
    pattern = (
        r"^(re:|fwd:|fw:|external:|\[\s*external\s*\]|\[\s*external\s*mail\s*\]|"
        r"\[\s*EXT\s*\])\s*"
    )
    s = subject
    while re.match(pattern, s, flags=re.IGNORECASE):
        s = re.sub(pattern, "", s, count=1, flags=re.IGNORECASE).strip()
    return s.strip()


def find_existing_email(
    db: Session, subject: Optional[str]
) -> Tuple[Optional[EmailHistory], bool]:
    if not subject:
        return None, False

    exact_match = db.query(EmailHistory).filter(EmailHistory.subject.ilike(subject)).first()
    if exact_match:
        candidate = exact_match
    else:
        normalized_subject = clean_subject(subject or "")
        candidates = db.query(EmailHistory).filter(
            EmailHistory.subject.ilike(f"%{normalized_subject}%")
        ).all()
        candidate = None
        for c in candidates:
            candidate_subject = c.subject or ""
            candidate_normalized = clean_subject(candidate_subject)
            if (
                normalized_subject in candidate_normalized
                or candidate_normalized in normalized_subject
                or normalized_subject == candidate_normalized
            ):
                candidate = c
                break

    should_flag = False
    if candidate and candidate.company_id:
        records = db.query(EmailHistory).filter(EmailHistory.company_id == candidate.company_id).all()
        if any((email.email_type or "") == "query_email" for email in records):
            should_flag = True

    return candidate, should_flag


def generate_unique_thread_id(db: Session) -> str:
    for _ in range(10):
        tid = str(uuid.uuid4())
        exists = db.query(EmailHistory).filter(EmailHistory.thread_id == tid).first()
        if not exists:
            return tid
    return f"thread_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:8]}"


def _stamp_first_reply_received(
    db: Session,
    *,
    thread_id: str,
    sent_at: datetime,
    discrepancy_template_id: Optional[str],
) -> None:
    """For the first company-side reply in a thread, stamp first_reply_received_at on affected entities."""
    if not discrepancy_template_id:
        return

    # Find the outbound discrepancy email in this thread
    disc_email = (
        db.query(EmailHistory)
        .filter(
            EmailHistory.thread_id == thread_id,
            EmailHistory.template_id == discrepancy_template_id,
            EmailHistory.is_inbound.is_(False),
        )
        .order_by(EmailHistory.sent_at.desc())
        .first()
    )
    if disc_email is None or not disc_email.affected_entity_ids:
        return

    # Statuses a reply is actually answering. Only entities awaiting a response move.
    _PRECEDING_RESPONSE_STAGES = {
        CompanyReviewStage.QUERY_SENT.value,
        CompanyReviewStage.QUERY_RESPONSE_REMINDER_1.value,
        CompanyReviewStage.QUERY_RESPONSE_REMINDER_2.value,
    }
    response_received = CompanyReviewStage.RESPONSE_RECEIVED.value

    # Stamp first-reply timestamp and shift status to Response received, but only on
    # the affected entities that were actually awaiting a response. State lives on
    # the entity now; the company is no longer touched.
    for entity_id in disc_email.affected_entity_ids:
        ent = db.query(Entity).filter(Entity.id == int(entity_id)).first()
        if ent is None:
            continue
        if ent.first_reply_received_at is None:
            ent.first_reply_received_at = sent_at
            db.add(ent)
        if (ent.status or "").strip() in _PRECEDING_RESPONSE_STAGES:
            before_status = ent.status
            ent.status = response_received
            db.add(ent)
            db.add(CompanyViewAudit(
                id=str(uuid.uuid4()),
                user_id="system",
                company_id=str(ent.portfolio_company_id),
                action=(
                    f'Status of "{ent.name or ent.id}" updated from '
                    f"{before_status or '—'} \u2192 {response_received}"
                )[:255],
                meta={
                    "entity_type": "entity",
                    "entity_id": ent.id,
                    "field": "status",
                    "before": before_status,
                    "after": response_received,
                    "event": "entity.status_auto_response_received",
                    "occurred_at": datetime.now(dt_timezone.utc).isoformat(),
                },
            ))


def _is_first_company_reply_in_thread(
    db: Session,
    *,
    thread_id: str,
    from_email: str,
    base_from_email: str,
) -> bool:
    """Return True if this is the very first inbound (company-side) email in the thread."""
    existing_inbound = (
        db.query(EmailHistory)
        .filter(
            EmailHistory.thread_id == thread_id,
            EmailHistory.is_inbound.is_(True),
        )
        .count()
    )
    # The email being classified hasn't been inserted yet, so count == 0 means first reply
    return existing_inbound == 0


def classify_incoming_emails() -> None:
    """
    Read new rows from SourceTempEmailHistory (since checkpoint), classify into EmailHistory.

    Mirrors portfolio-review-app-api-develop ``classifying_incoming_emails``.
    Called by APScheduler (default: every 10 minutes).

    Only runs when ENV=prod.  Source rows are read from the portfolio-review
    Postgres server via a dedicated session (get_pr_source_db); all writes go
    to the audit DB via the regular session (get_sync_db).
    """
    if settings.ENV != "prod":
        logger.info(
            "classify_incoming_emails: ENV=%s (not prod) — skipping run", settings.ENV
        )
        return

    db = get_sync_db()
    source_db = get_pr_source_db()  # separate connection to portfolio-review server
    processed_count = 0
    error_count = 0
    last_processed_id_in_batch = 0

    # Resolve the discrepancy template ID once per job run (may be None if config missing)
    discrepancy_template_id: Optional[str] = None
    try:
        discrepancy_template_id = get_discrepancy_template_id_sync(db)
    except Exception as exc:
        logger.warning(
            "Could not resolve discrepancy template ID from ConfigTable; "
            "first_reply_received_at stamping will be skipped: %s",
            exc,
        )

    base_from_email = settings.BASE_FROM_EMAIL

    try:
        last_processed_id = get_last_processed_id(db)
        logger.info("Starting email classification from checkpoint id=%s", last_processed_id)

        batch_size = 100
        failure_batch_id = str(uuid.uuid4())
        failure_batch: List[dict] = []
        success_batch: List[dict] = []

        total_new = source_db.query(SourceTempEmailHistory).filter(SourceTempEmailHistory.id > last_processed_id).count()
        logger.info("Found %s new temp emails", total_new)
        if total_new == 0:
            logger.info("No new emails — exiting classify_incoming_emails")
            return

        batch_no = 1
        current_id = last_processed_id

        while True:
            chunk = (
                source_db.query(SourceTempEmailHistory)
                .filter(SourceTempEmailHistory.id > current_id)
                .order_by(SourceTempEmailHistory.id)
                .limit(batch_size)
                .all()
            )
            if not chunk:
                break

            bs, be = chunk[0].id, chunk[-1].id
            logger.info(
                "Batch %s: %s emails (ids %s — %s)", batch_no, len(chunk), bs, be
            )

            for temp_email in chunk:
                try:
                    if not temp_email.subject or not temp_email.from_email:
                        error_count += 1
                        failure_batch.append({
                            "batch_id": failure_batch_id,
                            "email_type": "incoing_mail_process",
                            "error_message": "Missing required fields - subject or from_email",
                            "status": "failed",
                            "company_name": "",
                            "company_id": "",
                            "email_message_id": str(temp_email.id),
                            "email_payload": str({
                                "to": temp_email.to,
                                "cc": temp_email.cc,
                                "subject": temp_email.subject,
                                "from_email": temp_email.from_email,
                                "sent_at": temp_email.sent_at,
                                "attachments": temp_email.attachments,
                                "status": temp_email.status,
                            }),
                        })
                        last_processed_id_in_batch = max(last_processed_id_in_batch, temp_email.id)
                        current_id = temp_email.id
                        continue

                    # Skip emails sent from our own address
                    if temp_email.from_email == base_from_email:
                        last_processed_id_in_batch = max(last_processed_id_in_batch, temp_email.id)
                        current_id = temp_email.id
                        continue

                    existing_processed = db.query(EmailHistory).filter(
                        EmailHistory.subject == temp_email.subject,
                        EmailHistory.sender == temp_email.from_email,
                        EmailHistory.sent_at == temp_email.sent_at,
                    ).first()

                    if existing_processed:
                        error_count += 1
                        failure_batch.append({
                            "batch_id": failure_batch_id,
                            "email_type": "incoing_mail_process",
                            "status": "failed",
                            "company_name": "",
                            "company_id": "",
                            "email_message_id": str(temp_email.id),
                            "error_message": "Already processed",
                            "email_payload": str({
                                "to": temp_email.to,
                                "cc": temp_email.cc,
                                "subject": temp_email.subject,
                                "from_email": temp_email.from_email,
                                "sent_at": temp_email.sent_at,
                                "attachments": temp_email.attachments,
                                "status": temp_email.status,
                            }),
                        })
                        last_processed_id_in_batch = max(last_processed_id_in_batch, temp_email.id)
                        current_id = temp_email.id
                        continue

                    candidate, should_flag = find_existing_email(db, temp_email.subject)
                    if candidate:
                        thread_id = candidate.thread_id
                        company_id = candidate.company_id
                        company_pr_cycle_id = candidate.company_pr_cycle_id
                    else:
                        thread_id = generate_unique_thread_id(db)
                        company_id = None
                        company_pr_cycle_id = None

                    pc_row: Optional[PortfolioCompany] = None
                    portfolio_company_id: Optional[int] = None
                    if company_id:
                        q = db.query(PortfolioCompany).filter(PortfolioCompany.company_id == company_id)
                        if company_pr_cycle_id:
                            q = q.filter(PortfolioCompany.review_cycle_id == company_pr_cycle_id)
                        pc_row = q.order_by(PortfolioCompany.id.desc()).first()
                        if pc_row:
                            portfolio_company_id = pc_row.id
                    # Threads created without legacy company_id (outbound sends, audited-financials
                    # rows whose legacy refs were degraded) still carry portfolio_company_id —
                    # inherit it so replies stay visible on the company's email page.
                    if portfolio_company_id is None and candidate is not None:
                        portfolio_company_id = candidate.portfolio_company_id

                    if (
                        should_flag
                        and temp_email.from_email != base_from_email
                        and company_id
                    ):
                        upd = db.query(PortfolioCompany).filter(PortfolioCompany.company_id == company_id)
                        if company_pr_cycle_id:
                            upd = upd.filter(PortfolioCompany.review_cycle_id == company_pr_cycle_id)
                        upd.update({"company_stage": "In Progress - TBD"})

                    # Email is from the company side (not from us)
                    is_inbound = temp_email.from_email != base_from_email

                    if temp_email.sent_at:
                        st = temp_email.sent_at
                        sent_final = (
                            st.astimezone(TZ_IST)
                            if st.tzinfo is not None
                            else TZ_IST.localize(st.replace(tzinfo=None))
                        )
                    else:
                        sent_final = datetime.now(dt_timezone.utc).astimezone(TZ_IST)

                    # Copy inbound attachments into our bucket so presign and
                    # downstream pipelines always resolve against AWS_S3_EMAIL_BUCKET.
                    attachment_keys = _copy_attachments_to_our_bucket(
                        list(temp_email.attachments or []),
                        thread_id,
                    )

                    new_email = EmailHistory(
                        id=str(uuid.uuid4()),
                        thread_id=thread_id,
                        portfolio_company_id=portfolio_company_id,
                        company_id=company_id,
                        company_pr_cycle_id=company_pr_cycle_id,
                        sender=(temp_email.from_email or "").strip(),
                        recipients=temp_email.to,
                        cc=temp_email.cc,
                        subject=(temp_email.subject or "").strip(),
                        body=(temp_email.body_html or temp_email.body or ""),
                        email_type="",
                        attachments=attachment_keys,
                        sent_at=sent_final,
                        is_inbound=is_inbound,
                        created_at=datetime.utcnow(),
                        updated_on=datetime.utcnow(),
                        status=int(temp_email.status or 0),
                    )
                    db.add(new_email)

                    # Stamp first_reply_received_at for the first company-side reply in an existing thread
                    if (
                        is_inbound
                        and thread_id
                        and candidate is not None  # email matched to an existing thread
                        and _is_first_company_reply_in_thread(
                            db,
                            thread_id=thread_id,
                            from_email=temp_email.from_email,
                            base_from_email=base_from_email,
                        )
                    ):
                        _stamp_first_reply_received(
                            db,
                            thread_id=thread_id,
                            sent_at=sent_final,
                            discrepancy_template_id=discrepancy_template_id,
                        )

                    processed_count += 1

                    company_name = pc_row.name if pc_row else ""

                    success_batch.append({
                        "batch_id": failure_batch_id,
                        "email_type": "incoming_mail_process",
                        "status": "success",
                        "company_name": company_name,
                        "company_id": company_id or "",
                        "email_message_id": str(temp_email.id),
                    })

                    last_processed_id_in_batch = max(last_processed_id_in_batch, temp_email.id)
                    current_id = temp_email.id

                except Exception as e:
                    failure_batch.append({
                        "batch_id": failure_batch_id,
                        "email_type": "incoming_mail_process",
                        "status": "failed",
                        "company_name": "",
                        "company_id": "",
                        "email_message_id": str(temp_email.id),
                        "error_message": str(e),
                        "email_payload": str({
                            "to": temp_email.to,
                            "cc": temp_email.cc,
                            "subject": temp_email.subject,
                            "from_email": temp_email.from_email,
                            "sent_at": temp_email.sent_at,
                            "attachments": temp_email.attachments,
                            "status": temp_email.status,
                        }),
                    })
                    logger.exception("Error processing temp email id=%s", temp_email.id)
                    error_count += 1
                    last_processed_id_in_batch = max(last_processed_id_in_batch, temp_email.id)
                    current_id = temp_email.id
                    continue

            try:
                db.commit()

                if last_processed_id_in_batch > last_processed_id:
                    update_checkpoint(db, last_processed_id_in_batch)
                    last_processed_id = last_processed_id_in_batch
                    logger.info("Checkpoint advanced to id=%s", last_processed_id_in_batch)

                if failure_batch or success_batch:
                    ts = datetime.utcnow()
                    try:
                        for fr in failure_batch:
                            db.add(
                                EmailClassificationBatchLog(
                                    id=str(uuid.uuid4()),
                                    batch_id=fr.get("batch_id"),
                                    email_type=fr.get("email_type"),
                                    error_message=fr.get("error_message", ""),
                                    email_payload=fr.get("email_payload") or "",
                                    status=fr.get("status"),
                                    company_name=fr.get("company_name", ""),
                                    company_id=fr.get("company_id", ""),
                                    email_message_id=fr.get("email_message_id", ""),
                                    created_at=ts,
                                )
                            )
                        for sr in success_batch:
                            db.add(
                                EmailClassificationBatchLog(
                                    id=str(uuid.uuid4()),
                                    batch_id=sr.get("batch_id"),
                                    email_type=sr.get("email_type"),
                                    error_message=None,
                                    email_payload=None,
                                    status=sr.get("status"),
                                    company_name=sr.get("company_name", ""),
                                    company_id=sr.get("company_id", ""),
                                    email_message_id=sr.get("email_message_id", ""),
                                    created_at=ts,
                                )
                            )
                        db.commit()
                    except Exception as le:
                        logger.exception("Storing classifier batch logs failed: %s", le)
                        db.rollback()

                failure_batch.clear()
                success_batch.clear()

            except Exception as ce:
                db.rollback()
                logger.exception("Commit failed on batch %s: %s", batch_no, ce)
                error_count += len(chunk)
                break

            batch_no += 1

        logger.info(
            "Classifier summary: inserted=%s errors=%s last_id=%s",
            processed_count,
            error_count,
            last_processed_id_in_batch,
        )

        # Trigger the audited-financials pipeline as a separate one-shot scheduler job
        # so it picks up matching EmailHistory rows just committed. Runs on the
        # AsyncIOScheduler's own loop — no asyncio.run() involved.
        if processed_count > 0:
            try:
                from datetime import timezone as _tz
                from src.scheduler.scheduler import JobScheduler
                from src.scripts.data_manipulation.audited_financials_email_ingestion import (
                    process_audited_financials_emails,
                )
                JobScheduler().add_job(
                    process_audited_financials_emails,
                    "date",
                    run_date=datetime.now(_tz.utc),
                    id="process_audited_financials_emails_triggered",
                    replace_existing=True,
                    max_instances=1,
                )
                logger.info("classify_incoming_emails: triggered process_audited_financials_emails")
            except Exception as _trigger_exc:
                logger.warning(
                    "classify_incoming_emails: could not trigger audited_financials job: %s",
                    _trigger_exc,
                )

    except Exception as outer:
        db.rollback()
        logger.exception("Critical error in classify_incoming_emails: %s", outer)
        raise
    finally:
        db.close()
        source_db.close()





if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    classify_incoming_emails()
