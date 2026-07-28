from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

import httpx
from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings
from src.db.models import ConfigTable, EmailHistory, File, PortfolioCompany
from src.services.company_audit_recorder import CompanyAuditRecorder, get_audit_actor_email
from src.utils.s3 import upload_file


def _attachment_in_app_storage(key: Optional[str], attach_path: str) -> bool:
    """True if the attachment key lives under the app's own email-attachment prefix.

    A successful classifier copy produces ``{AWS_S3_EMAIL_ATTACH_PATH}/{thread}/{file}``.
    If the copy fails (e.g. S3 AccessDenied on the source bucket) the row keeps the raw
    source key (``pf_portreview/...``), which the app can't download. That mismatch is the
    signal we surface to the UI.
    """
    k = (key or "").lstrip("/")
    base = (attach_path or "").strip().strip("/")
    return bool(base) and k.startswith(base + "/")


def _compute_email_diagnostics(eh: EmailHistory, email_files: list) -> dict:
    """Explain, read-only, why an audited-financials email does/doesn't have a file.

    Order of checks mirrors the pipeline so the *first* real blocker is reported.
    """
    # Lazy import avoids a circular import at module load.
    from src.scripts.data_manipulation.audited_financials_email_ingestion import (
        parse_audited_financials_subject,
    )

    attach_path = settings.AWS_S3_EMAIL_ATTACH_PATH
    attachments = list(eh.attachments or [])
    att_diags: list[dict] = []
    any_not_in_storage = False
    for key in attachments:
        in_app = _attachment_in_app_storage(key, attach_path)
        if not in_app:
            any_not_in_storage = True
        att_diags.append({
            "key": key,
            "filename": key.split("/")[-1] if key else key,
            "in_app_storage": in_app,
            "issue": None if in_app else (
                "Not copied into the app's storage — the copy from the source bucket failed "
                "(likely an S3 AccessDenied / IAM permission issue). The PDF can't be downloaded."
            ),
        })

    files = email_files or []
    usable = [f for f in files if (getattr(f, "status", "") or "").lower() not in ("failed", "error", "deleted")]
    failed = [f for f in files if (getattr(f, "status", "") or "").lower() in ("failed", "error")]

    if usable:
        status, code = "processed", "file_created"
        message = f"{len(usable)} file(s) created from this email's attachment(s)."
    elif failed:
        status, code = "file_failed", "extraction_failed"
        message = "A file was created but its extraction failed (status=failed). Re-run extraction or check the document."
    elif not attachments:
        status, code = "no_attachments", "no_attachments"
        message = "This email has no attachments, so there is nothing to extract."
    elif any_not_in_storage:
        status, code = "blocked", "attachment_not_in_storage"
        message = (
            "The attachment was never copied into the app's storage (S3 copy failed — likely an AWS "
            "AccessDenied / IAM permission issue on the source bucket). The PDF cannot be downloaded, "
            "so no file is created. This needs an S3/IAM fix — re-tagging will not help."
        )
    elif parse_audited_financials_subject(eh.subject or "") is None:
        status, code = "blocked", "subject_unparseable"
        message = (
            "The subject is not in a recognized audited-financials format, so it can't be auto-matched "
            "to a company/period. Use manual Tag & Extract, or rename to "
            "'Audited Financials_<Company>_<Entity>_<Mmm-YY>'."
        )
    else:
        status, code = "pending", "pending_extraction"
        message = (
            "Recognized as audited financials and the attachment is in storage, but extraction has not "
            "completed yet (queued, or not yet reached by the background job)."
        )

    return {
        "status": status,
        "reason_code": code,
        "message": message,
        "file_count": len(files),
        "attachments": att_diags,
    }


class EmailThreadsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _format_datetime_ist(self, dt: Optional[datetime]) -> str:
        """Format a datetime in IST for blockquote attribution, e.g. 'Mon, Jan 01, 2025 at 10:30 AM IST'."""
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist = pytz.timezone("Asia/Kolkata")
        dt_ist = dt.astimezone(ist)
        return dt_ist.strftime("%a, %b %d, %Y at %I:%M %p IST")

    def _extract_display_name(self, email: str) -> str:
        """Derive a display name from an email address (e.g. 'john.doe@x.com' → 'John Doe')."""
        if not email or "@" not in email:
            return email or ""
        username = email.split("@")[0]
        parts = [p for match in re.findall(r"([a-zA-Z]+)[._-]?([a-zA-Z]+)?", username) for p in match if p]
        return " ".join(parts).title() if parts else username.title()

    async def _upload_attachments(
        self,
        files: list[UploadFile],
        folder_id: str,
    ) -> list[str]:
        """Upload files to S3 and return their S3 keys."""
        keys: list[str] = []
        if not settings.AWS_S3_EMAIL_BUCKET:
            logger.warning("AWS_S3_EMAIL_BUCKET not configured; skipping attachment upload")
            return keys
        for f in files:
            if not f.filename:
                continue
            safe_name = re.sub(r"[^a-zA-Z0-9.\-_]+", "_", f.filename.replace(" ", "_"))[:180]
            key = f"{settings.AWS_S3_EMAIL_ATTACH_PATH}/{folder_id}/{safe_name}"
            data = await f.read()
            upload_file(
                data,
                key,
                content_type=f.content_type or "binary/octet-stream",
                bucket=settings.AWS_S3_EMAIL_BUCKET,
                bucket_env_var_name="AWS_S3_EMAIL_BUCKET",
            )
            keys.append(key)
        return keys

    async def get_company_contacts(self, *, portfolio_company_id: int) -> dict:
        """Return poc_email_ids and poc_cc_email_ids stored on the company record."""
        result = await self.db.execute(
            select(PortfolioCompany).where(PortfolioCompany.id == portfolio_company_id)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise ValueError("Company not found")
        return {
            "poc_email_ids": company.poc_email_ids or [],
            "poc_cc_email_ids": company.poc_cc_email_ids or [],
        }

    _SUGGESTED_EMAILS_KEY = "suggested_emails"

    async def get_suggested_emails(self) -> list[str]:
        """Return the global suggested-email list stored in config_table."""
        result = await self.db.execute(
            select(ConfigTable).where(ConfigTable.key == self._SUGGESTED_EMAILS_KEY)
        )
        row = result.scalar_one_or_none()
        if not row:
            return []
        return row.value.get("emails", [])

    async def put_suggested_emails(self, emails: list[str]) -> list[str]:
        """Overwrite the global suggested-email list. Creates the row if it doesn't exist."""
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped = [e for e in emails if e and not (e in seen or seen.add(e))]  # type: ignore[func-returns-value]

        result = await self.db.execute(
            select(ConfigTable).where(ConfigTable.key == self._SUGGESTED_EMAILS_KEY)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = {"emails": deduped}
        else:
            row = ConfigTable(
                key=self._SUGGESTED_EMAILS_KEY,
                value={"emails": deduped},
                description="Global email addresses suggested in every compose/reply form",
            )
            self.db.add(row)
        await self.db.commit()
        return deduped

    async def list_threads_for_company(self, *, portfolio_company_id: int) -> list[dict]:
        result = await self.db.execute(
            select(EmailHistory)
            .where(EmailHistory.portfolio_company_id == portfolio_company_id)
            .order_by(EmailHistory.sent_at.asc().nullsfirst(), EmailHistory.created_at.asc())
        )
        emails = list(result.scalars().all())

        threads: dict[str, dict] = {}
        for e in emails:
            tid = e.thread_id or "no_thread"
            if tid not in threads:
                threads[tid] = {
                    "thread_id": tid,
                    "latest_sent_at": e.sent_at,
                    "subject": e.subject,
                    "email_count": 0,
                    "emails": [],
                }
            threads[tid]["email_count"] += 1
            if e.sent_at and (not threads[tid]["latest_sent_at"] or e.sent_at > threads[tid]["latest_sent_at"]):
                threads[tid]["latest_sent_at"] = e.sent_at
            if e.subject and not threads[tid]["subject"]:
                threads[tid]["subject"] = e.subject
            threads[tid]["emails"].append(e)

        # Sort threads by latest_sent_at desc
        out = list(threads.values())
        out.sort(key=lambda x: x["latest_sent_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return out

    async def list_untagged_threads(self) -> list[dict]:
        """Return lightweight thread summaries (no body) for the last 60 days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)
        result = await self.db.execute(
            select(
                EmailHistory.id,
                EmailHistory.thread_id,
                EmailHistory.subject,
                EmailHistory.sender,
                EmailHistory.sent_at,
                EmailHistory.created_at,
            )
            .where(
                EmailHistory.portfolio_company_id.is_(None),
                EmailHistory.sent_at.is_not(None),
                EmailHistory.sent_at >= cutoff,
            )
            .order_by(EmailHistory.sent_at.desc().nullslast(), EmailHistory.created_at.desc())
        )
        rows = result.all()

        threads: dict[str, dict] = {}
        for row in rows:
            tid = row.thread_id or f"__no_thread__{row.id}"
            if tid not in threads:
                threads[tid] = {
                    "thread_id": tid,
                    "subject": row.subject,
                    "sender": row.sender,
                    "latest_sent_at": row.sent_at,
                    "email_count": 0,
                }
            t = threads[tid]
            t["email_count"] += 1
            if row.sent_at and (not t["latest_sent_at"] or row.sent_at > t["latest_sent_at"]):
                t["latest_sent_at"] = row.sent_at
                t["sender"] = row.sender
            if row.subject and not t["subject"]:
                t["subject"] = row.subject

        out = list(threads.values())
        out.sort(key=lambda x: x["latest_sent_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return out

    async def get_untagged_thread_detail(self, thread_id: str) -> list[dict] | None:
        """Return full emails (with bodies) for a single thread_id."""
        if thread_id.startswith("__no_thread__"):
            # Threadless single message stored under a sentinel id
            message_id = thread_id.removeprefix("__no_thread__")
            result = await self.db.execute(
                select(EmailHistory).where(EmailHistory.id == message_id)
            )
        else:
            result = await self.db.execute(
                select(EmailHistory).where(EmailHistory.thread_id == thread_id)
                .order_by(EmailHistory.sent_at.asc().nullsfirst(), EmailHistory.created_at.asc())
            )
        emails = list(result.scalars().all())
        if not emails:
            return None
        return emails

    async def check_attachment_storage(self, email_id: str) -> dict:
        """LIVE S3 check: actually attempt to read each of this email's attachments and return
        AWS's verbatim answer (accessible / AccessDenied / NoSuchKey).

        This replicates exactly what the ingestion download does
        (``head_object`` on ``AWS_S3_EMAIL_BUCKET`` with the stored key). Because the classifier
        copy also reads from that same bucket, a 403 here is the same AccessDenied the copy hit —
        so the result is definitive, not inferred. Runs with the deployed app's IAM identity, so
        call it against the environment where the problem is (prod).
        """
        import asyncio

        from botocore.exceptions import ClientError, NoCredentialsError

        from src.utils.s3 import get_s3_client

        eh = (
            await self.db.execute(select(EmailHistory).where(EmailHistory.id == email_id))
        ).scalar_one_or_none()
        if eh is None:
            raise ValueError(f"Email {email_id!r} not found")

        bucket = settings.AWS_S3_EMAIL_BUCKET
        keys = list(eh.attachments or [])

        def _probe(key: str) -> dict:
            try:
                resp = get_s3_client().head_object(Bucket=bucket, Key=key)
                return {
                    "key": key, "bucket": bucket, "accessible": True,
                    "size_bytes": resp.get("ContentLength"),
                    "aws_error_code": None, "aws_message": None,
                    "verdict": "OK — the app's IAM role CAN read this object.",
                }
            except ClientError as e:
                err = e.response.get("Error", {}) if hasattr(e, "response") else {}
                code = err.get("Code")
                http = (e.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode")
                msg = err.get("Message")
                if code in ("AccessDenied", "403") or http == 403:
                    verdict = (
                        "AccessDenied — the app's IAM role is NOT permitted to read this object "
                        "(s3:GetObject). THIS is the AWS/IAM permission issue; fix the role policy."
                    )
                elif code in ("NoSuchKey", "404") or http == 404:
                    verdict = (
                        "NoSuchKey — no object exists at this key in the bucket "
                        "(the copy from the source bucket never produced this file)."
                    )
                elif code == "NoSuchBucket":
                    verdict = f"NoSuchBucket — bucket {bucket!r} does not exist / is misconfigured."
                else:
                    verdict = f"S3 error {code or http}."
                return {
                    "key": key, "bucket": bucket, "accessible": False, "size_bytes": None,
                    "aws_error_code": code or (str(http) if http else None),
                    "aws_message": msg, "verdict": verdict,
                }
            except NoCredentialsError as e:
                return {
                    "key": key, "bucket": bucket, "accessible": False, "size_bytes": None,
                    "aws_error_code": "NoCredentials", "aws_message": str(e),
                    "verdict": "No AWS credentials available to the app.",
                }
            except Exception as e:  # noqa: BLE001 — surface anything else verbatim
                return {
                    "key": key, "bucket": bucket, "accessible": False, "size_bytes": None,
                    "aws_error_code": type(e).__name__, "aws_message": str(e),
                    "verdict": f"Unexpected error probing S3: {e}",
                }

        probes = [await asyncio.to_thread(_probe, k) for k in keys]

        if not keys:
            overall, summary = "no_attachments", "This email has no attachments to check."
        elif all(p["accessible"] for p in probes):
            overall = "all_accessible"
            summary = "All attachments are readable by the app — storage is NOT the problem."
        elif any(p["aws_error_code"] in ("AccessDenied", "403") for p in probes):
            overall = "access_denied"
            summary = (
                "CONFIRMED AWS/IAM issue: the app's role is denied s3:GetObject on the attachment(s). "
                "Grant the role read access to fix it — re-tagging will not help."
            )
        elif any(p["aws_error_code"] in ("NoSuchKey", "404") for p in probes):
            overall = "missing"
            summary = "The attachment object is missing from the bucket (it was never copied in)."
        else:
            overall = "mixed"
            summary = "See per-attachment verdicts below."

        return {
            "email_id": email_id, "bucket": bucket,
            "overall": overall, "summary": summary, "probes": probes,
        }

    async def list_audited_financials_emails(self) -> list[dict]:
        """Return all inbound emails classified as carrying audited financials.

        Two conditions (OR):
        - ``email_type == 'audited_financials'`` — catches user-tagged emails with
          non-conventional subjects (e.g. "Q4 financials attached") where the ingestion
          pipeline set the type explicitly.
        - ``subject ILIKE '%Audited Financials_%'`` — catches the conventional naming
          pattern including Re:/Fwd: reply prefixes from any mail client.

        Each result includes only files created from that email's attachments
        (matched via source_email_id FK on the files table).
        """
        result = await self.db.execute(
            select(EmailHistory, PortfolioCompany.name)
            .outerjoin(PortfolioCompany, EmailHistory.portfolio_company_id == PortfolioCompany.id)
            .where(
                (EmailHistory.email_type == "audited_financials")
                | EmailHistory.subject.ilike("%Audited Financials%")
            )
            .order_by(EmailHistory.sent_at.desc().nullslast())
        )
        rows = result.all()

        # Collect email IDs so we can fetch their linked files in bulk
        email_ids: list[str] = [eh.id for eh, _ in rows]

        # Fetch all files linked to these emails via source_email_id in one query
        files_by_email: dict[str, list[File]] = {}
        if email_ids:
            files_result = await self.db.execute(
                select(File)
                .where(
                    File.source_email_id.in_(email_ids),
                    File.status != "deleted",
                )
                .order_by(File.id.desc())
            )
            for f in files_result.scalars().all():
                files_by_email.setdefault(f.source_email_id, []).append(f)

        out: list[dict] = []
        for eh, company_name in rows:
            # classified_by reflects how the email got its email_type:
            # - "system": the subject matched the Audited Financials pattern automatically
            #   (portfolio_company_id may be set by ingestion pipeline, not by a human action)
            # - "user": explicitly tagged via UI (tag_thread / tag_email_with_entity)
            # We use the tagged_by field if present; fall back to email_type heuristic.
            tagged_by = getattr(eh, "tagged_by", None)
            if tagged_by in ("user", "system"):
                classified_by = tagged_by
            elif eh.portfolio_company_id is not None and eh.email_type == "audited_financials":
                # email_type was set explicitly — could be user OR system ingestion
                # Use presence of conventional subject as the discriminator: if subject
                # matches pattern the system classified it; otherwise the user tagged it.
                import re as _re
                _AF_PATTERN = _re.compile(r"audited\s+financials[_ ]", _re.IGNORECASE)
                classified_by = "system" if _AF_PATTERN.search(eh.subject or "") else "user"
            else:
                classified_by = "system" if eh.portfolio_company_id is None else "user"

            email_files = files_by_email.get(eh.id, [])
            out.append(
                {
                    "id": eh.id,
                    "thread_id": eh.thread_id,
                    "portfolio_company_id": eh.portfolio_company_id,
                    "portfolio_company_name": company_name,
                    "sender": eh.sender,
                    "subject": eh.subject,
                    "email_type": eh.email_type,
                    "sent_at": eh.sent_at,
                    "is_inbound": eh.is_inbound,
                    "attachments": eh.attachments,
                    "classified_by": classified_by,
                    "files": email_files,
                    "diagnostics": _compute_email_diagnostics(eh, email_files),
                }
            )
        return out

    async def tag_email_with_entity(
        self,
        *,
        email_id: str,
        portfolio_company_id: int,
        entity_id: Optional[int] = None,
        review_cycle_id: Optional[str] = None,
        force_reprocess: bool = False,
    ) -> dict:
        """Tag a single audited-financials email to a company + optional entity and trigger
        attachment extraction immediately.

        Unlike ``tag_thread`` (which resolves company/entity from the email subject), this
        method accepts explicit IDs supplied by the user — so it works for both conventional
        ``Audited Financials_...`` subjects and non-conventional ones.

        Steps:
        1. Set ``portfolio_company_id`` on the ``EmailHistory`` row.
        2. Set ``email_type = 'audited_financials'`` so it is always surfaced in the
           audited-financials view.
        3. For each attachment S3 key, download → create ``File`` record → queue + run
           extraction, exactly as the scheduler does.

        Returns the number of attachments successfully processed.
        """
        from src.db.models import PortfolioCompany as _PC
        from src.db.session import AsyncSessionLocal
        from src.scripts.data_manipulation.audited_financials_email_ingestion import (
            _process_attachment,
            _get_processed_attachment_keys,
        )

        # Fetch the email
        result = await self.db.execute(
            select(EmailHistory).where(EmailHistory.id == email_id)
        )
        eh = result.scalar_one_or_none()
        if eh is None:
            raise ValueError(f"Email {email_id!r} not found")

        # Fetch the portfolio company (needed as ORM object for _process_attachment)
        pc_result = await self.db.execute(
            select(_PC).where(_PC.id == portfolio_company_id)
        )
        portfolio_company = pc_result.scalar_one_or_none()
        if portfolio_company is None:
            raise ValueError(f"Portfolio company {portfolio_company_id} not found")

        # 1 + 2. Tag email to company and mark email_type
        await self.db.execute(
            update(EmailHistory)
            .where(EmailHistory.id == email_id)
            .values(
                portfolio_company_id=portfolio_company_id,
                email_type="audited_financials",
                updated_on=func.now(),
            )
        )
        await self.db.commit()

        # 3. Process attachments in a dedicated session
        attachments = list(eh.attachments or [])
        if not attachments:
            logger.info(
                "tag_email_with_entity: email id=%s has no attachments — skipping extraction",
                email_id,
            )
            return {"attachments_processed": 0, "review_cycle_id": review_cycle_id}

        attachments_processed = 0
        attachment_results: list[dict] = []
        async with AsyncSessionLocal() as ingest_db:
            # Snapshot portfolio company into the new session
            pc_result2 = await ingest_db.execute(
                select(_PC).where(_PC.id == portfolio_company_id)
            )
            pc = pc_result2.scalar_one_or_none()
            if pc is None:
                raise ValueError(f"Portfolio company {portfolio_company_id} not found in ingest session")

            try:
                already_processed = await _get_processed_attachment_keys(ingest_db)
            except Exception:
                # Checkpoint table may not exist in all environments; safe to skip
                already_processed = set()
            for s3_key in attachments:
                try:
                    att_result = await _process_attachment(
                        db=ingest_db,
                        s3_key=s3_key,
                        portfolio_company=pc,
                        entity_id=entity_id,
                        review_cycle_id=review_cycle_id or "",
                        already_processed=already_processed,
                        email_id=email_id,
                        force_reprocess=force_reprocess,
                    )
                    already_processed.add(s3_key)
                    if att_result.status == "success":
                        attachments_processed += 1
                    attachment_results.append({
                        "s3_key": att_result.s3_key,
                        "filename": att_result.filename,
                        "status": att_result.status,
                        "file_id": att_result.file_id,
                        "error": att_result.error,
                        "skip_reason": att_result.skip_reason,
                    })
                    logger.info(
                        "tag_email_with_entity: s3_key=%s status=%s file_id=%s",
                        s3_key, att_result.status, att_result.file_id,
                    )
                except Exception as exc:
                    logger.exception(
                        "tag_email_with_entity: attachment failed s3_key=%s: %s", s3_key, exc
                    )
                    attachment_results.append({
                        "s3_key": s3_key,
                        "filename": s3_key.split("/")[-1] if s3_key else s3_key,
                        "status": "failed",
                        "file_id": None,
                        "error": str(exc),
                        "skip_reason": None,
                    })
            await ingest_db.commit()

        return {
            "attachments_processed": attachments_processed,
            "review_cycle_id": review_cycle_id,
            "attachment_results": attachment_results,
        }

    async def tag_thread(
        self,
        *,
        portfolio_company_id: int,
        thread_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> dict:
        """
        Tag an email thread (or single message) with a portfolio company.

        Priority: thread_id > message_id. When message_id is given, the entire
        thread sharing that message's thread_id is updated; if the message has no
        thread_id, only that single message is updated.

        For emails whose subject matches the ``Audited Financials_*`` pattern the
        method also:
          - sets ``company_id`` / ``company_pr_cycle_id`` on the EmailHistory rows
          - creates File records and triggers extraction for every attachment

        Returns a dict with keys: thread_id, affected_rows.
        """
        update_values = {
            "portfolio_company_id": portfolio_company_id,
            "updated_on": func.now(),
        }

        resolved_thread_id: Optional[str] = None
        affected_rows = 0
        tagged_email_ids: list[str] = []

        if thread_id:
            stmt = (
                update(EmailHistory)
                .where(EmailHistory.thread_id == thread_id)
                .values(**update_values)
                .returning(EmailHistory.id)
            )
            result = await self.db.execute(stmt)
            rows = result.fetchall()
            resolved_thread_id = thread_id
            affected_rows = len(rows)
            tagged_email_ids = [r[0] for r in rows]

        elif message_id:
            # Resolve thread_id from the given message
            lookup = await self.db.execute(
                select(EmailHistory.thread_id).where(EmailHistory.id == message_id)
            )
            row = lookup.fetchone()

            if row and row.thread_id:
                # Update the full thread
                stmt = (
                    update(EmailHistory)
                    .where(EmailHistory.thread_id == row.thread_id)
                    .values(**update_values)
                    .returning(EmailHistory.id)
                )
                result = await self.db.execute(stmt)
                rows = result.fetchall()
                resolved_thread_id = row.thread_id
                affected_rows = len(rows)
                tagged_email_ids = [r[0] for r in rows]
            else:
                # Single orphaned message with no thread_id
                stmt = (
                    update(EmailHistory)
                    .where(EmailHistory.id == message_id)
                    .values(**update_values)
                    .returning(EmailHistory.id)
                )
                result = await self.db.execute(stmt)
                rows = result.fetchall()
                affected_rows = len(rows)
                tagged_email_ids = [r[0] for r in rows]

        if affected_rows == 0:
            raise ValueError("No emails found to tag")

        await self.db.commit()

        # For Audited Financials emails, trigger the ingestion pipeline so that
        # File records are created and extraction is queued — exactly what the
        # scheduler does when the email arrives automatically.
        if tagged_email_ids:
            await self._ingest_audited_financials_from_tagged(tagged_email_ids)

        return {"thread_id": resolved_thread_id, "affected_rows": affected_rows}

    async def _ingest_audited_financials_from_tagged(self, email_ids: list[str]) -> None:
        """For each tagged EmailHistory row that looks like an Audited Financials email
        (subject matches ``Audited Financials_*`` and has attachments), run the full
        audited-financials attachment pipeline so that File records are created and
        extraction is triggered.

        We call the ingestion helpers directly instead of going through _process_one_email
        because the EmailHistory row already exists — the classifier created it — and we
        only need to resolve company/entity, handle the EmailHistory field updates, and
        process each attachment.
        """
        from src.db.session import AsyncSessionLocal
        from src.scripts.data_manipulation.audited_financials_email_ingestion import (
            parse_audited_financials_subject,
            resolve_company,
            resolve_entity,
            safe_legacy_refs,
            _process_attachment,
            _get_processed_attachment_keys,
        )
        from src.services.fy_end import normalize_fy_end, resolve_review_cycle_id_for_fy_end

        # Fetch the full EmailHistory rows we just tagged
        result = await self.db.execute(
            select(EmailHistory).where(EmailHistory.id.in_(email_ids))
        )
        email_rows: list[EmailHistory] = list(result.scalars().all())

        for eh in email_rows:
            subject = (eh.subject or "").strip()
            parsed = parse_audited_financials_subject(subject)
            if not parsed:
                continue
            attachments = list(eh.attachments or [])
            if not attachments:
                logger.info(
                    "tag_thread: Audited Financials email id=%s has no attachments — skipping ingestion",
                    eh.id,
                )
                continue

            logger.info(
                "tag_thread: triggering audited_financials ingestion for email id=%s subject=%r",
                eh.id,
                subject,
            )
            try:
                async with AsyncSessionLocal() as ingest_db:
                    # 1. Normalize FY end and resolve review cycle
                    fy_end = normalize_fy_end(parsed.fy_end_raw)
                    if not fy_end:
                        logger.error(
                            "tag_thread: invalid fy_end %r for email id=%s — skipping",
                            parsed.fy_end_raw, eh.id,
                        )
                        continue
                    try:
                        review_cycle_id = await resolve_review_cycle_id_for_fy_end(ingest_db, fy_end)
                    except Exception as exc:
                        logger.error(
                            "tag_thread: cannot resolve review_cycle for fy_end=%r email id=%s: %s",
                            fy_end, eh.id, exc,
                        )
                        continue

                    # 2. Resolve company (mandatory)
                    company = await resolve_company(ingest_db, parsed.company_name, review_cycle_id)
                    if company is None:
                        logger.error(
                            "tag_thread: company not found name=%r cycle=%r for email id=%s",
                            parsed.company_name, review_cycle_id, eh.id,
                        )
                        continue

                    # Snapshot values from the ORM object before switching sessions
                    eh_id = eh.id
                    company_pk = company.id
                    company_ext_id = company.company_id

                    # 3. Patch EmailHistory with company_id and cycle — best-effort; use a raw
                    #    UPDATE so we never attach the cross-session ORM object.  Legacy refs are
                    #    pre-validated against the portfolioreview FK targets so the email_type
                    #    tag is never lost to a FK violation.
                    safe_company_id, safe_cycle_id = await safe_legacy_refs(
                        ingest_db, company_id=company_ext_id, review_cycle_id=review_cycle_id
                    )
                    patch_values: dict = {"email_type": "audited_financials"}
                    if safe_company_id:
                        patch_values["company_id"] = safe_company_id
                    if safe_cycle_id:
                        patch_values["company_pr_cycle_id"] = safe_cycle_id
                    try:
                        await ingest_db.execute(
                            update(EmailHistory)
                            .where(EmailHistory.id == eh_id)
                            .values(**patch_values)
                        )
                        await ingest_db.commit()
                        logger.info(
                            "tag_thread: patched EmailHistory id=%s with company_id=%s cycle=%s",
                            eh_id, company_ext_id, review_cycle_id,
                        )
                    except Exception as patch_exc:
                        await ingest_db.rollback()
                        logger.warning(
                            "tag_thread: could not patch company_id/cycle on EmailHistory id=%s "
                            "(FK constraint or other error): %s — continuing with attachment processing",
                            eh_id, patch_exc,
                        )

                    # 4. Resolve entity (optional)
                    entity = await resolve_entity(ingest_db, company_pk, parsed.entity_name, review_cycle_id)
                    entity_id = entity.id if entity is not None else None

                    # 5. Process attachments
                    already_processed = await _get_processed_attachment_keys(ingest_db)
                    for s3_key in attachments:
                        try:
                            att_result = await _process_attachment(
                                db=ingest_db,
                                s3_key=s3_key,
                                portfolio_company=company,
                                entity_id=entity_id,
                                review_cycle_id=review_cycle_id,
                                already_processed=already_processed,
                                fy_end=fy_end,
                                email_id=eh_id,
                            )
                            already_processed.add(s3_key)
                            logger.info(
                                "tag_thread: attachment s3_key=%s status=%s file_id=%s",
                                s3_key, att_result.status, att_result.file_id,
                            )
                        except Exception as att_exc:
                            logger.exception(
                                "tag_thread: attachment processing failed s3_key=%s: %s",
                                s3_key, att_exc,
                            )

                    await ingest_db.commit()
                    logger.info(
                        "tag_thread: audited_financials ingestion completed for email id=%s",
                        eh.id,
                    )
            except Exception as exc:
                logger.exception(
                    "tag_thread: audited_financials ingestion failed for email id=%s: %s",
                    eh.id,
                    exc,
                )

    async def send_email(
        self,
        *,
        portfolio_company_id: int,
        to_addrs: list[str],
        cc_addrs: list[str],
        subject: str,
        body_html: str,
        thread_id: Optional[str],
        reply_to_message_id: Optional[str],
        attachment_keys: list[str],
        attachments_id: Optional[str],
        new_attachments: Optional[list[UploadFile]] = None,
        template_id: Optional[str] = None,
        affected_entity_ids: Optional[list[int]] = None,
    ) -> tuple[str, str]:
        if not settings.EMAIL_BASE_URL:
            raise ValueError("EMAIL_BASE_URL is not configured")

        # Ensure company exists (and optionally default TO).
        company_result = await self.db.execute(
            select(PortfolioCompany).where(PortfolioCompany.id == portfolio_company_id)
        )
        company = company_result.scalar_one_or_none()
        if not company:
            raise ValueError("Company not found")

        if not to_addrs:
            if company.contact_email_id:
                to_addrs = [company.contact_email_id]
        if not to_addrs:
            raise ValueError("No recipients (to_addrs) provided")

        # Assign thread/message ids.
        tid = thread_id or str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        # Upload any new inline attachments to S3, merging keys with pre-existing ones.
        folder_id = attachments_id or str(uuid.uuid4())
        if new_attachments:
            new_keys = await self._upload_attachments(new_attachments, folder_id)
            attachment_keys = list(attachment_keys or []) + new_keys
            attachments_id = folder_id

        # If replying, wrap body with Gmail-style quoted history block.
        body_to_send = body_html
        if reply_to_message_id:
            prev_result = await self.db.execute(
                select(EmailHistory).where(EmailHistory.id == reply_to_message_id)
            )
            prev = prev_result.scalar_one_or_none()
            if prev:
                prev_sent = self._format_datetime_ist(prev.sent_at)
                sender_email = prev.sender or ""
                sender_name = self._extract_display_name(sender_email)
                body_to_send = (
                    f'<div dir="ltr">'
                    f"<div>{body_html}</div>"
                    f"<br/>"
                    f'<div class="gmail_attr">On {prev_sent}, {sender_name} &lt;{sender_email}&gt; wrote:</div>'
                    f'<blockquote class="gmail_quote" style="margin:0 0 0 0.8ex;border-left:1px solid #ccc;padding-left:1ex">'
                    f"{prev.body or ''}"
                    f"</blockquote>"
                    f"</div>"
                )
                tid = prev.thread_id or tid

        api_payload = {
            "data": {
                "to_addrs": to_addrs,
                "cc": cc_addrs,
                "from_addrs": settings.BASE_FROM_EMAIL,
                "subject": subject,
                "body_html": body_to_send,
                "attachmentsId": (
                    f"{settings.AWS_S3_EMAIL_ATTACH_PATH}/{attachments_id}"
                    if attachments_id
                    else ""
                ),
            }
        }

        if settings.LOCAL_DEV:
            logger.info(
                "LOCAL_DEV: skipping external email API call (would POST to %s). "
                "Payload: to=%s subject=%r",
                settings.EMAIL_BASE_URL,
                to_addrs,
                subject,
            )
        else:
            async with httpx.AsyncClient(timeout=settings.EXTERNAL_API_TIMEOUT) as client:
                resp = await client.post(settings.EMAIL_BASE_URL, json=api_payload)
                resp.raise_for_status()

        # Store history
        now = datetime.now(timezone.utc)
        row = EmailHistory(
            id=message_id,
            thread_id=tid,
            portfolio_company_id=portfolio_company_id,
            sender=settings.BASE_FROM_EMAIL,
            recipients=to_addrs,
            cc=cc_addrs,
            subject=subject,
            body=body_to_send,
            body_html=body_to_send,
            email_type="manual",
            attachments=attachment_keys or [],
            sent_at=now,
            is_inbound=False,
            status=0,
            associated_query_ids=[],
            bcc=[],
            reply_to=None,
            template_id=template_id,
            affected_entity_ids=affected_entity_ids or [],
            created_at=now,
            updated_on=now,
        )
        self.db.add(row)
        actor = get_audit_actor_email() or settings.BASE_FROM_EMAIL
        to_display = ", ".join(to_addrs[:3])
        if len(to_addrs) > 3:
            to_display += f" (+{len(to_addrs) - 3} more)"
        await CompanyAuditRecorder(self.db).log_company(
            portfolio_company_id=portfolio_company_id,
            action=f'Email sent to {to_display} with subject "{subject}" by {actor}',
            meta={
                "event": "email.sent",
                "thread_id": tid,
                "message_id": message_id,
                "to": to_addrs,
                "cc": cc_addrs,
                "subject": subject,
            },
            actor_email=actor,
        )
        await self.db.commit()
        await self.db.refresh(row)
        return tid, message_id

