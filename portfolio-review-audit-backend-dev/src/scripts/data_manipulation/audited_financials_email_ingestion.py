"""Audited-financials email ingestion side-flow.

Scans TempEmailHistory rows whose subject starts with "Audited Financials",
uploads each attachment into the audit-file pipeline, starts extraction with
kind="audit_financials", and tags the resulting EmailHistory row.

This runs independently of the main email classifier (separate checkpoint key).
A failure in this flow never affects the classifier's checkpoint or DB state.

Checkpoint key: ``audited_financials_email_ingestion``
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.db.models import (
    EmailHistory,
    EmailProcessingCheckPoint,
    PortfolioCompany,
    Entity,
    TempEmailHistory,
)
from src.db.session import get_sync_db
from src.scripts.data_manipulation.classifying_incoming_emails import clean_subject
from src.services.audit_service import (
    AuditService,
    ALLOWED_AUDIT_UPLOAD_EXTENSIONS,
    MAX_AUDIT_UPLOAD_BYTES,
    validate_audit_upload_filename,
)
from src.services.fy_end import (
    coerce_fy_end_token,
    normalize_fy_end,
    resolve_review_cycle_id_for_fy_end,
)
from src.utils.s3 import download_storage_uri

logger = logging.getLogger(__name__)

_CHECKPOINT_KEY = "audited_financials_email_ingestion"
_SUBJECT_PREFIX = "Audited Financials"
_EXTRACTION_KIND = "audit_financials"


@asynccontextmanager
async def fresh_ingestion_session():
    """Yield an AsyncSession on a private NullPool engine bound to the CURRENT loop.

    The global AsyncSessionLocal engine's connection pool binds to the event loop
    it first ran on (uvicorn's main loop). The classifier bridge executes this
    pipeline via asyncio.run() inside a worker thread — a different loop — where
    pooled connections raise "attached to a different loop". A throwaway engine
    created inside the running loop is safe everywhere; ingestion volume is low
    enough that skipping the pool costs nothing.
    """
    from src.db.session import DATABASE_URL, _async_connect_args

    eng = create_async_engine(DATABASE_URL, poolclass=NullPool, connect_args=_async_connect_args)
    maker = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

@dataclass
class AuditedFinancialsSubject:
    company_name: str
    entity_name: str
    fy_end_raw: str


import re as _re
_PERIOD_WRAPPER_RE = _re.compile(r"^[Pp]eriod\((.+)\)$")

# Tolerant "Audited Financials" keyword: case-insensitive, allows the "Financial" spelling and
# an optional "Statement(s)" suffix, and any inter-word whitespace. clean_subject() has already
# stripped RE:/FW:/[External] prefixes before this is applied.
_AF_KEYWORD_RE = _re.compile(
    r"^audited\s+financ(?:e|ial)s?(?:\s+statements?)?", _re.IGNORECASE
)

# Common legal-entity suffixes, longest-first so multi-word suffixes strip before single tokens.
_LEGAL_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "private limited", "pvt ltd", "pvt limited", "pte ltd", "pte limited",
            "sdn bhd", "co ltd", "company limited", "limited", "ltd", "llp", "llc",
            "incorporated", "inc", "corporation", "corp", "company", "co",
            "gmbh", "plc", "ag", "sa", "bv", "nv", "srl", "sas", "pte", "bhd",
        ),
        key=len,
        reverse=True,
    )
)


def _norm_name(s: object) -> str:
    """Case- and whitespace-insensitive form for tolerant name matching."""
    return _re.sub(r"\s+", " ", str(s or "").strip().lower())


def _strip_legal_suffixes(t: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in _LEGAL_SUFFIXES:
            if t == suf:
                return ""
            if t.endswith(" " + suf):
                t = t[: -(len(suf) + 1)].strip()
                changed = True
                break
    return t


def _norm_name_loose(s: object) -> str:
    """Looser form: drop punctuation + trailing legal suffixes (e.g. 'Pte Ltd', 'Limited')."""
    t = _re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    t = _re.sub(r"\s+", " ", t).strip()
    return _strip_legal_suffixes(t)


# Every word that appears in any legal suffix, so they can be ignored during token matching.
_LEGAL_SUFFIX_TOKENS: frozenset[str] = frozenset(
    tok for suf in _LEGAL_SUFFIXES for tok in suf.split()
)


def _significant_tokens(s: object) -> set[str]:
    """Word tokens of a name, punctuation removed and legal-suffix words dropped.

    Used as a last-resort matcher so 'manatal' matches 'Manatal Pte Ltd'. Always paired with
    an ambiguity guard at the call site so a partial match never silently maps to >1 company.
    """
    t = _re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    return {tok for tok in t.split() if tok and tok not in _LEGAL_SUFFIX_TOKENS}


def parse_audited_financials_subject(subject: Optional[str]) -> Optional[AuditedFinancialsSubject]:
    """Return parsed fields from an audited-financials subject line, or None if not matching.

    Expected shape: ``Audited Financials_<Deal Name>_<Legal Entity Name>_<Period>``.

    The format is matched tolerantly:
      * keyword tolerates ``Financial``/``Statements`` and reply/forward prefixes (via
        ``clean_subject``);
      * the **period** is the right-most ``_`` field that parses as a financial year
        (``Mar-24``, ``Mar-2024``, ``March 2024``, ``Period(Mar-25)`` …); trailing non-period
        fields such as ``_v2`` are ignored;
      * the Deal Name field may be **omitted** when it equals the entity, so a two-field
        ``Audited Financials_<Entity>_<Period>`` is accepted and the entity doubles as the
        company name.

    ``fy_end_raw`` is returned already normalised to canonical ``Mmm-YY``.
    """
    if not subject:
        return None
    # Strip reply/forward/external prefixes (RE:, FW:, FWD:, [External], [EXT], …) using the
    # same cleaner the classifier uses, so a forwarded/replied audited-financials mail still parses.
    s = clean_subject(subject)
    km = _AF_KEYWORD_RE.match(s)
    if not km:
        return None
    # The remainder after the keyword must be the '_'-delimited fields.
    rest = s[km.end():]
    if not rest.startswith("_"):
        return None
    fields = [f.strip() for f in rest[1:].split("_")]
    fields = [f for f in fields if f]
    if not fields:
        return None

    # Identify the period as the right-most field that parses as a financial year.
    period_idx: Optional[int] = None
    fy_end_canonical: Optional[str] = None
    for i in range(len(fields) - 1, -1, -1):
        candidate = fields[i]
        wrap = _PERIOD_WRAPPER_RE.match(candidate)
        if wrap:
            candidate = wrap.group(1).strip()
        coerced = coerce_fy_end_token(candidate)
        if coerced:
            period_idx, fy_end_canonical = i, coerced
            break
    if period_idx is None or fy_end_canonical is None:
        logger.info(
            "audited_financials: subject has the audited-financials keyword but no parseable "
            "period field — skipping: %r",
            subject,
        )
        return None

    name_fields = fields[:period_idx]
    if not name_fields:
        logger.info(
            "audited_financials: subject missing company/entity field — skipping: %r", subject
        )
        return None
    if len(name_fields) == 1:
        # Single field → deal name and entity are the same (e.g. 'Audited Financials_Manatal Pte. Ltd_Mar-24').
        company_name = entity_name = name_fields[0]
    else:
        company_name = name_fields[0]
        entity_name = "_".join(name_fields[1:]).strip()

    if not company_name or not entity_name:
        return None
    return AuditedFinancialsSubject(
        company_name=company_name,
        entity_name=entity_name,
        fy_end_raw=fy_end_canonical,
    )


# ---------------------------------------------------------------------------
# Checkpoint helpers (async)
# ---------------------------------------------------------------------------

async def _get_last_processed_id(db: AsyncSession) -> int:
    row = (
        await db.execute(
            select(EmailProcessingCheckPoint).where(
                EmailProcessingCheckPoint.checkpoint_key == _CHECKPOINT_KEY
            )
        )
    ).scalar_one_or_none()
    if row is None:
        new_ck = EmailProcessingCheckPoint(
            checkpoint_key=_CHECKPOINT_KEY,
            checkpoint_value={"last_processed_id": 0},
        )
        db.add(new_ck)
        await db.commit()
        return 0
    try:
        return int((row.checkpoint_value or {}).get("last_processed_id", 0))
    except (TypeError, ValueError):
        return 0


async def _update_checkpoint(db: AsyncSession, last_id: int) -> None:
    row = (
        await db.execute(
            select(EmailProcessingCheckPoint).where(
                EmailProcessingCheckPoint.checkpoint_key == _CHECKPOINT_KEY
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        base = dict(row.checkpoint_value or {})
        base["last_processed_id"] = last_id
        row.checkpoint_value = base
    else:
        db.add(
            EmailProcessingCheckPoint(
                checkpoint_key=_CHECKPOINT_KEY,
                checkpoint_value={"last_processed_id": last_id},
            )
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Idempotency: processed-attachment tracking inside checkpoint_value
# ---------------------------------------------------------------------------

async def _get_processed_attachment_keys(db: AsyncSession) -> set[str]:
    """Return the set of attachment S3 keys already processed in previous runs."""
    row = (
        await db.execute(
            select(EmailProcessingCheckPoint).where(
                EmailProcessingCheckPoint.checkpoint_key == _CHECKPOINT_KEY
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return set()
    return set((row.checkpoint_value or {}).get("processed_attachments", []))


async def _mark_attachment_processed(db: AsyncSession, s3_key: str) -> None:
    row = (
        await db.execute(
            select(EmailProcessingCheckPoint).where(
                EmailProcessingCheckPoint.checkpoint_key == _CHECKPOINT_KEY
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            EmailProcessingCheckPoint(
                checkpoint_key=_CHECKPOINT_KEY,
                checkpoint_value={"last_processed_id": 0, "processed_attachments": [s3_key]},
            )
        )
    else:
        base = dict(row.checkpoint_value or {})
        existing = list(base.get("processed_attachments", []))
        if s3_key not in existing:
            existing.append(s3_key)
        base["processed_attachments"] = existing
        row.checkpoint_value = base
    await db.commit()


# ---------------------------------------------------------------------------
# Company / entity resolution
# ---------------------------------------------------------------------------

async def resolve_company(
    db: AsyncSession, company_name: str, review_cycle_id: str
) -> Optional[PortfolioCompany]:
    """Return the PortfolioCompany for this cycle whose name matches ``company_name``.

    The subject's deal-name field is free-typed, so matching is tolerant of **case and
    whitespace** (e.g. ``"Beam Checkout"`` vs ``"beam  checkout"``) and, as a guarded
    fallback, **legal-suffix** differences (``"Beam Checkout"`` vs ``"Beam Checkout Pte Ltd"``).
    Still returns None on no-match or an ambiguous match (>1 distinct company) so financials
    are never silently mapped to the wrong company.
    """
    rows = (
        await db.execute(
            select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == review_cycle_id)
        )
    ).scalars().all()

    target = _norm_name(company_name)
    matches = [r for r in rows if _norm_name(r.name) == target]
    if not matches:
        target_loose = _norm_name_loose(company_name)
        if target_loose:
            matches = [r for r in rows if _norm_name_loose(r.name) == target_loose]

    if not matches:
        # Last resort: every significant word of the subject name appears in the company name
        # (e.g. 'manatal' ⊆ 'Manatal Pte Ltd'). The ambiguity guard below rejects >1 match.
        target_tokens = _significant_tokens(company_name)
        if target_tokens:
            matches = [
                r for r in rows if target_tokens.issubset(_significant_tokens(r.name))
            ]

    if not matches:
        logger.warning(
            "audited_financials: no PortfolioCompany matched name=%r review_cycle_id=%r "
            "(checked %d companies in cycle, case/whitespace/suffix-insensitive)",
            company_name,
            review_cycle_id,
            len(rows),
        )
        return None
    if len({r.id for r in matches}) > 1:
        logger.warning(
            "audited_financials: ambiguous PortfolioCompany name=%r review_cycle_id=%r — %d matches; aborting",
            company_name,
            review_cycle_id,
            len(matches),
        )
        return None
    return matches[0]


async def resolve_entity(
    db: AsyncSession,
    portfolio_company_id: int,
    entity_name: str,
    review_cycle_id: Optional[str],
) -> Optional[Entity]:
    """Return matching Entity or None (entity match is optional; never blocks ingestion).

    Tolerant of case/whitespace/legal-suffix differences. Prefers an entity in the same
    review cycle, then falls back to any cycle for this company.
    """
    rows = (
        await db.execute(
            select(Entity).where(Entity.portfolio_company_id == portfolio_company_id)
        )
    ).scalars().all()

    def _match(cands: list[Entity]) -> Optional[Entity]:
        target = _norm_name(entity_name)
        m = [e for e in cands if _norm_name(e.name) == target]
        if not m:
            target_loose = _norm_name_loose(entity_name)
            if target_loose:
                m = [e for e in cands if _norm_name_loose(e.name) == target_loose]
        if not m:
            # Token-subset last resort, but only when it resolves to exactly one entity
            # (entity is optional, so we never risk picking the wrong one).
            target_tokens = _significant_tokens(entity_name)
            if target_tokens:
                tok_m = [e for e in cands if target_tokens.issubset(_significant_tokens(e.name))]
                if len(tok_m) == 1:
                    m = tok_m
        return m[0] if m else None

    if review_cycle_id:
        same_cycle = [e for e in rows if getattr(e, "review_cycle", None) == review_cycle_id]
        hit = _match(same_cycle)
        if hit is not None:
            return hit

    hit = _match(rows)
    if hit is not None:
        if review_cycle_id:
            logger.info(
                "audited_financials: entity matched without review_cycle filter name=%r", entity_name
            )
        return hit

    logger.info(
        "audited_financials: no entity found name=%r portfolio_company_id=%s — continuing with entity_id=None",
        entity_name,
        portfolio_company_id,
    )
    return None


# ---------------------------------------------------------------------------
# Attachment download from email S3 bucket
# ---------------------------------------------------------------------------

def _download_email_attachment(s3_key: str) -> bytes:
    """Download attachment bytes from the email S3 bucket using its full S3 key."""
    from src.configs.env import settings
    from src.utils.s3 import get_s3_client

    bucket = settings.AWS_S3_EMAIL_BUCKET
    if not bucket:
        raise RuntimeError("AWS_S3_EMAIL_BUCKET is not configured")
    client = get_s3_client()
    resp = client.get_object(Bucket=bucket, Key=s3_key)
    return resp["Body"].read()


# ---------------------------------------------------------------------------
# Per-attachment processing
# ---------------------------------------------------------------------------

@dataclass
class AttachmentResult:
    s3_key: str
    status: str  # "success" | "skipped" | "failed"
    file_id: Optional[int] = None
    error: Optional[str] = None
    skip_reason: Optional[str] = None
    filename: str = ""


async def _process_attachment(
    db: AsyncSession,
    s3_key: str,
    portfolio_company: PortfolioCompany,
    entity_id: Optional[int],
    review_cycle_id: str,
    already_processed: set[str],
    fy_end: Optional[str] = None,
    email_id: Optional[str] = None,
    force_reprocess: bool = False,
) -> AttachmentResult:
    filename = Path(s3_key).name or "attachment"

    # Per-email dedup: if a File already exists for (source_email_id, source_attachment_key)
    # and is not in a failed state, return it without re-processing.
    if email_id and not force_reprocess:
        from src.db.models import File as _File
        existing_result = await db.execute(
            select(_File).where(
                _File.source_email_id == email_id,
                _File.source_attachment_key == s3_key,
                _File.status != "failed",
                _File.status != "deleted",
            ).limit(1)
        )
        existing_file = existing_result.scalar_one_or_none()
        if existing_file is not None:
            # The attachment is already linked to a File. Decide what to do based on whether
            # extraction was ever kicked off for it:
            #   • already queued/running/completed/processed -> skip (unchanged behaviour)
            #   • NEVER queued (file attached but extraction never started) -> auto-trigger it now
            # We deliberately do NOT touch a run that started but did not finish ("running"):
            # that's a separate concern handled elsewhere. This only fixes the case where a file
            # arrived from email, got attached, but extraction was never triggered.
            from src.db.models import FileOCRMetadata as _FileOCRMetadata

            meta_row = (
                await db.execute(
                    select(_FileOCRMetadata).where(_FileOCRMetadata.file_id == existing_file.id)
                )
            ).scalar_one_or_none()
            ocr_json = getattr(meta_row, "ocr_json", None)
            ocr_status = ocr_json.get("status") if isinstance(ocr_json, dict) else None

            extraction_kicked_off = (
                ocr_status in ("running", "completed", "error")
                or existing_file.status == "processed"
            )
            if extraction_kicked_off:
                logger.info(
                    "audited_financials: skipping already-linked attachment email_id=%s key=%s "
                    "file_id=%s (extraction status=%r, file status=%r)",
                    email_id, s3_key, existing_file.id, ocr_status, existing_file.status,
                )
                return AttachmentResult(
                    s3_key=s3_key, status="skipped", file_id=existing_file.id,
                    skip_reason="already_processed", filename=filename,
                )

            # Attached but extraction was NEVER triggered -> kick it off now on the SAME file
            # (no new File row, no re-upload; the bytes are already in S3).
            logger.info(
                "audited_financials: already-linked attachment email_id=%s key=%s file_id=%s has no "
                "extraction queued (ocr status=%r, file status=%r) — auto-triggering extraction",
                email_id, s3_key, existing_file.id, ocr_status, existing_file.status,
            )
            try:
                await AuditService(db).queue_extraction(
                    file_id=existing_file.id, kind=_EXTRACTION_KIND
                )
            except Exception as exc:
                logger.error(
                    "audited_financials: queue_extraction failed for already-linked file_id=%s: %s",
                    existing_file.id, exc,
                )
                return AttachmentResult(
                    s3_key=s3_key, status="failed", file_id=existing_file.id,
                    error=str(exc), filename=filename,
                )

            async def _run_existing_extraction() -> None:
                async with fresh_ingestion_session() as ext_db:
                    await AuditService(ext_db).run_extraction_work(
                        file_id=existing_file.id, kind=_EXTRACTION_KIND
                    )

            try:
                await _run_existing_extraction()
            except Exception as exc:
                logger.error(
                    "audited_financials: run_extraction_work failed for already-linked file_id=%s: %s",
                    existing_file.id, exc,
                )
                # Extraction failure is already recorded inside run_extraction_work; don't propagate.

            return AttachmentResult(
                s3_key=s3_key, status="success", file_id=existing_file.id, filename=filename,
            )

    # Global checkpoint skip — only when no email context or force_reprocess not set
    if not force_reprocess and s3_key in already_processed:
        logger.info("audited_financials: skipping already-processed attachment key=%s", s3_key)
        return AttachmentResult(s3_key=s3_key, status="skipped", skip_reason="already_processed", filename=filename)

    fn_err = validate_audit_upload_filename(filename)
    if fn_err:
        msg = f"unsupported attachment {filename!r}: {fn_err}"
        logger.warning("audited_financials: %s", msg)
        return AttachmentResult(s3_key=s3_key, status="skipped", error=msg, skip_reason="unsupported_file", filename=filename)

    try:
        file_bytes = await asyncio.to_thread(_download_email_attachment, s3_key)
    except Exception as exc:
        msg = f"download failed for {s3_key!r}: {exc}"
        logger.error("audited_financials: %s", msg)
        return AttachmentResult(s3_key=s3_key, status="failed", error=msg, filename=filename)

    if len(file_bytes) > MAX_AUDIT_UPLOAD_BYTES:
        msg = f"attachment {filename!r} exceeds 50MB limit"
        logger.warning("audited_financials: %s", msg)
        return AttachmentResult(s3_key=s3_key, status="skipped", error=msg, skip_reason="too_large", filename=filename)

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    svc = AuditService(db)

    try:
        init = await svc.generate_upload_url(
            company_id=portfolio_company.id,
            file_name=filename,
            mime_type=mime_type,
            entity_id=entity_id,
            review_cycle_id=review_cycle_id,
            fy_end=fy_end,
            source_email_id=email_id,
            source_attachment_key=s3_key,
        )
        file_id = int(init["file_id"])
        await svc.upload_to_existing(
            file_id=file_id,
            file_bytes=file_bytes,
            content_type=mime_type,
        )
    except Exception as exc:
        msg = f"upload failed for {filename!r}: {exc}"
        logger.error("audited_financials: %s", msg)
        return AttachmentResult(s3_key=s3_key, status="failed", error=msg, filename=filename)

    # Queue and run extraction
    try:
        await svc.queue_extraction(file_id=file_id, kind=_EXTRACTION_KIND)
    except Exception as exc:
        logger.error(
            "audited_financials: queue_extraction failed file_id=%s: %s", file_id, exc
        )
        return AttachmentResult(s3_key=s3_key, status="failed", file_id=file_id, error=str(exc), filename=filename)

    # Run extraction in a fresh session bound to the current event loop
    # (this code also runs inside the classifier bridge's worker-thread loop,
    # where the global engine's pooled connections are unusable).
    async def _run_extraction() -> None:
        async with fresh_ingestion_session() as ext_db:
            ext_svc = AuditService(ext_db)
            await ext_svc.run_extraction_work(file_id=file_id, kind=_EXTRACTION_KIND)

    try:
        await _run_extraction()
    except Exception as exc:
        logger.error(
            "audited_financials: run_extraction_work failed file_id=%s: %s", file_id, exc
        )
        # Extraction failure is already recorded inside run_extraction_work; don't propagate

    await _mark_attachment_processed(db, s3_key)
    logger.info(
        "audited_financials: processed attachment key=%s file_id=%s", s3_key, file_id
    )
    return AttachmentResult(s3_key=s3_key, status="success", file_id=file_id, filename=filename)


# ---------------------------------------------------------------------------
# EmailHistory tagging
# ---------------------------------------------------------------------------

async def safe_legacy_refs(
    db: AsyncSession,
    *,
    company_id: Optional[str],
    review_cycle_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (company_id, company_pr_cycle_id) values safe to write to EmailHistory.

    email_history carries legacy FK constraints into the portfolioreview schema
    (company_id → company_data.id, company_pr_cycle_id → pr_submission_data.id)
    that the ORM model does not declare. A value with no matching row there
    aborts the INSERT/UPDATE, so unmatched values degrade to None — the app
    links emails through portfolio_company_id, which is unaffected.
    """

    async def _exists(table: str, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            # to_regclass never raises, even when the schema/table is absent —
            # probing with a plain SELECT would abort the transaction and expire
            # every ORM object in the session on rollback.
            reg = (
                await db.execute(
                    text("SELECT to_regclass(:t)"), {"t": f"portfolioreview.{table}"}
                )
            ).scalar_one_or_none()
            if reg is None:
                # Table doesn't exist in this environment, so no FK against it
                # can exist either — the original value is safe to store.
                return value
            hit = (
                await db.execute(
                    text(f"SELECT 1 FROM portfolioreview.{table} WHERE id = :v LIMIT 1"),
                    {"v": value},
                )
            ).scalar_one_or_none()
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "audited_financials: legacy-ref lookup failed on portfolioreview.%s (%s) — "
                "writing NULL instead of %r",
                table,
                exc,
                value,
            )
            return None
        return value if hit else None

    safe_company = await _exists("company_data", company_id)
    safe_cycle = await _exists("pr_submission_data", review_cycle_id)
    if safe_company != company_id or safe_cycle != review_cycle_id:
        logger.info(
            "audited_financials: degrading legacy refs company_id=%r→%r cycle=%r→%r "
            "(no matching portfolioreview row; email stays linked via portfolio_company_id)",
            company_id,
            safe_company,
            review_cycle_id,
            safe_cycle,
        )
    return safe_company, safe_cycle


async def _tag_email_history(
    db: AsyncSession,
    temp_email: TempEmailHistory,
    portfolio_company: PortfolioCompany,
    review_cycle_id: str,
) -> None:
    """Tag the EmailHistory row (if already created by classifier) with company fields."""
    te_subject = temp_email.subject
    te_from_email = temp_email.from_email
    te_id = temp_email.id
    pc_pk = portfolio_company.id
    pc_company_id = portfolio_company.company_id

    rows = (
        await db.execute(
            select(EmailHistory).where(
                EmailHistory.subject == te_subject,
                EmailHistory.sender == te_from_email,
            )
        )
    ).scalars().all()
    if not rows:
        logger.info(
            "audited_financials: EmailHistory not yet created for temp_email id=%s — will tag on next run",
            te_id,
        )
        return
    safe_company_id, safe_cycle_id = await safe_legacy_refs(
        db, company_id=pc_company_id, review_cycle_id=review_cycle_id
    )
    for eh in rows:
        changed = False
        if eh.portfolio_company_id != pc_pk:
            eh.portfolio_company_id = pc_pk
            changed = True
        if safe_company_id and eh.company_id != safe_company_id:
            eh.company_id = safe_company_id
            changed = True
        if safe_cycle_id and eh.company_pr_cycle_id != safe_cycle_id:
            eh.company_pr_cycle_id = safe_cycle_id
            changed = True
        if changed:
            db.add(eh)
    await db.commit()
    logger.info(
        "audited_financials: tagged %d EmailHistory row(s) for temp_email id=%s",
        len(rows),
        te_id,
    )


# ---------------------------------------------------------------------------
# Per-email processing
# ---------------------------------------------------------------------------

async def _upsert_email_history(
    db: AsyncSession,
    temp_email: TempEmailHistory,
    portfolio_company: PortfolioCompany,
    review_cycle_id: str,
) -> Optional[str]:
    """Create or update the EmailHistory row for this audited-financials email.

    The classifier was skipped for this email so we must write the EmailHistory
    row ourselves — with company_id and portfolio_company_id already populated.
    If a matching row already exists (subject + sender) we patch it in place.

    Returns the EmailHistory.id so callers can pass it as source_email_id to
    _process_attachment, linking File rows back to this email.
    """
    from pytz import timezone as pytz_timezone
    TZ_IST = pytz_timezone("Asia/Kolkata")

    # Snapshot every temp_email field up front: a rollback inside any query below
    # expires the ORM object, and a later attribute access would lazy-load
    # synchronously inside the async session ("greenlet_spawn has not been called").
    te_subject = temp_email.subject
    te_from_email = temp_email.from_email
    te_to = temp_email.to
    te_cc = temp_email.cc
    te_body = temp_email.body
    te_body_html = getattr(temp_email, "body_html", None)
    te_sent_at = temp_email.sent_at
    te_attachments = temp_email.attachments
    te_status = temp_email.status
    pc_pk = portfolio_company.id
    pc_company_id = portfolio_company.company_id

    existing_rows = (
        await db.execute(
            select(EmailHistory).where(
                EmailHistory.subject == te_subject,
                EmailHistory.sender == te_from_email,
            )
        )
    ).scalars().all()

    safe_company_id, safe_cycle_id = await safe_legacy_refs(
        db, company_id=pc_company_id, review_cycle_id=review_cycle_id
    )

    if existing_rows:
        for eh in existing_rows:
            eh.portfolio_company_id = pc_pk
            if safe_company_id:
                eh.company_id = safe_company_id
            if safe_cycle_id:
                eh.company_pr_cycle_id = safe_cycle_id
            if te_attachments:
                eh.attachments = te_attachments
            db.add(eh)
        await db.commit()
        logger.info(
            "audited_financials: patched %d existing EmailHistory row(s) with company_id=%s",
            len(existing_rows),
            safe_company_id,
        )
        return existing_rows[0].id

    # No existing row — create one now
    if te_sent_at:
        sent_final = (
            te_sent_at.astimezone(TZ_IST)
            if te_sent_at.tzinfo is not None
            else TZ_IST.localize(te_sent_at.replace(tzinfo=None))
        )
    else:
        sent_final = datetime.now(dt_timezone.utc).astimezone(TZ_IST)

    new_email = EmailHistory(
        id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        portfolio_company_id=pc_pk,
        company_id=safe_company_id,
        company_pr_cycle_id=safe_cycle_id,
        sender=(te_from_email or "").strip(),
        recipients=te_to,
        cc=te_cc,
        subject=(te_subject or "").strip(),
        body=(te_body_html or te_body or ""),
        email_type="audited_financials",
        attachments=te_attachments,
        sent_at=sent_final,
        is_inbound=True,
        created_at=datetime.utcnow(),
        updated_on=datetime.utcnow(),
        status=int(te_status or 0),
    )
    db.add(new_email)
    await db.commit()
    logger.info(
        "audited_financials: created EmailHistory id=%s portfolio_company_id=%s company_id=%s cycle=%s",
        new_email.id,
        pc_pk,
        safe_company_id,
        safe_cycle_id,
    )
    return new_email.id


@dataclass
class AuditedFinancialsResult:
    company_matched: bool = False
    company_name: str = ""
    company_id: str = ""
    review_cycle_id: str = ""
    entity_matched: bool = False
    skip_reason: str = ""


async def _process_one_email(db: AsyncSession, temp_email: TempEmailHistory) -> AuditedFinancialsResult:
    result = AuditedFinancialsResult()
    subject = (temp_email.subject or "").strip()
    parsed = parse_audited_financials_subject(subject)
    if parsed is None:
        result.skip_reason = "subject does not match audited-financials pattern"
        return result

    logger.info(
        "audited_financials: processing temp_email id=%s subject=%r",
        temp_email.id,
        subject,
    )

    # 1. Normalize FY end
    fy_end = normalize_fy_end(parsed.fy_end_raw)
    if not fy_end:
        logger.error(
            "audited_financials: invalid fy_end %r in subject %r — skipping",
            parsed.fy_end_raw,
            subject,
        )
        result.skip_reason = f"invalid fy_end {parsed.fy_end_raw!r}"
        return result

    # 2. Resolve review cycle
    try:
        review_cycle_id = await resolve_review_cycle_id_for_fy_end(db, fy_end)
    except Exception as exc:
        logger.error(
            "audited_financials: cannot resolve review_cycle for fy_end=%r temp_email id=%s: %s",
            fy_end,
            temp_email.id,
            exc,
        )
        result.skip_reason = f"cannot resolve review_cycle for fy_end={fy_end!r}: {exc}"
        return result

    result.review_cycle_id = review_cycle_id

    # 3. Match company (mandatory)
    company = await resolve_company(db, parsed.company_name, review_cycle_id)
    if company is None:
        logger.error(
            "audited_financials: company not found name=%r review_cycle_id=%r — aborting temp_email id=%s",
            parsed.company_name,
            review_cycle_id,
            temp_email.id,
        )
        result.skip_reason = f"company not found name={parsed.company_name!r} cycle={review_cycle_id!r}"
        return result

    result.company_matched = True
    result.company_name = company.name or ""
    result.company_id = str(company.company_id or "")

    # 4. Match entity (optional)
    entity = await resolve_entity(db, company.id, parsed.entity_name, review_cycle_id)
    entity_id = entity.id if entity is not None else None
    result.entity_matched = entity is not None

    # 5. Write EmailHistory row now (classifier was skipped for this email).
    #    This ensures company_id, portfolio_company_id and thread are always recorded
    #    regardless of whether attachments exist or succeed. A failure here must not
    #    block attachment extraction — the file pipeline is the critical path.
    email_history_id: Optional[str] = None
    try:
        email_history_id = await _upsert_email_history(db, temp_email, company, review_cycle_id)
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "audited_financials: EmailHistory write failed for temp_email id=%s — "
            "continuing with attachment processing: %s",
            temp_email.id,
            exc,
        )

    # 6. Process attachments
    attachments: list[str] = list(temp_email.attachments or [])
    if not attachments:
        logger.warning(
            "audited_financials: no attachments on temp_email id=%s subject=%r — "
            "EmailHistory written, extraction skipped",
            temp_email.id,
            subject,
        )
        return result

    already_processed = await _get_processed_attachment_keys(db)

    for s3_key in attachments:
        try:
            att_result = await _process_attachment(
                db=db,
                s3_key=s3_key,
                portfolio_company=company,
                entity_id=entity_id,
                review_cycle_id=review_cycle_id,
                already_processed=already_processed,
                fy_end=fy_end,
                email_id=email_history_id,
            )
            already_processed.add(s3_key)  # keep local set in sync
            if att_result.status == "failed":
                logger.error(
                    "audited_financials: attachment failed key=%s error=%s",
                    s3_key,
                    att_result.error,
                )
        except Exception as exc:
            logger.exception(
                "audited_financials: unexpected error processing attachment key=%s: %s",
                s3_key,
                exc,
            )

    return result


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

async def _run_async() -> None:
    async with fresh_ingestion_session() as db:
        last_id = await _get_last_processed_id(db)
        logger.info(
            "audited_financials: starting from checkpoint id=%s", last_id
        )

        batch_size = 100
        current_id = last_id
        total_processed = 0

        while True:
            rows = (
                await db.execute(
                    select(TempEmailHistory)
                    .where(TempEmailHistory.id > current_id)
                    .order_by(TempEmailHistory.id)
                    .limit(batch_size)
                )
            ).scalars().all()
            if not rows:
                break

            for temp_email in rows:
                try:
                    await _process_one_email(db, temp_email)
                    total_processed += 1
                except Exception as exc:
                    logger.exception(
                        "audited_financials: unhandled error for temp_email id=%s: %s",
                        temp_email.id,
                        exc,
                    )
                current_id = temp_email.id

            # Advance checkpoint after each batch
            try:
                await _update_checkpoint(db, current_id)
                logger.info("audited_financials: checkpoint advanced to id=%s", current_id)
            except Exception as exc:
                logger.exception(
                    "audited_financials: checkpoint update failed: %s", exc
                )

        logger.info(
            "audited_financials: done — scanned up to id=%s total_emails_touched=%s",
            current_id,
            total_processed,
        )


async def process_audited_financials_emails() -> None:
    """Entry point for AsyncIOScheduler and manual runs.

    Phase 1: scans new TempEmailHistory rows (legacy path).
    Phase 2: scans EmailHistory rows written by the classifier whose subject matches
             the audited-financials pattern but haven't been extracted yet.

    Both phases run on the AsyncIOScheduler's own event loop — no asyncio.run()
    bridging, no loop-conflict errors.  All failures are caught and logged; the
    function never raises.
    """
    try:
        await _run_async()
    except Exception as exc:
        logger.exception("audited_financials: top-level failure (TempEmailHistory phase): %s", exc)

    try:
        await _run_email_history_async()
    except Exception as exc:
        logger.exception("audited_financials: top-level failure (EmailHistory phase): %s", exc)


async def _process_one_email_history(db: AsyncSession, eh: EmailHistory) -> str:
    """Run extraction for a single EmailHistory row whose subject matches the audited-financials pattern.

    Attachments are already in our S3 bucket (copied by the classifier).
    Tags the row with resolved company/cycle and email_type='audited_financials'.

    Returns a short outcome string ("processed" / "no_parse" / "invalid_fy" /
    "no_review_cycle" / "no_company" / "tagged_no_attachments" / "tag_failed") so the
    caller can log a per-run summary. Each decision point is logged with the email id and
    subject so a single email can be traced end-to-end through the log.
    """
    subject = (eh.subject or "").strip()
    # A stable, greppable prefix for every line about this email.
    tag = f"id={eh.id} subject={subject!r}"
    # Log sender / inbound / attachment file names up front (BEFORE parsing) so a candidate
    # can be classified — company submission vs internal notification — even when the subject
    # never parses. attachment basenames are cheaper to read than full S3 keys.
    eh_attachments = list(eh.attachments or [])
    att_names = [Path(k).name for k in eh_attachments]
    logger.info(
        "audited_financials(eh): >>> evaluating %s sender=%r inbound=%s attachments(%d)=%s",
        tag, eh.sender, eh.is_inbound, len(eh_attachments), att_names,
    )

    parsed = parse_audited_financials_subject(subject)
    if parsed is None:
        # NOTE: parse_audited_financials_subject already logs the specific reason
        # (no keyword / no parseable period / missing company-entity field).
        logger.warning("audited_financials(eh): SKIP %s — subject did not parse", tag)
        return "no_parse"
    logger.info(
        "audited_financials(eh): parsed %s -> company=%r entity=%r period=%r",
        tag, parsed.company_name, parsed.entity_name, parsed.fy_end_raw,
    )

    attachments = eh_attachments  # already snapshotted + logged at entry

    # 1. Normalize FY end
    fy_end = normalize_fy_end(parsed.fy_end_raw)
    if not fy_end:
        logger.error(
            "audited_financials(eh): SKIP %s — invalid fy_end %r (could not normalize)",
            tag, parsed.fy_end_raw,
        )
        return "invalid_fy"
    logger.info("audited_financials(eh): %s fy_end normalized -> %s", tag, fy_end)

    # 2. Resolve review cycle
    try:
        review_cycle_id = await resolve_review_cycle_id_for_fy_end(db, fy_end)
    except Exception as exc:
        logger.error(
            "audited_financials(eh): SKIP %s — cannot resolve review_cycle for fy_end=%r: %s",
            tag, fy_end, exc,
        )
        return "no_review_cycle"
    logger.info("audited_financials(eh): %s resolved review_cycle_id=%s", tag, review_cycle_id)

    # 3. Match company (mandatory)
    company = await resolve_company(db, parsed.company_name, review_cycle_id)
    if company is None:
        # resolve_company logs whether it was a no-match or an ambiguous (>1) match.
        logger.error(
            "audited_financials(eh): SKIP %s — no company matched name=%r in cycle=%r "
            "(stays tagged-by-classifier only; needs manual Tag & Extract)",
            tag, parsed.company_name, review_cycle_id,
        )
        return "no_company"
    logger.info(
        "audited_financials(eh): %s matched company id=%s name=%r",
        tag, company.id, company.name,
    )

    # 4. Match entity (optional)
    entity = await resolve_entity(db, company.id, parsed.entity_name, review_cycle_id)
    entity_id = entity.id if entity is not None else None
    if entity is not None:
        logger.info("audited_financials(eh): %s matched entity id=%s name=%r", tag, entity.id, entity.name)
    else:
        logger.info(
            "audited_financials(eh): %s no entity matched for name=%r — file will be company-level",
            tag, parsed.entity_name,
        )

    # 5. Tag the EmailHistory row
    try:
        safe_company_id, safe_cycle_id = await safe_legacy_refs(
            db, company_id=company.company_id, review_cycle_id=review_cycle_id
        )
        eh_row = (
            await db.execute(select(EmailHistory).where(EmailHistory.id == eh.id))
        ).scalar_one_or_none()
        if eh_row is not None:
            eh_row.portfolio_company_id = company.id
            if safe_company_id:
                eh_row.company_id = safe_company_id
            if safe_cycle_id:
                eh_row.company_pr_cycle_id = safe_cycle_id
            eh_row.email_type = "audited_financials"
            db.add(eh_row)
            await db.commit()
            logger.info(
                "audited_financials(eh): %s tagged to company id=%s, email_type='audited_financials'",
                tag, company.id,
            )
    except Exception as exc:
        await db.rollback()
        logger.exception("audited_financials(eh): %s tagging failed: %s", tag, exc)
        return "tag_failed"

    if not attachments:
        logger.warning(
            "audited_financials(eh): %s tagged but has NO attachments — nothing to extract", tag
        )
        return "tagged_no_attachments"

    # 6. Process attachments — S3 keys already in our bucket
    already_processed = await _get_processed_attachment_keys(db)
    for s3_key in attachments:
        logger.info("audited_financials(eh): %s -> processing attachment key=%s", tag, s3_key)
        try:
            att = await _process_attachment(
                db=db,
                s3_key=s3_key,
                portfolio_company=company,
                entity_id=entity_id,
                review_cycle_id=review_cycle_id,
                already_processed=already_processed,
                fy_end=fy_end,
                email_id=eh.id,
            )
            already_processed.add(s3_key)
            logger.info(
                "audited_financials(eh): %s attachment key=%s -> status=%s file_id=%s%s",
                tag, s3_key, att.status, att.file_id,
                f" reason={att.skip_reason}" if att.skip_reason else (f" error={att.error}" if att.error else ""),
            )
        except Exception as exc:
            logger.exception(
                "audited_financials(eh): %s attachment key=%s raised: %s", tag, s3_key, exc
            )
    logger.info("audited_financials(eh): <<< done %s", tag)
    return "processed"


async def _run_email_history_async() -> None:
    """Scan EmailHistory rows that look like audited-financials mail but haven't been
    extracted yet, and run the file-creation + extraction pipeline for each.

    Idempotency is the ``email_type`` flag, NOT a created_at checkpoint: a row is stamped
    ``email_type='audited_financials'`` only once it has been successfully tagged and handed to
    attachment processing (see ``_process_one_email_history`` step 5). Rows that fail an earlier
    parse keep ``email_type=''`` and are therefore reconsidered on the next run — so a brittle
    subject that we later learn to parse self-heals instead of being skipped forever. Re-scanning
    a still-unparseable row is cheap (``parse → None`` with no S3/LLM I/O), and per-attachment
    work is de-duped inside ``_process_attachment`` via ``(source_email_id, source_attachment_key)``.

    The SQL pre-filter is a *contains* match so reply/forward-prefixed subjects ("Re: Audited
    Financials_…") are considered; ``parse_audited_financials_subject`` is the precise gate.
    """
    async with fresh_ingestion_session() as db:
        # Fetch not-yet-extracted matching rows, oldest first. is_distinct_from keeps NULL
        # email_type rows in scope (Postgres '!=' would drop them).
        q = (
            select(EmailHistory)
            .where(
                EmailHistory.is_inbound.is_(True),
                EmailHistory.email_type.is_distinct_from("audited_financials"),
                EmailHistory.subject.ilike("%audited financ%"),
                EmailHistory.attachments.is_not(None),
            )
            .order_by(EmailHistory.created_at.asc())
            .limit(200)
        )

        rows = (await db.execute(q)).scalars().all()
        if not rows:
            logger.info(
                "audited_financials(eh): no candidate EmailHistory rows "
                "(inbound, subject~'audited financ', has attachments, not yet extracted)"
            )
            return

        logger.info(
            "audited_financials(eh): %d candidate row(s) to evaluate: %s",
            len(rows), [eh.id for eh in rows],
        )
        outcomes: dict[str, int] = {}
        for eh in rows:
            try:
                outcome = await _process_one_email_history(db, eh)
            except Exception as exc:
                outcome = "unhandled_error"
                logger.exception(
                    "audited_financials(eh): unhandled error for EmailHistory id=%s: %s",
                    eh.id, exc,
                )
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        # One-line summary so you can see, per run, how every candidate ended up.
        logger.info("audited_financials(eh): run summary by outcome: %s", outcomes)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(process_audited_financials_emails())
