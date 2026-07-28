"""
Batch org-chart ZIP upload service.

Flow:
  1. User POSTs a ZIP + review_cycle_id  → create OrgChartUploadBatch + one
     OrgChartUploadRecord (+ File row) per valid file inside the ZIP.
  2. User GETs mapping template XLSX  → file_name / company_id / name columns.
  3. User POSTs filled template XLSX  → each row is resolved to a PortfolioCompany
     (company_id first, name fallback, both scoped to review_cycle_id) and LLM
     extraction is triggered for resolved rows.
"""
from __future__ import annotations

import io
import logging
import mimetypes
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import openpyxl
from fastapi import UploadFile
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings
from src.db.file_filters import ORG_CHART_BATCH_FILE_TAG
from src.db.models import (
    File,
    FileUploadStatus,
    OrgChartUploadBatch,
    OrgChartUploadRecord,
    PortfolioCompany,
    ReviewCycle,
)
from src.services.docx_conversion import maybe_convert_to_pdf
from src.utils.s3 import delete_file_best_effort, upload_file as s3_upload_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / limits (re-use existing audit-upload constants where possible)
# ---------------------------------------------------------------------------

ALLOWED_BATCH_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx",
    ".png", ".jpg", ".jpeg", ".pptx", ".ppt",
})
MAX_BATCH_FILE_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB per file
MAX_BATCH_TOTAL_FILES = 300
ORG_CHART_BATCH_TAG = ORG_CHART_BATCH_FILE_TAG

# ---------------------------------------------------------------------------
# ZIP validation helpers
# ---------------------------------------------------------------------------

_SKIP_PREFIXES = ("__MACOSX/", ".DS_Store")


def _is_skippable(name: str) -> bool:
    base = Path(name).name
    return (
        base.startswith(".")
        or any(name.startswith(p) for p in _SKIP_PREFIXES)
        or name.endswith("/")  # directory entry
    )


def _safe_filename(name: str) -> str:
    """Return only the basename, strip directory component."""
    return Path(name).name


def _validate_no_traversal(name: str) -> None:
    """Raise ValueError if the entry attempts path traversal."""
    resolved = Path(name)
    for part in resolved.parts:
        if part in ("..", "/"):
            raise ValueError(f"Path traversal detected in ZIP entry: {name!r}")


def _s3_key_batch(review_cycle_id: Optional[str], batch_id: int, file_id_str: str, filename: str) -> str:
    safe = filename.replace(" ", "_")
    return f"org_chart_batches/{review_cycle_id or 'unassigned'}/{batch_id}/{file_id_str}/{safe}"


# ---------------------------------------------------------------------------
# ZIP upload
# ---------------------------------------------------------------------------

def validate_zip(
    zip_bytes: bytes,
) -> list[tuple[str, str, bytes]]:
    """
    Parse and validate a ZIP, returning a list of (display_name, ext, data) entries.

    Raises ValueError for invalid ZIPs or if no valid files are found.
    Does NOT hit the DB or S3 — safe to call synchronously before the request returns.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Not a valid ZIP file: {exc}") from exc

    entries: list[tuple[str, str, bytes]] = []
    seen_names: set[str] = set()
    errors: list[str] = []

    for info in zf.infolist():
        if _is_skippable(info.filename):
            continue
        _validate_no_traversal(info.filename)
        display = _safe_filename(info.filename)
        if not display:
            continue
        ext = Path(display).suffix.lower()
        if ext not in ALLOWED_BATCH_EXTENSIONS:
            errors.append(f"{display}: unsupported extension {ext!r}")
            continue
        if display in seen_names:
            stem = Path(display).stem
            unique = f"{stem}_{len(entries)+1}{ext}"
            logger.warning("Duplicate ZIP entry %s renamed to %s", display, unique)
            display = unique
        seen_names.add(display)

        data = zf.read(info.filename)
        if len(data) > MAX_BATCH_FILE_SIZE_BYTES:
            errors.append(f"{display}: file too large ({len(data)} bytes)")
            continue
        entries.append((display, ext, data))

    if not entries:
        if errors:
            raise ValueError(f"ZIP contained no valid files. Errors: {'; '.join(errors)}")
        raise ValueError("ZIP is empty or contains no supported files (.pdf, .docx, .doc, .xlsx, .png, .jpg, .jpeg, .pptx, .ppt)")

    if len(entries) > MAX_BATCH_TOTAL_FILES:
        raise ValueError(
            f"ZIP contains {len(entries)} files; maximum allowed is {MAX_BATCH_TOTAL_FILES}"
        )

    by_ext: dict[str, int] = {}
    for _, ext, _ in entries:
        by_ext[ext] = by_ext.get(ext, 0) + 1
    breakdown = ", ".join(f"{ext}={count}" for ext, count in sorted(by_ext.items()))
    skipped = len(errors)
    logger.info(
        "ZIP validated: %d file(s) accepted [%s], %d skipped/rejected%s",
        len(entries),
        breakdown,
        skipped,
        f" — errors: {'; '.join(errors)}" if errors else "",
    )

    return entries


async def create_pending_batch(
    db: AsyncSession,
    review_cycle_id: Optional[str],
    zip_filename: str,
    file_count: int,
) -> OrgChartUploadBatch:
    """
    Create the OrgChartUploadBatch row in 'processing' status and return immediately.
    The caller is responsible for launching process_batch_entries as a background task.

    Raises ValueError if review_cycle_id is provided but not found.
    """
    if review_cycle_id is not None:
        cycle = (
            await db.execute(select(ReviewCycle).where(ReviewCycle.id == review_cycle_id))
        ).scalar_one_or_none()
        if cycle is None:
            raise ValueError(f"Review cycle {review_cycle_id!r} not found")

    batch = OrgChartUploadBatch(
        review_cycle_id=review_cycle_id,
        original_zip_filename=zip_filename[:512],
        status="processing",
        file_count=file_count,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return batch


async def process_batch_entries(
    batch_id: int,
    review_cycle_id: Optional[str],
    entries: list[tuple[str, str, bytes]],
) -> None:
    """
    Background task: convert, upload to S3, create File + OrgChartUploadRecord rows.
    Sets batch status to 'uploaded' on success or 'failed' on error.
    Opens its own DB session — must be called outside the request session.
    """
    from src.db.session import AsyncSessionLocal

    s3_keys_uploaded: list[str] = []

    logger.info("Batch %s: starting background processing of %d file(s)", batch_id, len(entries))

    async with AsyncSessionLocal() as db:
        try:
            for idx, (display_name, ext, data) in enumerate(entries, start=1):
                file_id_str = str(uuid.uuid4())
                content_type = mimetypes.guess_type(display_name)[0] or "application/octet-stream"
                original_name = display_name

                # Convert to PDF if required (ppt/pptx/docx/images). If conversion
                # fails (e.g. LibreOffice not available), mark this file as failed
                # immediately rather than sending raw binary bytes to the LLM — the
                # LLM would otherwise hallucinate entity names from unreadable content.
                conversion_error: Optional[str] = None
                try:
                    data, display_name, content_type = maybe_convert_to_pdf(data, display_name, content_type)
                except Exception as conv_exc:
                    conversion_error = f"File conversion to PDF failed: {conv_exc}"
                    logger.warning(
                        "Batch %s [%d/%d] conversion failed for %s: %s",
                        batch_id, idx, len(entries), original_name, conv_exc,
                    )

                converted = display_name != original_name
                s3_key = _s3_key_batch(review_cycle_id, batch_id, file_id_str, display_name)
                bucket = settings.S3_BUCKET
                storage_uri = f"s3://{bucket}/{s3_key}"

                logger.info(
                    "Batch %s [%d/%d] uploading %s (%d bytes)%s",
                    batch_id, idx, len(entries), display_name, len(data),
                    f" [converted from {original_name}]" if converted else "",
                )
                s3_upload_file(data, s3_key, content_type)
                s3_keys_uploaded.append(s3_key)

                file_row = File(
                    portfolio_company_id=None,
                    entity_id=None,
                    review_cycle_id=review_cycle_id,
                    filename=display_name,
                    content_type=content_type,
                    storage_uri=storage_uri,
                    size_bytes=len(data),
                    status=FileUploadStatus.UPLOADED,
                    tags=[ORG_CHART_BATCH_TAG],
                    pending_audit_log=[],
                )
                db.add(file_row)
                await db.flush()
                # Explicitly refresh to ensure the server-generated PK is loaded.
                # In SQLAlchemy async, flush() sends the INSERT but the PK returned
                # via RETURNING may not be reflected on the ORM object without a
                # refresh in some driver/version combinations.
                await db.refresh(file_row)

                # Fallback: if id is still None after refresh, query by storage_uri.
                # This handles edge cases where refresh didn't load the PK.
                file_db_id: Optional[int] = file_row.id
                if file_db_id is None:
                    logger.warning(
                        "Batch %s [%d/%d] file_row.id is None after refresh — querying by storage_uri",
                        batch_id, idx, len(entries),
                    )
                    result = await db.execute(
                        select(File).where(File.storage_uri == storage_uri)
                    )
                    fetched = result.scalar_one_or_none()
                    file_db_id = fetched.id if fetched else None

                logger.info(
                    "Batch %s [%d/%d] file row created id=%s",
                    batch_id, idx, len(entries), file_db_id,
                )

                record = OrgChartUploadRecord(
                    batch_id=batch_id,
                    review_cycle_id=review_cycle_id,
                    file_id=file_db_id,
                    file_name=original_name,
                    storage_uri=storage_uri,
                    company_id=None,
                    name=None,
                    portfolio_company_id=None,
                    extracted_org_chart=None,
                    extraction_status="failed" if conversion_error else "pending_mapping",
                    error_message=conversion_error,
                )
                db.add(record)

            batch = await db.get(OrgChartUploadBatch, batch_id)
            if batch:
                batch.status = "uploaded"
            await db.commit()
            logger.info("Batch %s processing complete (%d files)", batch_id, len(entries))

        except Exception:
            logger.exception("Batch %s processing failed; cleaning up S3 best-effort", batch_id)
            for key in s3_keys_uploaded:
                try:
                    delete_file_best_effort(key)
                except Exception:  # noqa: BLE001
                    pass
            try:
                async with AsyncSessionLocal() as err_db:
                    batch = await err_db.get(OrgChartUploadBatch, batch_id)
                    if batch:
                        batch.status = "failed"
                    await err_db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Batch %s: could not mark status=failed", batch_id)


async def create_batch_from_zip(
    db: AsyncSession,
    review_cycle_id: Optional[str],
    zip_bytes: bytes,
    zip_filename: str,
) -> OrgChartUploadBatch:
    """
    Kept for backwards compatibility. Validates ZIP, creates batch row in 'processing'
    status, and returns it. Caller must schedule process_batch_entries as a background task.
    """
    entries = validate_zip(zip_bytes)
    return await create_pending_batch(db, review_cycle_id, zip_filename, len(entries))


# ---------------------------------------------------------------------------
# Mapping template XLSX
# ---------------------------------------------------------------------------

def build_mapping_template(records: list[OrgChartUploadRecord]) -> bytes:
    """Return XLSX bytes with headers [file_name, company_id, name] and one row per record."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Org Chart Mapping"
    ws.append(["file_name", "company_id", "name"])
    for r in records:
        ws.append([r.file_name, "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_status_report(records: list[OrgChartUploadRecord]) -> bytes:
    """Return XLSX bytes with current processing state: file_name, company_id, name, status, error."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Status Report"
    ws.append(["file_name", "company_id", "name", "status", "error"])
    for r in records:
        ws.append([r.file_name, r.company_id or "", r.name or "", r.extraction_status, r.error_message or ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Mapping XLSX upload
# ---------------------------------------------------------------------------

class MappingUploadResult:
    def __init__(self) -> None:
        self.rows_processed: int = 0
        self.rows_mapped: int = 0
        self.rows_skipped: int = 0
        self.errors: list[dict[str, Any]] = []


async def process_mapping_upload(
    db: AsyncSession,
    batch: OrgChartUploadBatch,
    xlsx_bytes: bytes,
    review_cycle_id: Optional[str] = None,
) -> MappingUploadResult:
    """
    Parse the user-filled mapping XLSX and resolve each row to a PortfolioCompany.

    Resolution order (scoped to review_cycle_id, falling back to batch.review_cycle_id):
      1. company_id column (PortfolioCompany.company_id)  — if present, use first
      2. name column (PortfolioCompany.name)               — fallback

    Records that resolve are updated and queued for LLM extraction.
    Unresolvable rows are left with extraction_status=pending_mapping and an error_message.
    All updates are committed in one transaction; per-row errors do not abort others.
    """
    result = MappingUploadResult()

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Cannot read XLSX: {exc}") from exc

    ws = wb.active
    rows_iter = iter(ws.iter_rows(values_only=True))

    header_row = next(rows_iter, None)
    if header_row is None:
        raise ValueError("XLSX is empty")

    headers = [str(h).strip().lower() if h is not None else "" for h in header_row]
    required = {"file_name", "company_id", "name"}
    missing = required - set(headers)
    if missing:
        raise ValueError(f"XLSX missing required columns: {missing}")

    idx_fn = headers.index("file_name")
    idx_cid = headers.index("company_id")
    idx_name = headers.index("name")

    # Build lookup map: file_name → record
    records_q = await db.execute(
        select(OrgChartUploadRecord).where(OrgChartUploadRecord.batch_id == batch.id)
    )
    record_map: dict[str, OrgChartUploadRecord] = {r.file_name: r for r in records_q.scalars()}

    review_cycle_id = review_cycle_id or batch.review_cycle_id

    for xlsx_row in rows_iter:
        result.rows_processed += 1

        def _cell(idx: int) -> str:
            v = xlsx_row[idx] if idx < len(xlsx_row) else None
            return str(v).strip() if v is not None else ""

        file_name = _cell(idx_fn)
        company_id_val = _cell(idx_cid)
        name_val = _cell(idx_name)

        if not file_name:
            result.rows_skipped += 1
            result.errors.append({"row": result.rows_processed + 1, "file_name": file_name, "error": "empty file_name"})
            continue

        record = record_map.get(file_name)
        if record is None:
            result.rows_skipped += 1
            result.errors.append({"row": result.rows_processed + 1, "file_name": file_name, "error": "file_name not found in batch"})
            continue

        pc: Optional[PortfolioCompany] = None

        if company_id_val:
            pc = (
                await db.execute(
                    select(PortfolioCompany).where(
                        and_(
                            PortfolioCompany.company_id == company_id_val,
                            PortfolioCompany.review_cycle_id == review_cycle_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if pc is None:
                result.errors.append({
                    "row": result.rows_processed + 1,
                    "file_name": file_name,
                    "error": f"company_id {company_id_val!r} not found in review cycle {review_cycle_id!r}",
                })

        if pc is None and name_val:
            pc = (
                await db.execute(
                    select(PortfolioCompany).where(
                        and_(
                            PortfolioCompany.name == name_val,
                            PortfolioCompany.review_cycle_id == review_cycle_id,
                        )
                    )
                )
            ).scalar_one_or_none()
            if pc is None:
                result.errors.append({
                    "row": result.rows_processed + 1,
                    "file_name": file_name,
                    "error": f"name {name_val!r} not found in review cycle {review_cycle_id!r}",
                })

        record.company_id = company_id_val or None
        record.name = name_val or None

        if pc is not None:
            record.portfolio_company_id = pc.id
            record.extraction_status = "mapped"
            result.rows_mapped += 1
        else:
            result.rows_skipped += 1

    # Update batch status
    total = len(record_map)
    mapped = sum(1 for r in record_map.values() if r.extraction_status in ("mapped", "processing", "completed"))
    if mapped == 0:
        batch.status = "uploaded"
    elif mapped < total:
        batch.status = "partially_mapped"
    else:
        batch.status = "processing"

    batch.mapping_uploaded_at = datetime.now(timezone.utc)

    await db.flush()
    await db.commit()

    # Queue LLM extraction for newly mapped records (fire-and-forget in background)
    mapped_ids = [
        r.id for r in record_map.values() if r.extraction_status == "mapped"
    ]
    return result, mapped_ids


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_RETRYABLE_STATUSES = frozenset({"failed", "mapped", "processing"})


async def reset_record_for_retry(db: AsyncSession, record_id: int) -> OrgChartUploadRecord:
    """Reset a failed/stuck record back to 'mapped' so extraction can be re-queued.

    Raises ValueError if the record cannot be retried (not found, wrong status,
    or not mapped to a company).
    """
    record = (
        await db.execute(select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        raise ValueError(f"OrgChartUploadRecord {record_id} not found")
    if record.extraction_status not in _RETRYABLE_STATUSES:
        raise ValueError(
            f"Record {record_id} cannot be retried (status={record.extraction_status!r}); "
            f"only {sorted(_RETRYABLE_STATUSES)} are retryable"
        )
    if record.portfolio_company_id is None:
        raise ValueError(
            f"Record {record_id} is not mapped to a PortfolioCompany — "
            "complete the mapping step before retrying extraction"
        )
    record.extraction_status = "mapped"
    record.error_message = None
    record.extracted_org_chart = None
    await db.commit()
    return record


# ---------------------------------------------------------------------------
# Batch / record queries
# ---------------------------------------------------------------------------

async def list_batches(
    db: AsyncSession,
    review_cycle_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OrgChartUploadBatch], int]:
    from sqlalchemy import func

    q = select(OrgChartUploadBatch)
    count_q = select(func.count()).select_from(OrgChartUploadBatch)
    if review_cycle_id:
        q = q.where(OrgChartUploadBatch.review_cycle_id == review_cycle_id)
        count_q = count_q.where(OrgChartUploadBatch.review_cycle_id == review_cycle_id)
    q = q.order_by(OrgChartUploadBatch.created_at.desc()).limit(limit).offset(offset)
    items = (await db.execute(q)).scalars().all()
    total = (await db.execute(count_q)).scalar_one()
    return list(items), total


async def get_batch(db: AsyncSession, batch_id: int) -> Optional[OrgChartUploadBatch]:
    return (
        await db.execute(select(OrgChartUploadBatch).where(OrgChartUploadBatch.id == batch_id))
    ).scalar_one_or_none()


async def get_batch_records(db: AsyncSession, batch_id: int) -> list[OrgChartUploadRecord]:
    rows = await db.execute(
        select(OrgChartUploadRecord)
        .where(OrgChartUploadRecord.batch_id == batch_id)
        .order_by(OrgChartUploadRecord.id)
    )
    return list(rows.scalars())
