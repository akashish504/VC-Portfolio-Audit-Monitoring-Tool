"""Local test runner for classify_incoming_emails against a real Gmail inbox.

Fetches emails from Gmail (via OAuth2), constructs in-memory SourceTempEmailHistory-
like objects, and drives the classifier logic directly — no PR source DB required.
Writes results to the audit DB exactly as the real scheduler would.

Usage:
    python -m src.scripts.data_manipulation.gmail_classifier_runner [options]

Options:
    --max-results N      Max Gmail messages to fetch (default: 20)
    --query Q            Gmail search query (default: "in:inbox")
    --dry-run            Resolve + classify without writing to DB
    --env-override       Force ENV=prod for this run (required unless already set)

Reads from .env:
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_TOKEN_URI
    + standard POSTGRES_* vars for the audit DB
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# ---------------------------------------------------------------------------
# Gmail auth
# ---------------------------------------------------------------------------

def _build_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        token_uri=os.environ.get("GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=_SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Gmail fetch helpers
# ---------------------------------------------------------------------------

def _header(payload: dict, name: str) -> Optional[str]:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _extract_addresses(header_val: Optional[str]) -> list[str]:
    if not header_val:
        return []
    return [a.strip() for a in re.split(r"[,;]", header_val) if a.strip()]


def _extract_plain_email(addr: str) -> str:
    """'John Doe <john@example.com>' → 'john@example.com'"""
    m = re.search(r"<([^>]+)>", addr)
    return m.group(1).strip() if m else addr.strip()


def _body_from_payload(payload: dict) -> tuple[str, str]:
    """Return (plain_body, html_body) from a Gmail message payload."""
    plain, html = "", ""

    def _walk(part: dict) -> None:
        nonlocal plain, html
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data:
            decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if mime == "text/plain" and not plain:
                plain = decoded
            elif mime == "text/html" and not html:
                html = decoded
        for sub in part.get("parts", []):
            _walk(sub)

    _walk(payload)
    return plain, html


def _list_messages(service, query: str, max_results: int) -> list[str]:
    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return [m["id"] for m in result.get("messages", [])]


def _fetch_message(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


# ---------------------------------------------------------------------------
# In-memory SourceTempEmailHistory stand-in
# ---------------------------------------------------------------------------

@dataclass
class _FakeSourceEmail:
    """Mirrors the ORM fields read by classify_incoming_emails."""
    id: int
    message_id: str
    from_email: Optional[str]
    to: Optional[list[str]]
    cc: Optional[list[str]]
    subject: Optional[str]
    body: Optional[str]
    body_html: Optional[str]
    sent_at: Optional[datetime]
    attachments: Optional[list[str]]
    status: int = 1


def _gmail_msg_to_fake(index: int, msg: dict) -> _FakeSourceEmail:
    payload = msg.get("payload", {})
    subject = _header(payload, "Subject")
    from_raw = _header(payload, "From") or ""
    from_email = _extract_plain_email(from_raw)
    to_addrs = _extract_addresses(_header(payload, "To"))
    cc_addrs = _extract_addresses(_header(payload, "Cc"))

    date_str = _header(payload, "Date")
    sent_at: Optional[datetime] = None
    if date_str:
        try:
            sent_at = parsedate_to_datetime(date_str)
        except Exception:
            pass

    plain_body, html_body = _body_from_payload(payload)

    attachment_names: list[str] = []
    for part in payload.get("parts", []):
        fname = (part.get("filename") or "").strip()
        if fname and part.get("body", {}).get("attachmentId"):
            attachment_names.append(fname)

    return _FakeSourceEmail(
        id=index,
        message_id=msg.get("id", ""),
        from_email=from_email or None,
        to=to_addrs or None,
        cc=cc_addrs or None,
        subject=subject,
        body=plain_body or None,
        body_html=html_body or None,
        sent_at=sent_at,
        attachments=attachment_names or None,
        status=1,
    )


# ---------------------------------------------------------------------------
# Per-email result tracking
# ---------------------------------------------------------------------------

@dataclass
class _EmailResult:
    index: int
    from_email: str
    subject: str
    outcome: str          # inserted | dry_run | audited_financials | skipped | error
    company_matched: bool = False
    company_name: str = ""
    company_id: str = ""
    thread_id: str = ""
    is_inbound: Optional[bool] = None
    note: str = ""        # skip reason or error message


# ---------------------------------------------------------------------------
# Classifier logic driven against fake source emails
# ---------------------------------------------------------------------------

def _run_classifier(fake_emails: list[_FakeSourceEmail], dry_run: bool) -> None:
    import asyncio
    from pytz import timezone as pytz_timezone
    from src.db.session import get_sync_db
    from src.scripts.data_manipulation.classifying_incoming_emails import (
        clean_subject,
        find_existing_email,
        generate_unique_thread_id,
        get_last_processed_id,
        update_checkpoint,
        _is_first_company_reply_in_thread,
        _stamp_first_reply_received,
    )
    from src.db.models import EmailHistory, PortfolioCompany, EmailClassificationBatchLog, TempEmailHistory
    from src.services.email_template_config import get_discrepancy_template_id_sync
    from src.configs.env import settings

    TZ_IST = pytz_timezone("Asia/Kolkata")
    base_from_email = settings.BASE_FROM_EMAIL

    db = get_sync_db()

    discrepancy_template_id: Optional[str] = None
    try:
        discrepancy_template_id = get_discrepancy_template_id_sync(db)
    except Exception as exc:
        logger.warning("Could not resolve discrepancy template ID: %s", exc)

    processed, errors = 0, 0
    batch_id = str(uuid.uuid4())
    failure_batch: list[dict] = []
    success_batch: list[dict] = []
    results: list[_EmailResult] = []

    logger.info("Running classifier against %d Gmail message(s)", len(fake_emails))

    try:
        for temp_email in fake_emails:
            res = _EmailResult(
                index=temp_email.id,
                from_email=temp_email.from_email or "",
                subject=temp_email.subject or "",
                outcome="skipped",
            )
            try:
                logger.info(
                    "Processing id=%s from=%s subject=%r",
                    temp_email.id, temp_email.from_email, temp_email.subject,
                )

                if not temp_email.subject or not temp_email.from_email:
                    logger.warning("  → SKIP: missing subject or from_email")
                    res.outcome = "skipped"
                    res.note = "missing subject or from_email"
                    errors += 1
                    failure_batch.append({
                        "batch_id": batch_id,
                        "email_type": "incoming_mail_process",
                        "error_message": "Missing required fields - subject or from_email",
                        "status": "failed",
                        "company_name": "",
                        "company_id": "",
                        "email_message_id": str(temp_email.id),
                        "email_payload": str(vars(temp_email)),
                    })
                    results.append(res)
                    continue

                if temp_email.from_email == base_from_email:
                    logger.info("  → SKIP: email is from our own address (%s)", base_from_email)
                    res.note = "sent from our own address"
                    results.append(res)
                    continue

                # Route "Audited Financials_…" emails to file-extraction pipeline
                cleaned_subject = clean_subject(temp_email.subject or "")
                if cleaned_subject.startswith("Audited Financials_"):
                    logger.info(
                        "  → AUDITED FINANCIALS: routing to extraction pipeline\n"
                        "    raw subject   : %s\n"
                        "    cleaned subject: %s",
                        temp_email.subject,
                        cleaned_subject,
                    )
                    res.outcome = "audited_financials"
                    if dry_run:
                        res.note = "dry-run: would trigger audited_financials pipeline"
                        logger.info("  [DRY RUN] Would trigger audited_financials pipeline")
                    else:
                        try:
                            from src.scripts.data_manipulation.audited_financials_email_ingestion import (
                                _process_one_email as _process_audited,
                                AuditedFinancialsResult,
                            )
                            from src.db.session import AsyncSessionLocal

                            fake_temp = TempEmailHistory(
                                message_id=temp_email.message_id,
                                from_email=temp_email.from_email,
                                to=temp_email.to,
                                cc=temp_email.cc,
                                subject=temp_email.subject,
                                body=temp_email.body,
                                body_html=temp_email.body_html,
                                sent_at=temp_email.sent_at,
                                attachments=temp_email.attachments,
                                status=temp_email.status,
                            )
                            db.add(fake_temp)
                            db.flush()

                            af_result: AuditedFinancialsResult = AuditedFinancialsResult()

                            async def _run_audited() -> None:
                                nonlocal af_result
                                async with AsyncSessionLocal() as async_db:
                                    af_result = await _process_audited(async_db, fake_temp)

                            asyncio.run(_run_audited())
                            db.commit()

                            res.company_matched = af_result.company_matched
                            res.company_name = af_result.company_name
                            res.company_id = af_result.company_id
                            res.note = af_result.skip_reason or (
                                "entity not found (ok)" if not af_result.entity_matched and af_result.company_matched else ""
                            )
                            logger.info(
                                "  → audited_financials pipeline completed: company_matched=%s company=%r cycle=%s",
                                af_result.company_matched,
                                af_result.company_name,
                                af_result.review_cycle_id,
                            )
                        except Exception as af_exc:
                            db.rollback()
                            res.outcome = "error"
                            res.note = str(af_exc)
                            logger.exception("  → audited_financials pipeline failed: %s", af_exc)
                    results.append(res)
                    continue

                existing = db.query(EmailHistory).filter(
                    EmailHistory.subject == temp_email.subject,
                    EmailHistory.sender == temp_email.from_email,
                    EmailHistory.sent_at == temp_email.sent_at,
                ).first()
                if existing:
                    logger.info("  → SKIP: already in EmailHistory (id=%s)", existing.id)
                    res.note = "already processed"
                    errors += 1
                    failure_batch.append({
                        "batch_id": batch_id,
                        "email_type": "incoming_mail_process",
                        "status": "failed",
                        "error_message": "Already processed",
                        "company_name": "",
                        "company_id": "",
                        "email_message_id": str(temp_email.id),
                        "email_payload": str(vars(temp_email)),
                    })
                    results.append(res)
                    continue

                candidate, should_flag = find_existing_email(db, temp_email.subject)
                if candidate:
                    thread_id = candidate.thread_id
                    company_id = candidate.company_id
                    company_pr_cycle_id = candidate.company_pr_cycle_id
                    logger.info(
                        "  → MATCHED existing thread thread_id=%s company_id=%s",
                        thread_id, company_id,
                    )
                else:
                    thread_id = generate_unique_thread_id(db)
                    company_id = None
                    company_pr_cycle_id = None
                    logger.info("  → NEW thread thread_id=%s", thread_id)

                pc_row: Optional[object] = None
                portfolio_company_id: Optional[int] = None
                if company_id:
                    q = db.query(PortfolioCompany).filter(
                        PortfolioCompany.company_id == company_id
                    )
                    if company_pr_cycle_id:
                        q = q.filter(PortfolioCompany.review_cycle_id == company_pr_cycle_id)
                    pc_row = q.order_by(PortfolioCompany.id.desc()).first()
                    if pc_row:
                        portfolio_company_id = pc_row.id

                if should_flag and temp_email.from_email != base_from_email and company_id:
                    if not dry_run:
                        upd = db.query(PortfolioCompany).filter(
                            PortfolioCompany.company_id == company_id
                        )
                        if company_pr_cycle_id:
                            upd = upd.filter(
                                PortfolioCompany.review_cycle_id == company_pr_cycle_id
                            )
                        upd.update({"company_stage": "In Progress - TBD"})
                    logger.info("  → FLAGGED company_id=%s as 'In Progress - TBD'", company_id)

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

                company_name = pc_row.name if pc_row else ""

                res.company_matched = bool(company_id)
                res.company_name = company_name
                res.company_id = str(company_id) if company_id else ""
                res.thread_id = thread_id
                res.is_inbound = is_inbound

                logger.info(
                    "  → is_inbound=%s company=%r portfolio_company_id=%s",
                    is_inbound, company_name or "(unmatched)", portfolio_company_id,
                )

                if dry_run:
                    logger.info("  [DRY RUN] Would insert EmailHistory row — skipping DB write")
                    res.outcome = "dry_run"
                    processed += 1
                    success_batch.append({
                        "batch_id": batch_id,
                        "email_type": "incoming_mail_process",
                        "status": "dry_run",
                        "company_name": company_name,
                        "company_id": company_id or "",
                        "email_message_id": str(temp_email.id),
                    })
                    results.append(res)
                    continue

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
                    attachments=temp_email.attachments,
                    sent_at=sent_final,
                    is_inbound=is_inbound,
                    created_at=datetime.utcnow(),
                    updated_on=datetime.utcnow(),
                    status=int(temp_email.status or 0),
                )
                db.add(new_email)

                if (
                    is_inbound
                    and thread_id
                    and candidate is not None
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

                db.commit()
                logger.info("  → INSERTED EmailHistory id=%s", new_email.id)
                res.outcome = "inserted"
                processed += 1
                success_batch.append({
                    "batch_id": batch_id,
                    "email_type": "incoming_mail_process",
                    "status": "success",
                    "company_name": company_name,
                    "company_id": company_id or "",
                    "email_message_id": str(temp_email.id),
                })

            except Exception as exc:
                db.rollback()
                logger.exception("Error processing message id=%s: %s", temp_email.id, exc)
                res.outcome = "error"
                res.note = str(exc)
                errors += 1
                failure_batch.append({
                    "batch_id": batch_id,
                    "email_type": "incoming_mail_process",
                    "status": "failed",
                    "error_message": str(exc),
                    "company_name": "",
                    "company_id": "",
                    "email_message_id": str(temp_email.id),
                    "email_payload": str(vars(temp_email)),
                })

            results.append(res)

        # Write batch logs
        if not dry_run and (failure_batch or success_batch):
            try:
                ts = datetime.utcnow()
                for row in failure_batch + success_batch:
                    db.add(EmailClassificationBatchLog(
                        id=str(uuid.uuid4()),
                        batch_id=row.get("batch_id"),
                        email_type=row.get("email_type"),
                        error_message=row.get("error_message"),
                        email_payload=row.get("email_payload"),
                        status=row.get("status"),
                        company_name=row.get("company_name", ""),
                        company_id=row.get("company_id", ""),
                        email_message_id=row.get("email_message_id", ""),
                        created_at=ts,
                    ))
                db.commit()
                logger.info("Batch logs written (%d rows)", len(failure_batch) + len(success_batch))
            except Exception as le:
                logger.exception("Failed to write batch logs: %s", le)
                db.rollback()

    finally:
        db.close()

    _print_summary(results, dry_run)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_OUTCOME_ICON = {
    "inserted":            "[OK]",
    "dry_run":             "[DR]",
    "audited_financials":  "[AF]",
    "skipped":             "[--]",
    "error":               "[ER]",
}

_COMPANY_ICON = {True: "YES", False: "NO "}


def _print_summary(results: list[_EmailResult], dry_run: bool) -> None:
    tag = "[DRY RUN] " if dry_run else ""
    total     = len(results)
    inserted  = sum(1 for r in results if r.outcome in ("inserted", "dry_run", "audited_financials"))
    matched   = sum(1 for r in results if r.company_matched)
    unmatched = sum(1 for r in results if r.outcome not in ("skipped",) and not r.company_matched)
    skipped   = sum(1 for r in results if r.outcome == "skipped")
    errors    = sum(1 for r in results if r.outcome == "error")

    W = 90
    print("\n" + "=" * W)
    print(f"  {tag}GMAIL CLASSIFIER — PER-EMAIL RESULTS")
    print("=" * W)
    print(f"  {'#':<4}  {'OUTCOME':<10}  {'COMPANY?':<8}  {'COMPANY NAME':<28}  SUBJECT")
    print("-" * W)
    for r in results:
        icon    = _OUTCOME_ICON.get(r.outcome, "?")
        matched_tag = _COMPANY_ICON[r.company_matched]
        co_name = (r.company_name or "(no match)")[:28]
        subject = r.subject
        print(f"  {r.index:<4}  {icon:<10}  {matched_tag:<8}  {co_name:<28}  {subject}")
        if r.note:
            print(f"        note: {r.note}")
        if r.company_matched:
            direction = "inbound" if r.is_inbound else "outbound"
            print(f"        from: {r.from_email}  thread: {r.thread_id}  direction: {direction}")
    print("=" * W)
    print(f"  Total fetched: {total}  |  Processed: {inserted}  |  "
          f"Company matched: {matched}  |  Unmatched: {unmatched}  |  "
          f"Skipped: {skipped}  |  Errors: {errors}")
    print("=" * W + "\n")
    print("  Legend: [OK]=inserted  [AF]=audited_financials pipeline  "
          "[DR]=dry-run  [--]=skipped  [ER]=error\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run classify_incoming_emails logic against your Gmail inbox"
    )
    parser.add_argument("--max-results", type=int, default=20,
                        help="Max Gmail messages to fetch (default: 20)")
    parser.add_argument("--query", default="in:inbox",
                        help="Gmail search query (default: 'in:inbox')")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify and log without writing to DB")
    parser.add_argument("--env-override", action="store_true",
                        help="Temporarily set ENV=prod so the classifier guard passes")
    args = parser.parse_args()

    if args.env_override:
        os.environ["ENV"] = "prod"
        logger.info("ENV overridden to 'prod' for this run")

    logger.info("Fetching up to %d messages from Gmail (query: %r)", args.max_results, args.query)
    service = _build_gmail_service()
    ids = _list_messages(service, args.query, args.max_results)
    logger.info("Found %d Gmail message(s)", len(ids))

    fake_emails: list[_FakeSourceEmail] = []
    for i, gid in enumerate(ids, start=1):
        try:
            msg = _fetch_message(service, gid)
            fake_emails.append(_gmail_msg_to_fake(i, msg))
        except Exception as exc:
            logger.warning("Could not fetch gmail_id=%s: %s", gid, exc)

    _run_classifier(fake_emails, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
