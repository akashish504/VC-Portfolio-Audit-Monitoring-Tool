"""Direct local test runner for audited-financials email ingestion.

Bypasses TempEmailHistory entirely. For each Gmail message whose subject
matches the audited-financials pattern:
  1. Parse subject → company name, entity name, fy_end.
  2. Resolve review_cycle_id and match PortfolioCompany / Entity from DB.
  3. Download each attachment from Gmail directly.
  4. Upload straight into the audit file pipeline (File / PortfolioFile row).
  5. Queue and run extraction with kind="audit_financials".

Gmail messages are never modified (no mark-read, no labels, no archive).
TempEmailHistory and EmailHistory are not touched.

Usage:
    python -m src.scripts.data_manipulation.gmail_direct_audit_runner [--max-results 20] [--dry-run]

Reads credentials from env:
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_TOKEN_URI
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_SUBJECT_PREFIX = "Audited Financials"
_EXTRACTION_KIND = "audit_financials"


# ---------------------------------------------------------------------------
# Gmail auth (same approach as main runner — refresh token from env)
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


def _list_audited_financials_messages(service, max_results: int) -> list[str]:
    result = (
        service.users()
        .messages()
        .list(userId="me", q=f"subject:{_SUBJECT_PREFIX}", maxResults=max_results)
        .execute()
    )
    return [m["id"] for m in result.get("messages", [])]


def _fetch_message(service, message_id: str) -> dict:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def _download_gmail_attachment(service, message_id: str, attachment_id: str) -> bytes:
    data = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    raw = data.get("data", "")
    return base64.urlsafe_b64decode(raw + "==")


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class AttachmentResult:
    filename: str
    status: str          # success | skipped | failed
    file_id: Optional[int] = None
    error: Optional[str] = None


@dataclass
class MessageResult:
    gmail_id: str
    subject: str
    company_name: Optional[str] = None
    company_id: Optional[int] = None
    entity_id: Optional[int] = None
    review_cycle_id: Optional[str] = None
    attachments: list[AttachmentResult] = field(default_factory=list)
    skip_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Core async processing
# ---------------------------------------------------------------------------

async def _process_message(
    service,
    gmail_id: str,
    msg: dict,
    dry_run: bool,
) -> MessageResult:
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
        resolve_company,
        resolve_entity,
    )
    from src.services.fy_end import normalize_fy_end, resolve_review_cycle_id_for_fy_end
    from src.services.audit_service import (
        AuditService,
        validate_audit_upload_filename,
        MAX_AUDIT_UPLOAD_BYTES,
    )
    from src.db.session import AsyncSessionLocal

    payload = msg.get("payload", {})
    subject = _header(payload, "Subject") or ""
    result = MessageResult(gmail_id=gmail_id, subject=subject)

    # 1. Parse subject
    parsed = parse_audited_financials_subject(subject)
    if parsed is None:
        result.skip_reason = "subject does not match audited-financials pattern"
        return result

    # 2. Normalize FY end
    fy_end = normalize_fy_end(parsed.fy_end_raw)
    if not fy_end:
        result.skip_reason = f"invalid fy_end {parsed.fy_end_raw!r}"
        return result

    async with AsyncSessionLocal() as db:
        # 3. Resolve review cycle
        try:
            review_cycle_id = await resolve_review_cycle_id_for_fy_end(db, fy_end)
        except Exception as exc:
            result.skip_reason = f"cannot resolve review_cycle for fy_end={fy_end!r}: {exc}"
            return result

        result.review_cycle_id = review_cycle_id

        # 4. Match company (mandatory)
        company = await resolve_company(db, parsed.company_name, review_cycle_id)
        if company is None:
            result.skip_reason = (
                f"no PortfolioCompany matched name={parsed.company_name!r} "
                f"review_cycle_id={review_cycle_id!r}"
            )
            return result

        result.company_name = company.name
        result.company_id = company.id

        # 5. Match entity (optional)
        entity = await resolve_entity(db, company.id, parsed.entity_name, review_cycle_id)
        result.entity_id = entity.id if entity else None

        if dry_run:
            logger.info(
                "[DRY RUN] Would upload attachments for gmail_id=%s company=%r entity_id=%s cycle=%s",
                gmail_id, company.name, result.entity_id, review_cycle_id,
            )

        # 6. Process each top-level attachment
        for part in payload.get("parts", []):
            att_id = part.get("body", {}).get("attachmentId")
            filename = (part.get("filename") or "").strip()
            if not att_id or not filename:
                continue

            fn_err = validate_audit_upload_filename(filename)
            if fn_err:
                result.attachments.append(AttachmentResult(
                    filename=filename, status="skipped", error=fn_err
                ))
                logger.warning("direct_runner: skipping %r — %s", filename, fn_err)
                continue

            if dry_run:
                result.attachments.append(AttachmentResult(filename=filename, status="dry_run"))
                logger.info("[DRY RUN] Would upload %r", filename)
                continue

            # Download from Gmail
            try:
                file_bytes = await asyncio.to_thread(
                    _download_gmail_attachment, service, gmail_id, att_id
                )
            except Exception as exc:
                result.attachments.append(AttachmentResult(
                    filename=filename, status="failed", error=f"gmail download: {exc}"
                ))
                logger.error("direct_runner: gmail download failed %r: %s", filename, exc)
                continue

            if len(file_bytes) > MAX_AUDIT_UPLOAD_BYTES:
                result.attachments.append(AttachmentResult(
                    filename=filename, status="skipped", error="exceeds 50MB limit"
                ))
                continue

            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

            # Upload into audit pipeline
            try:
                svc = AuditService(db)
                init = await svc.generate_upload_url(
                    company_id=company.id,
                    file_name=filename,
                    mime_type=mime_type,
                    entity_id=result.entity_id,
                    review_cycle_id=review_cycle_id,
                    fy_end=fy_end,
                )
                file_id = int(init["file_id"])
                await svc.upload_to_existing(
                    file_id=file_id,
                    file_bytes=file_bytes,
                    content_type=mime_type,
                )
            except Exception as exc:
                result.attachments.append(AttachmentResult(
                    filename=filename, status="failed", error=f"upload: {exc}"
                ))
                logger.error("direct_runner: upload failed %r: %s", filename, exc)
                continue

            # Queue extraction
            try:
                await svc.queue_extraction(file_id=file_id, kind=_EXTRACTION_KIND)
            except Exception as exc:
                result.attachments.append(AttachmentResult(
                    filename=filename, status="failed", file_id=file_id,
                    error=f"queue_extraction: {exc}"
                ))
                logger.error("direct_runner: queue_extraction failed file_id=%s: %s", file_id, exc)
                continue

            # Run extraction in fresh session
            try:
                async with AsyncSessionLocal() as ext_db:
                    await AuditService(ext_db).run_extraction_work(
                        file_id=file_id, kind=_EXTRACTION_KIND
                    )
            except Exception as exc:
                logger.error(
                    "direct_runner: run_extraction_work failed file_id=%s: %s", file_id, exc
                )

            result.attachments.append(AttachmentResult(
                filename=filename, status="success", file_id=file_id
            ))
            logger.info(
                "direct_runner: uploaded %r → file_id=%s company=%r cycle=%s",
                filename, file_id, company.name, review_cycle_id,
            )

    return result


# ---------------------------------------------------------------------------
# Runner entry point
# ---------------------------------------------------------------------------

async def _run_async(max_results: int, dry_run: bool) -> list[MessageResult]:
    service = await asyncio.to_thread(_build_gmail_service)
    gmail_ids = await asyncio.to_thread(
        _list_audited_financials_messages, service, max_results
    )
    logger.info("direct_runner: found %d Gmail message(s) matching pattern", len(gmail_ids))

    results: list[MessageResult] = []
    for gmail_id in gmail_ids:
        try:
            msg = await asyncio.to_thread(_fetch_message, service, gmail_id)
            r = await _process_message(service, gmail_id, msg, dry_run=dry_run)
            results.append(r)
        except Exception as exc:
            logger.exception("direct_runner: unhandled error for gmail_id=%s: %s", gmail_id, exc)

    return results


def _print_summary(results: list[MessageResult]) -> None:
    print("\n" + "=" * 60)
    print("DIRECT RUNNER SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"\nGmail ID : {r.gmail_id}")
        print(f"Subject  : {r.subject}")
        if r.skip_reason:
            print(f"SKIPPED  : {r.skip_reason}")
            continue
        print(f"Company  : {r.company_name} (id={r.company_id})")
        print(f"Entity ID: {r.entity_id or 'None (no match)'}")
        print(f"Cycle    : {r.review_cycle_id}")
        if not r.attachments:
            print("Attachments: none found")
        for att in r.attachments:
            icon = {"success": "✓", "skipped": "–", "failed": "✗", "dry_run": "~"}.get(att.status, "?")
            fid = f"  file_id={att.file_id}" if att.file_id else ""
            err = f"  [{att.error}]" if att.error else ""
            print(f"  {icon} {att.filename}{fid}{err}")
    print("=" * 60 + "\n")


def run_direct(max_results: int = 20, dry_run: bool = False) -> list[MessageResult]:
    """Public entry point for programmatic use."""
    results = asyncio.run(_run_async(max_results=max_results, dry_run=dry_run))
    _print_summary(results)
    return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Direct Gmail → audit pipeline runner (skips TempEmailHistory)"
    )
    parser.add_argument(
        "--max-results", type=int, default=20,
        help="Max Gmail messages to scan (default: 20)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and resolve only — do not upload or trigger extraction",
    )
    args = parser.parse_args()
    run_direct(max_results=args.max_results, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
