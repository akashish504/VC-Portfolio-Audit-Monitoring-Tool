"""Audit file service — merged from portfolio-review-audit-backend-main."""

import asyncio
import copy
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Optional

from botocore.exceptions import ClientError
from fastapi import BackgroundTasks
from sqlalchemy import and_, select

from src.db.models import (
    AuditActorType,
    AuditEvent,
    Entity,
    FileOCRMetadata,
    FileUploadStatus,
    PortfolioCompany,
    PortfolioFile,
)
from src.schema.portfolio import CompanyReviewStage
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings
from src.db.session import AsyncSessionLocal
from src.db.file_filters import exclude_org_chart_uploads_clause
from src.services.docx_conversion import (
    key_with_replaced_filename,
    looks_like_docx,
    maybe_convert_to_pdf,
)
from src.services.financial_audit_schema import (
    apply_audit_financials_formulas,
    apply_schema_defaults_to_extracted,
    consolidate_financial_synonyms,
    finalize_audit_financials_extracted,
    ensure_nested_dict,
    list_assignable_parent_paths,
    list_audit_financials_numeric_leaf_paths,
    load_audit_financials_schema,
    set_value_at_dotted_path,
    slug_snake_label,
    sum_numeric_leaves_in_audit_subtree,
    validate_extracted_value_path,
    _get_value_at_dotted_path,
    _delete_dotted_path,
)
from src.services.extraction_currency_scale import scale_extracted_statement_amounts, scale_unmatched_amounts
from src.services.financial_data_extraction_sync import (
    build_mapping_breakdowns_for_read,
    persist_file_metric_breakdown,
    push_file_breakdown_to_reconciliation,
    resolve_primary_afs_file,
    sync_financial_data_for_file_id,
    sync_financial_data_from_audit_extraction,
)
from src.services.financial_metric_mapping import load_financial_metric_mapping_config
from src.services.manual_edit_marker import (
    ACTION_CURRENCY_CONVERT,
    ACTION_FIELD_COMPONENTS,
    ACTION_MAP_UNMATCHED,
    ACTION_VALUE_PATCH,
    build_marker,
    clear_marker,
    rescale_marker_previous_values,
    set_marker,
)
from src.services.fx_service import FxConversionUnavailable, FxService, fetch_historical_rate
from src.services.fy_end import normalize_fy_end, fy_end_last_day
from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR, format_audit_value
from src.services.company_audit_context import get_audit_actor_email
from src.services.financial_label_matching import (
    enrich_unmatched_rows,
    infer_component_sign,
    looks_like_total,
    prepare_label_matcher,
    signed_components_total,
    upsert_label_alias,
)
from src.llm.prompts import AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL
from src.utils.aws_credential_logging import log_bedrock_credential_audit
from src.utils.s3 import (
    delete_file as s3_delete_file,
    delete_file_best_effort as s3_delete_best_effort,
    download_storage_uri,
    generate_presigned_url,
    get_s3_client,
    parse_s3_uri,
    upload_file as s3_upload_file,
)

logger = logging.getLogger(__name__)


def _s3_key(company_id: Optional[int], file_id_str: str, file_name: str) -> str:
    prefix = f"companies/{company_id}" if company_id is not None else "unassigned"
    return f"{prefix}/files/{file_id_str}/{file_name.replace(' ', '_')}"


def _storage_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


async def _run(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


MAX_BULK_AUDIT_UPLOAD_FILES = 10
MAX_AUDIT_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_AUDIT_UPLOAD_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx", ".ppt"})


def normalize_upload_filename(name: str) -> str:
    return (name or "").strip()


def audit_upload_filename_key(name: str) -> str:
    return normalize_upload_filename(name).casefold()


def validate_audit_upload_filename(name: str) -> Optional[str]:
    """Return an error message if invalid, else None."""
    cleaned = normalize_upload_filename(name)
    if not cleaned:
        return "filename is empty"
    ext = Path(cleaned).suffix.lower()
    if ext not in ALLOWED_AUDIT_UPLOAD_EXTENSIONS:
        return f"unsupported file type {ext or '(none)'}; allowed: PDF, XLSX, DOCX, PPTX, PPT"
    return None


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    _STATUSES_BEFORE_IN_REVIEW = frozenset({
        CompanyReviewStage.NOT_APPLICABLE.value,
        CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value,
    })

    async def _maybe_advance_entity_to_in_review(
        self, entity_id: Optional[int]
    ) -> None:
        """Move an entity's status to In review on file attach, only if it is currently
        in a preceding state (Not applicable or Financials to be received). Any state at
        or beyond In review is left untouched. A file with no entity advances nothing,
        since audit state now lives per entity."""
        if entity_id is None:
            return
        ent = await self.db.get(Entity, entity_id)
        if ent is None or (ent.status or "").strip() not in self._STATUSES_BEFORE_IN_REVIEW:
            return
        before = {"status": ent.status}
        ent.status = CompanyReviewStage.IN_REVIEW.value
        await self.db.flush()
        after = {"status": ent.status}
        changes = CompanyAuditRecorder.diff_snapshots(before, after)
        await CompanyAuditRecorder(self.db).log_company_field_changes(
            portfolio_company_id=ent.portfolio_company_id,
            changes=changes,
            entity_type="entity",
            entity_id=ent.id,
        )

    async def _check_duplicate_files(
        self,
        entity_id: Optional[int],
        review_cycle_id: Optional[str],
        exclude_file_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Return a duplicate_warning dict if non-deleted files already exist for
        the same (entity_id, review_cycle_id), else None.

        Only fires when both entity_id and review_cycle_id are provided — without
        both dimensions the check would be too broad.
        """
        if entity_id is None or not review_cycle_id:
            return None

        filters = [
            PortfolioFile.entity_id == entity_id,
            PortfolioFile.review_cycle_id == review_cycle_id,
            PortfolioFile.status != FileUploadStatus.DELETED,
        ]
        if exclude_file_id is not None:
            filters.append(PortfolioFile.id != exclude_file_id)

        existing = (
            await self.db.execute(
                select(PortfolioFile).where(and_(*filters)).order_by(PortfolioFile.created_at.desc())
            )
        ).scalars().all()

        if not existing:
            return None

        return {
            "duplicate_warning": True,
            "message": (
                "An audit file already exists for this entity and review cycle."
            ),
            "existing_files": [
                {"file_id": f.id, "filename": f.filename, "status": f.status}
                for f in existing
            ],
        }

    async def generate_upload_url(
        self,
        company_id: Optional[int],
        file_name: str,
        mime_type: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        checksum_sha256: Optional[str] = None,
        entity_id: Optional[int] = None,
        review_cycle_id: Optional[str] = None,
        fy_end: Optional[str] = None,
        source_email_id: Optional[str] = None,
        source_attachment_key: Optional[str] = None,
    ) -> dict:
        """Register a pending file row and S3 key; client must POST bytes to POST /files/{id}/upload."""
        del uploaded_by
        file_id_str = str(uuid.uuid4())
        resolved_mime = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        s3_key = _s3_key(company_id, file_id_str, file_name)
        bucket = settings.S3_BUCKET
        uri = _storage_uri(bucket, s3_key)

        tags: list = []
        if checksum_sha256:
            tags.append(f"sha256:{checksum_sha256}")

        if entity_id is not None:
            ent_row = (await self.db.execute(select(Entity).where(Entity.id == entity_id))).scalar_one_or_none()
            if not ent_row:
                raise ValueError("Entity not found")
            if company_id is not None and ent_row.portfolio_company_id != company_id:
                raise ValueError("Entity does not belong to the selected portfolio company")
            if fy_end:
                normalized_fy = normalize_fy_end(fy_end)
                if not normalized_fy:
                    raise ValueError(f"invalid fy_end: {fy_end!r}")
                ent_row.fy_end = normalized_fy

        duplicate_warning = await self._check_duplicate_files(entity_id, review_cycle_id)

        row = PortfolioFile(
            portfolio_company_id=company_id,
            entity_id=entity_id,
            review_cycle_id=review_cycle_id,
            filename=file_name,
            content_type=resolved_mime,
            storage_uri=uri,
            status=FileUploadStatus.PENDING,
            tags=tags,
            source_email_id=source_email_id,
            source_attachment_key=source_attachment_key,
        )

        self.db.add(row)
        await self.db.flush()

        await CompanyAuditRecorder(self.db).log_file(
            row,
            action=f'File "{row.filename}" registered for upload',
            meta={"event": "file.registered", "status": row.status},
        )
        await self._maybe_advance_entity_to_in_review(entity_id)

        await self.db.commit()
        await self.db.refresh(row)

        logger.info("Pending upload registered file_id=%s key=%s (use POST /files/{id}/upload)", row.id, s3_key)
        result: dict = {"file_id": row.id, "s3_key": s3_key}
        if duplicate_warning:
            result.update(duplicate_warning)
        return result

    async def confirm_upload(
        self,
        file_id: int,
        size_bytes: Optional[int] = None,
        page_count: Optional[int] = None,
        language: Optional[str] = None,
    ) -> PortfolioFile:
        del page_count, language

        file_obj = await self._get_file_or_raise(file_id)
        if file_obj.status != FileUploadStatus.PENDING:
            raise ValueError(f"File {file_id} is already '{file_obj.status}'")

        if not file_obj.storage_uri:
            raise ValueError("File has no storage_uri")

        bucket, key = parse_s3_uri(file_obj.storage_uri)

        def _head():
            return get_s3_client().head_object(Bucket=bucket, Key=key)

        try:
            head_resp = await _run(_head)
        except ClientError:
            file_obj.status = FileUploadStatus.FAILED
            await CompanyAuditRecorder(self.db).log_file(
                file_obj,
                action=f'Upload confirmation failed — file not found in S3',
                meta={
                    "event": "file.upload_confirm_s3_missing",
                    "status_before": FileUploadStatus.PENDING,
                    "status_after": FileUploadStatus.FAILED,
                },
            )
            await self.db.commit()
            raise FileNotFoundError(f"File {file_id} not found in S3 after confirm") from None

        if looks_like_docx(file_obj.filename, file_obj.content_type):

            def _replace_pending_docx_with_pdf():
                data = download_storage_uri(file_obj.storage_uri)
                out_bytes, out_name, out_ct = maybe_convert_to_pdf(data, file_obj.filename, file_obj.content_type)
                new_key = key_with_replaced_filename(key, out_name)
                s3_upload_file(
                    out_bytes,
                    new_key,
                    out_ct,
                    bucket=bucket,
                    bucket_env_var_name="S3_BUCKET",
                )
                if new_key != key:
                    s3_delete_best_effort(key, bucket=bucket, bucket_env_var_name="S3_BUCKET")
                return out_name, out_ct, _storage_uri(bucket, new_key), len(out_bytes)

            fn, ct, uri, sz = await _run(_replace_pending_docx_with_pdf)
            file_obj.filename = fn
            file_obj.content_type = ct
            file_obj.storage_uri = uri
            file_obj.size_bytes = sz
        else:
            cl = head_resp.get("ContentLength")
            if cl is not None:
                file_obj.size_bytes = int(cl)
            elif size_bytes is not None and size_bytes > 0:
                file_obj.size_bytes = size_bytes

        file_obj.status = FileUploadStatus.UPLOADED
        self.db.add(
            FileOCRMetadata(
                file_id=file_obj.id,
                ocr_json={"queued": True, "pipeline": "ocr"},
            )
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'File "{file_obj.filename}" upload confirmed',
            meta={"event": "file.upload_confirmed", "size_bytes": file_obj.size_bytes},
        )
        await self._maybe_advance_entity_to_in_review(file_obj.entity_id)
        await self.db.commit()
        await self.db.refresh(file_obj)
        logger.info("File confirmed file_id=%s status=uploaded", file_obj.id)
        return file_obj

    async def upload_file_direct(
        self,
        company_id: Optional[int],
        file_name: str,
        file_bytes: bytes,
        mime_type: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        page_count: Optional[int] = None,
        language: Optional[str] = None,
        extra_tags: Optional[list[str]] = None,
    ) -> PortfolioFile:
        del uploaded_by, page_count, language
        file_id_str = str(uuid.uuid4())
        guessed_mime = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        file_bytes, file_name, resolved_mime = await _run(maybe_convert_to_pdf, file_bytes, file_name, guessed_mime)
        s3_key = _s3_key(company_id, file_id_str, file_name)
        bucket = settings.S3_BUCKET
        uri = _storage_uri(bucket, s3_key)

        await _run(s3_upload_file, file_bytes, s3_key, resolved_mime, bucket=bucket, bucket_env_var_name="S3_BUCKET")

        row = PortfolioFile(
            portfolio_company_id=company_id,
            filename=file_name,
            content_type=resolved_mime,
            storage_uri=uri,
            size_bytes=len(file_bytes),
            status=FileUploadStatus.UPLOADED,
            tags=list(extra_tags or []),
        )
        self.db.add(row)
        await self.db.flush()
        self.db.add(
            FileOCRMetadata(
                file_id=row.id,
                ocr_json={"queued": True, "pipeline": "ocr"},
            )
        )
        await CompanyAuditRecorder(self.db).log_file(
            row,
            action=f'File "{row.filename}" uploaded',
            meta={"event": "file.uploaded", "tags": list(row.tags or [])},
        )
        # Direct upload creates a company-level file with no entity; nothing to advance.
        await self._maybe_advance_entity_to_in_review(row.entity_id)
        await self.db.commit()
        await self.db.refresh(row)
        logger.info("Direct upload complete file_id=%s", row.id)
        return row

    async def upload_to_existing(
        self,
        *,
        file_id: int,
        file_bytes: bytes,
        content_type: Optional[str] = None,
    ) -> PortfolioFile:
        file_obj = await self._get_file_or_raise(file_id)
        if not file_obj.storage_uri:
            raise ValueError("File has no storage_uri")
        bucket, key = parse_s3_uri(file_obj.storage_uri)
        resolved_ct = content_type or file_obj.content_type or "application/octet-stream"
        out_bytes, out_name, out_ct = await _run(maybe_convert_to_pdf, file_bytes, file_obj.filename, resolved_ct)
        new_key = key_with_replaced_filename(key, out_name)
        await _run(s3_upload_file, out_bytes, new_key, out_ct, bucket=bucket, bucket_env_var_name="S3_BUCKET")
        if new_key != key:
            await _run(s3_delete_best_effort, key, bucket=bucket, bucket_env_var_name="S3_BUCKET")
        file_obj.filename = out_name
        file_obj.content_type = out_ct
        file_obj.storage_uri = _storage_uri(bucket, new_key)
        file_obj.size_bytes = len(out_bytes)
        file_obj.status = FileUploadStatus.UPLOADED
        has_meta = (
            await self.db.execute(select(FileOCRMetadata.id).where(FileOCRMetadata.file_id == file_id))
        ).scalar_one_or_none()
        if has_meta is None:
            self.db.add(
                FileOCRMetadata(
                    file_id=file_obj.id,
                    ocr_json={"queued": True, "pipeline": "ocr"},
                )
            )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'File "{file_obj.filename}" uploaded',
            meta={"event": "file.uploaded", "tags": list(file_obj.tags or [])},
        )
        await self._maybe_advance_entity_to_in_review(file_obj.entity_id)
        await self.db.commit()
        await self.db.refresh(file_obj)
        return file_obj

    async def bulk_upload_audit_files(
        self,
        *,
        company_id: Optional[int],
        entity_id: Optional[int],
        files: list[tuple[str, bytes, Optional[str]]],
        review_cycle_id: Optional[str] = None,
        fy_end: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Upload multiple audit files sequentially to the same company (and optional entity).

        Each tuple is ``(filename, file_bytes, content_type)``. Failures on one file do not
        stop the rest. Returns summary counts and per-file results.
        """
        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0

        for filename, file_bytes, content_type in files:
            name = normalize_upload_filename(filename) or "upload"
            try:
                fn_err = validate_audit_upload_filename(name)
                if fn_err:
                    raise ValueError(fn_err)
                if len(file_bytes) > MAX_AUDIT_UPLOAD_BYTES:
                    raise ValueError("File too large (max 50MB). Use a smaller file or split the document.")

                init = await self.generate_upload_url(
                    company_id=company_id,
                    file_name=name,
                    mime_type=content_type,
                    entity_id=entity_id,
                    review_cycle_id=review_cycle_id,
                    fy_end=fy_end,
                )
                file_id = int(init["file_id"])
                await self.upload_to_existing(
                    file_id=file_id,
                    file_bytes=file_bytes,
                    content_type=content_type,
                )
                file_result: dict[str, Any] = {"filename": name, "status": "success", "file_id": file_id}
                if init.get("duplicate_warning"):
                    file_result["duplicate_warning"] = True
                    file_result["duplicate_warning_message"] = init.get("message")
                    file_result["existing_files"] = init.get("existing_files")
                results.append(file_result)
                succeeded += 1
            except ValueError as exc:
                msg = str(exc).strip() or "Invalid upload"
                if len(msg) > 500:
                    msg = msg[:500] + "…"
                results.append({"filename": name, "status": "failed", "error": msg})
                failed += 1
                logger.warning("bulk upload failed filename=%s error=%s", name, msg)
            except Exception:
                results.append({"filename": name, "status": "failed", "error": "Upload failed"})
                failed += 1
                logger.exception("bulk upload failed filename=%s", name)

        return {
            "total": len(files),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }

    async def queue_extraction(self, *, file_id: int, kind: str) -> dict:
        """Mark extraction as ``running`` and persist ``kind`` on OCR metadata.

        Also resets ``File.status`` back to ``UPLOADED`` when the file was previously
        ``PROCESSED`` or ``FAILED`` — without this the file-list API keeps returning the
        old terminal status while the new run is in flight, so the UI never shows
        'processing'.

        Prefer :func:`schedule_extraction_with_background` from HTTP handlers so the
        worker is always registered together with this state update.
        """
        file_obj = await self._get_file_or_raise(file_id)

        # Reset the file-row status so the list API reflects the in-flight state.
        if file_obj.status in (FileUploadStatus.PROCESSED, FileUploadStatus.FAILED):
            file_obj.status = FileUploadStatus.UPLOADED

        meta = file_obj.ocr_metadata
        if meta is None:
            meta = FileOCRMetadata(file_id=file_obj.id, ocr_json={})
            self.db.add(meta)
            await self.db.flush()
        ocr_json = dict(meta.ocr_json or {})
        # Preserve currency override; clear everything else from the previous run
        # so stale extracted data is not shown while the new run is in flight.
        kept = {k: ocr_json[k] for k in ("currency", "currency_source") if k in ocr_json}
        kept.update({"status": "running", "kind": kind})
        meta.ocr_json = kept
        meta.auditor_opinion = None
        meta.audit_qualitative = None
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'Extraction started ({kind}) for file "{file_obj.filename}"',
            meta={"event": "extraction.started", "kind": kind},
        )
        await self.db.commit()
        return await self.extraction_status(file_id=file_id)

    async def run_extraction_work(self, *, file_id: int, kind: str) -> None:
        """LLM extraction (separate session; used from background task). Updates ``ocr_json`` to completed/error."""
        log_bedrock_credential_audit(
            logger,
            context=f"run_extraction_work file_id={file_id} kind={kind!r}",
        )
        try:
            if kind == "org_chart":
                from src.services.org_chart_extraction import run_org_chart_extraction

                await run_org_chart_extraction(self.db, file_id)
            else:
                from src.services.audit_document_extraction import run_audit_document_extraction

                await run_audit_document_extraction(self.db, file_id, kind)
        except Exception as e:
            logger.exception("extraction failed file_id=%s kind=%s", file_id, kind)
            file_obj = await self._get_file_or_raise(file_id)
            meta = file_obj.ocr_metadata
            if meta is None:
                meta = FileOCRMetadata(file_id=file_obj.id, ocr_json={})
                self.db.add(meta)
                await self.db.flush()
            j = dict(meta.ocr_json or {})
            j.update(
                {
                    "status": "error",
                    "kind": kind,
                    "error_message": str(e)[:2000],
                }
            )
            meta.ocr_json = j
            file_obj.status = FileUploadStatus.FAILED
            await CompanyAuditRecorder(self.db).log_file(
                file_obj,
                action=f'Extraction failed ({kind}) for file "{file_obj.filename}"',
                meta={"event": "extraction.error", "kind": kind, "error_message": str(e)[:500]},
                actor_email=SYSTEM_ACTOR,
            )
            await self.db.commit()

    async def extraction_status(self, *, file_id: int) -> dict:
        file_obj = await self._get_file_or_raise(file_id)
        ocr = file_obj.ocr_metadata
        ocr_json = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
        status = str(ocr_json.get("status") or file_obj.status or "unknown")
        kind = str(ocr_json.get("kind") or "").strip().lower()
        if kind in ("audit_financials", "financials", "audit-financials"):
            ext = ocr_json.get("extracted")
            unmatched = ocr_json.get("audit_financials_unmatched")
            needs_schema = isinstance(ext, dict) or (isinstance(unmatched, list) and unmatched)
            if needs_schema:
                schema = await load_audit_financials_schema(self.db)
                ocr_json = dict(ocr_json)
                if isinstance(ext, dict):
                    ocr_json["extracted"] = finalize_audit_financials_extracted(copy.deepcopy(ext), schema)
                    # Per-metric mapping breakdown, computed with the SAME configured formula and
                    # shape the /company discrepancy dashboard uses (build_mapping_breakdowns_for_read).
                    # Lets the file page show EBITDA (and the other metrics) from the settings mapping
                    # with a contributor breakdown, instead of the raw LLM-derived value. Non-fatal.
                    try:
                        mapping = await load_financial_metric_mapping_config(self.db)
                        ocr_json["mapping_breakdown"] = build_mapping_breakdowns_for_read(
                            ocr_json["extracted"],
                            mapping,
                            currency=(
                                ocr_json.get("currency")
                                if isinstance(ocr_json.get("currency"), str)
                                else None
                            ),
                        )
                    except Exception as e:
                        logger.warning("mapping breakdown build failed (non-fatal): %s", str(e)[:300])
                # Enrich unmatched rows with canonical suggestions on read, so files extracted
                # before this shipped (and any alias learned since) get one-click attach too.
                if isinstance(unmatched, list) and unmatched:
                    try:
                        cand_idx, alias_idx = await prepare_label_matcher(self.db, schema)
                        # overwrite=True so suggestions reflect the latest learned aliases on every
                        # read, not just whatever was baked in at extraction time.
                        ocr_json["audit_financials_unmatched"] = enrich_unmatched_rows(
                            unmatched, candidate_index=cand_idx, aliases_index=alias_idx, overwrite=True
                        )
                    except Exception as e:
                        logger.warning("unmatched suggestion enrich failed (non-fatal): %s", str(e)[:300])
        if ocr is not None:
            ao = getattr(ocr, "auditor_opinion", None)
            aq = getattr(ocr, "audit_qualitative", None)
            ocr_json = dict(ocr_json)
            ocr_json["auditor_opinion"] = ao
            ocr_json["audit_qualitative"] = aq
        return {"file_id": file_obj.id, "status": status, "error_message": ocr_json.get("error_message"), "meta": ocr_json}

    async def get_file(self, file_id: int) -> PortfolioFile:
        return await self._get_file_or_raise(file_id)

    async def list_files(
        self,
        company_id: Optional[int] = None,
        upload_status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        filters = [
            PortfolioFile.status != FileUploadStatus.DELETED,
            exclude_org_chart_uploads_clause(),
        ]
        if company_id:
            filters.append(PortfolioFile.portfolio_company_id == company_id)
        if upload_status:
            filters.append(PortfolioFile.status == upload_status)

        all_rows = (
            await self.db.execute(select(PortfolioFile).where(and_(*filters)))
        ).scalars().all()
        total = len(all_rows)
        offset = (page - 1) * page_size
        items = (
            await self.db.execute(
                select(PortfolioFile)
                .where(and_(*filters))
                .order_by(PortfolioFile.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, -(-total // page_size)),
        }

    async def get_download_url(self, file_id: int, expires_in: int = 3600) -> dict:
        file_obj = await self._get_file_or_raise(file_id)
        # Do not block ``failed``: that status is used when **LLM extraction** errors, while the PDF may still
        # exist in S3 — users must be able to preview/download. Block only incomplete upload and soft-delete.
        blocked = (
            FileUploadStatus.PENDING,
            FileUploadStatus.DELETED,
        )
        if file_obj.status in blocked:
            raise ValueError(f"File {file_id} not available (status: {file_obj.status})")
        if not file_obj.storage_uri:
            raise ValueError("Missing storage_uri")
        bucket, key = parse_s3_uri(file_obj.storage_uri)
        url = await _run(generate_presigned_url, key, expires_in, bucket=bucket)
        return {
            "file_id": file_id,
            "download_url": url,
            "file_name": file_obj.filename,
            "content_type": file_obj.content_type,
            "expires_in": expires_in,
        }

    async def delete_file(self, file_id: int, deleted_by: Optional[int] = None) -> None:
        file_obj = await self._get_file_or_raise(file_id)
        if file_obj.storage_uri:
            try:
                bucket, key = parse_s3_uri(file_obj.storage_uri)
                await _run(s3_delete_file, key, bucket=bucket)
            except (ClientError, ValueError) as e:
                logger.warning("S3 delete failed for file %s, continuing: %s", file_id, e)

        file_obj.status = FileUploadStatus.DELETED
        self.db.add(
            AuditEvent(
                entity_type="file",
                entity_id=file_id,
                event_type="deleted",
                actor_type=AuditActorType.USER if deleted_by else AuditActorType.SYSTEM,
                actor_id=deleted_by,
                new_value_json={"status": FileUploadStatus.DELETED},
            )
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'File "{file_obj.filename}" marked deleted',
            meta={"event": "file.soft_deleted"},
        )
        await self.db.commit()
        logger.info("File deleted file_id=%s by=%s", file_id, deleted_by)

    async def link_file(
        self,
        file_id: int,
        entity_type: str,
        entity_id: int,
        linked_by: Optional[int] = None,
    ) -> dict:
        del linked_by
        file_obj = await self._get_file_or_raise(file_id)
        if entity_type == "entity":
            file_obj.entity_id = entity_id
        else:
            tag = f"link:{entity_type}:{entity_id}"
            tags = list(file_obj.tags or [])
            if tag not in tags:
                tags.append(tag)
            file_obj.tags = tags
        if entity_type == "entity":
            ent_label = await CompanyAuditRecorder(self.db).resolve_entity_label(entity_id)
            action = f'File "{file_obj.filename}" linked to {ent_label}'
            meta = {"event": "file.linked", "entity_type": entity_type, "entity_id": entity_id}
        else:
            action = f'File "{file_obj.filename}" linked to {entity_type} id {entity_id}'
            meta = {"event": "file.linked", "entity_type": entity_type, "entity_id": entity_id}
        await CompanyAuditRecorder(self.db).log_file(file_obj, action=action, meta=meta)

        # After linking an entity, trigger currency conversion on the extracted tree
        # (or re-conversion if re-attached to a different entity), then sync reconciliation.
        if entity_type == "entity":
            await self._apply_entity_currency_conversion(file_obj, entity_id)
            # Push the per-file breakdown (written above by _apply_entity_currency_conversion)
            # into FinancialMetricReconciliation unconditionally — this file was just explicitly
            # attached so its numbers should immediately be reflected on the dashboard.
            await push_file_breakdown_to_reconciliation(self.db, file_row=file_obj)

        duplicate_warning: Optional[dict] = None
        if entity_type == "entity":
            duplicate_warning = await self._check_duplicate_files(
                entity_id, file_obj.review_cycle_id, exclude_file_id=file_obj.id
            )

        await self.db.commit()
        await self.db.refresh(file_obj)
        result = {
            "file_id": file_obj.id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "status": file_obj.status,
            "tags": list(file_obj.tags or []),
        }
        if duplicate_warning:
            result.update(duplicate_warning)
        return result

    async def _apply_entity_currency_conversion(self, file_obj: PortfolioFile, entity_id: int) -> None:
        """Convert extracted tree to PortfolioCompany.currency (or INR) using entity fy_end
        historical rate, then re-sync FinancialMetricReconciliation rows.

        Called on every entity attach/re-attach so the numbers always reflect the
        correct target currency at the correct historical date.  Uses chain conversion
        (converts from whatever currency is currently stored).
        """
        from src.services.fx_inr_conversion import auto_convert_extracted_tree

        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            return

        ocr_json = dict(meta.ocr_json)
        kind = str(ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            return

        status = str(ocr_json.get("status") or "").strip().lower()
        if status not in ("completed", "processed"):
            return

        extracted_raw = ocr_json.get("extracted")
        if not isinstance(extracted_raw, dict):
            return

        src_cur = ocr_json.get("currency")
        if not isinstance(src_cur, str) or len(src_cur) != 3:
            return

        # Resolve entity fy_end.
        ent = await self.db.get(Entity, entity_id)
        if ent is None:
            return
        fy_end_str = normalize_fy_end(ent.fy_end)
        if not fy_end_str:
            logger.info(
                "_apply_entity_currency_conversion skipped file_id=%s entity_id=%s: no fy_end",
                file_obj.id, entity_id,
            )
            return
        try:
            fy_date = fy_end_last_day(fy_end_str)
        except ValueError:
            logger.warning(
                "_apply_entity_currency_conversion skipped file_id=%s: invalid fy_end %r",
                file_obj.id, fy_end_str,
            )
            return

        # Resolve target currency from PortfolioCompany.
        target_cur = "INR"
        if ent.portfolio_company_id is not None:
            pc = await self.db.get(PortfolioCompany, ent.portfolio_company_id)
            if pc is not None and pc.currency:
                target_cur = pc.currency.strip().upper()

        if src_cur == target_cur:
            # Currencies already match — still need to re-sync reconciliation in case
            # this is a fresh entity attach with no prior sync.
            await sync_financial_data_from_audit_extraction(
                self.db,
                file_row=file_obj,
                extracted=extracted_raw,
                currency=src_cur,
                is_audit_financials=True,
            )
            return

        converted_tree, new_cur, fx_meta = await auto_convert_extracted_tree(
            self.db,
            extracted_raw,
            from_currency=src_cur,
            target_currency=target_cur,
            fy_end_date=fy_date,
            log_context=f"file_id={file_obj.id} entity_id={entity_id}",
        )

        if fx_meta is not None:
            hist = [h for h in (ocr_json.get("extraction_currency_conversion_history") or []) if isinstance(h, dict)]
            hist.append(fx_meta)
            ocr_json["extracted"] = converted_tree
            ocr_json["currency"] = new_cur
            ocr_json["currency_source"] = "converted"
            ocr_json["extraction_currency_conversion_history"] = hist[-20:]
            _rate = fx_meta.get("rate")
            if isinstance(_rate, (int, float)) and _rate > 0:
                _um, _um_n = scale_unmatched_amounts(ocr_json.get("audit_financials_unmatched"), float(_rate))
                if _um_n:
                    ocr_json["audit_financials_unmatched"] = _um
            meta.ocr_json = ocr_json

        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=converted_tree if fx_meta else extracted_raw,
            currency=new_cur or src_cur,
            is_audit_financials=True,
        )

    async def get_audit_financial_parent_paths(self, file_id: Optional[int] = None) -> dict:
        """Dot paths to dict nodes and leaf slots for mapping unmatched lines.

        Combines the canonical schema's container paths with any custom composite (bucket)
        nodes the user created in this file's extracted tree, so user-added groups are
        selectable as attach/reattach targets — not just canonical ones.
        """
        schema = await load_audit_financials_schema(self.db)
        parent_paths = set(list_assignable_parent_paths(schema))
        if file_id is not None:
            file_obj = await self._get_file_or_raise(file_id)
            meta = file_obj.ocr_metadata
            if meta is not None and isinstance(meta.ocr_json, dict):
                extracted = meta.ocr_json.get("extracted")
                if isinstance(extracted, dict):
                    # Custom composites live only in extracted; merge their container paths in.
                    parent_paths.update(list_assignable_parent_paths(extracted))
        return {
            "parent_paths": sorted(parent_paths),
            "leaf_paths": list_audit_financials_numeric_leaf_paths(schema),
        }

    async def map_audit_financial_unmatched(
        self,
        file_id: int,
        *,
        unmatched_id: str,
        target_parent_path: str,
        target_key: Optional[str],
        confirm_overwrite: bool,
        conflict_mode: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Move one row from ``audit_financials_unmatched`` under ``extracted`` at
        ``target_parent_path`` + ``target_key`` (default: slug of document_label).

        When the target leaf is already occupied (several document lines → one canonical tag),
        we never silently overwrite. ``conflict_mode`` decides:
          - ``"sum"``     → append a signed roll-up component; leaf becomes Σ components (no loss).
          - ``"total"``   → the incoming line is the stated total; prior lines kept as breakdown.
          - ``"replace"`` → overwrite (legacy).
        When ``conflict_mode`` is ``None`` and the slot is occupied (and ``confirm_overwrite`` is
        false), returns ``{ conflict: True, existing_components, incoming, double_count_warning, ... }``
        without persisting, so the UI can ask. The signed breakdown is stored in
        ``ocr_json.audit_financials_field_components[full_path]``.
        """
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        ocr_json = dict(meta.ocr_json)
        kind = str(ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")
        extracted = copy.deepcopy(ocr_json.get("extracted") or {})
        if not isinstance(extracted, dict):
            raise ValueError("Invalid extracted payload")

        unmatched = list(ocr_json.get("audit_financials_unmatched") or [])

        # IDs prefixed "other:" identify items sitting inside an `other` catch-all bucket
        # in the extracted tree rather than in audit_financials_unmatched.
        if unmatched_id.startswith("other:"):
            # id format: "other:<parent_path>.<item_key>"  e.g. "other:profit_and_loss.other.my_item"
            dotted_path = unmatched_id[len("other:"):]
            parts = dotted_path.rsplit(".", 1)
            if len(parts) != 2:
                raise ValueError("Malformed other-bucket unmatched id")
            other_parent_path, item_key = parts
            other_bucket = _get_value_at_dotted_path(extracted, other_parent_path)
            if not isinstance(other_bucket, dict) or item_key not in other_bucket:
                raise ValueError("Other-bucket item not found or already moved")
            raw_val = other_bucket[item_key]
            item = {"document_label": item_key.replace("_", " "), "value": raw_val}
            # Remove from the other bucket
            del other_bucket[item_key]
        else:
            idx = next(
                (i for i, u in enumerate(unmatched) if isinstance(u, dict) and str(u.get("id")) == unmatched_id),
                None,
            )
            if idx is None:
                raise ValueError("Unmatched row not found or already mapped")
            item = unmatched[idx]
            unmatched.pop(idx)

        tk = (target_key or "").strip() or slug_snake_label(str(item.get("document_label") or ""))
        full_path = f"{target_parent_path}.{tk}"
        doc_label = str(item.get("document_label") or tk)

        raw_val = item.get("value")
        if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            val: Any = raw_val
        else:
            try:
                val = float(raw_val) if raw_val is not None else 0.0
            except (TypeError, ValueError):
                val = 0.0

        parent = ensure_nested_dict(extracted, target_parent_path)
        components_map = {
            k: [c for c in (v or []) if isinstance(c, dict)]
            for k, v in (ocr_json.get("audit_financials_field_components") or {}).items()
            if isinstance(v, list)
        }
        existing_components = list(components_map.get(full_path) or [])

        occupied = tk in parent and sum_numeric_leaves_in_audit_subtree(parent.get(tk)) is not None
        if occupied:
            # Seed a base component from the value already at the leaf so a sum keeps it.
            if not existing_components:
                base_num = sum_numeric_leaves_in_audit_subtree(parent.get(tk)) or 0.0
                existing_components = [
                    {
                        "id": uuid.uuid4().hex[:8],
                        "label": f"{tk.replace('_', ' ')} (existing)",
                        "value": float(base_num),
                        "sign": "+",
                        "source": "extracted",
                    }
                ]
            incoming_sign = infer_component_sign(doc_label)
            warning = looks_like_total(doc_label, val, existing_components)

            # No resolution chosen yet → ask the UI. Nothing is persisted on this return.
            if conflict_mode is None and not confirm_overwrite:
                return {
                    "conflict": True,
                    "unmatched_id": unmatched_id,
                    "target_parent_path": target_parent_path,
                    "target_key": tk,
                    "full_path": full_path,
                    "existing_value": parent[tk],
                    "existing_components": existing_components,
                    "incoming": {
                        "document_label": doc_label,
                        "value": val,
                        "inferred_sign": incoming_sign,
                    },
                    "double_count_warning": warning,
                }

            mode = conflict_mode or ("replace" if confirm_overwrite else "sum")
            if mode == "replace":
                parent[tk] = val
                components_map.pop(full_path, None)
            elif mode == "total":
                # Incoming line is the stated total; keep prior lines as its breakdown.
                parent[tk] = val
                components_map[full_path] = existing_components
            else:  # "sum"
                comps = list(existing_components)
                comps.append(
                    {
                        "id": uuid.uuid4().hex[:8],
                        "label": doc_label,
                        "value": float(val),
                        "sign": incoming_sign,
                        "source": "mapped",
                    }
                )
                components_map[full_path] = comps
                parent[tk] = signed_components_total(comps)
            applied_mode = mode
        else:
            parent[tk] = val
            applied_mode = "set"

        schema = await load_audit_financials_schema(self.db)
        extracted = finalize_audit_financials_extracted(extracted, schema)

        # After synonym consolidation the value may have moved to a canonical alias path.
        # Detect the effective path so the frontend can inform the user where the field landed.
        effective_path = full_path
        if _get_value_at_dotted_path(extracted, full_path) is None:
            from .financial_audit_schema import (  # noqa: PLC0415
                _EXPENSES_BUCKET_SYNONYMS,
                _FINANCIAL_PATH_SYNONYMS,
                _PROFIT_AND_LOSS_ROOT_SYNONYMS,
                _REVENUE_BUCKET_SYNONYMS,
                _TAX_EXPENSE_BUCKET_SYNONYMS,
            )
            _found = False
            for group in (
                *_PROFIT_AND_LOSS_ROOT_SYNONYMS,
                *_REVENUE_BUCKET_SYNONYMS,
                *_EXPENSES_BUCKET_SYNONYMS,
                *_TAX_EXPENSE_BUCKET_SYNONYMS,
            ):
                if tk in group:
                    candidate = f"{target_parent_path}.{group[0]}"
                    if _get_value_at_dotted_path(extracted, candidate) is not None:
                        effective_path = candidate
                        _found = True
                        break
            if not _found:
                for group in _FINANCIAL_PATH_SYNONYMS:
                    if full_path in group:
                        candidate = group[0]
                        if _get_value_at_dotted_path(extracted, candidate) is not None:
                            effective_path = candidate
                            break

        j = dict(meta.ocr_json or {})
        j["extracted"] = extracted
        j["audit_financials_unmatched"] = unmatched
        j["audit_financials_field_components"] = components_map
        # Carry the line's located page reference onto the now-canonical field, so the mapped value
        # keeps its "view source" link. Additive — no-op when the line had no located ref.
        #  - unmatched-array rows carry the ref directly on the row (``source_ref``);
        #  - ``other:`` overflow items already had a ref in the source_refs map under their original
        #    dotted path, so re-key it to the new path (and drop the stale old key).
        _existing_refs = dict(j.get("source_refs") or {})
        _carried_ref = None
        if unmatched_id.startswith("other:"):
            _orig_path = unmatched_id[len("other:"):]
            _carried_ref = _existing_refs.get(_orig_path)
            if isinstance(_carried_ref, dict) and isinstance(_carried_ref.get("page"), int):
                _existing_refs.pop(_orig_path, None)
        elif isinstance(item, dict):
            _carried_ref = item.get("source_ref")
        if isinstance(_carried_ref, dict) and isinstance(_carried_ref.get("page"), int):
            _existing_refs[full_path] = _carried_ref
            j["source_refs"] = _existing_refs
        # Manually mapping an unmatched line into the canonical tree is a manual edit — flag the
        # destination leaf so it shows the "edited manually" badge + justification. Non-fatal.
        try:
            set_marker(
                j,
                full_path,
                build_marker(
                    action=ACTION_MAP_UNMATCHED,
                    reason=reason,
                    actor=get_audit_actor_email(),
                    previous_value=None,
                    new_value=_get_value_at_dotted_path(extracted, full_path),
                ),
            )
        except Exception as e:  # pragma: no cover - marker must never block the edit
            logger.warning("manual-edit marker write failed (non-fatal): %s", str(e)[:200])
        meta.ocr_json = j
        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=j.get("currency") if isinstance(j.get("currency"), str) else None,
            is_audit_financials=True,
        )
        _mode_phrase = {
            "sum": "added (summed) into",
            "total": "set as the total of",
            "replace": "mapped (replacing) to",
            "set": "mapped to",
        }.get(applied_mode, "mapped to")
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=(
                f'Mapped unmatched line "{doc_label}" {_mode_phrase} `{full_path}` '
                f'on file "{file_obj.filename}"'
            ),
            meta={
                "event": "extraction.unmatched_mapped",
                "unmatched_id": unmatched_id,
                "target_parent_path": target_parent_path,
                "target_key": tk,
                "value": val,
                "conflict_mode": applied_mode,
                "leaf_value": _get_value_at_dotted_path(extracted, full_path),
                "document_label": doc_label,
            },
        )
        # Self-learning (suggest tier): remember this reviewer-confirmed wording so the next
        # document with the same label resolves deterministically. Best-effort, never fatal.
        try:
            section = target_parent_path.split(".")[0]
            await upsert_label_alias(
                self.db,
                document_label=doc_label,
                section_hint=section if section in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL else None,
                parent_path=target_parent_path,
                key=tk,
                actor=get_audit_actor_email(),
            )
        except Exception as e:
            logger.warning("label alias capture failed (non-fatal): %s", str(e)[:300])
        await self.db.commit()
        await self.db.refresh(meta)
        return {
            "file_id": file_id,
            "extracted": extracted,
            "audit_financials_unmatched": unmatched,
            "audit_financials_field_components": components_map,
            "conflict_mode": applied_mode,
            "source_refs": j.get("source_refs") or {},
            "intended_path": full_path,
            "effective_path": effective_path,
        }

    async def detach_audit_financial_field(self, file_id: int, *, path: str) -> dict[str, Any]:
        """Remove a field from the extracted tree and move it to the unmatched panel.

        The inverse of :meth:`map_audit_financial_unmatched` — a manual escape hatch for a
        duplicate / mis-mapped line the automatic dedupe missed. The field's value (the sum of
        its numeric subtree) is pulled out as a new ``audit_financials_unmatched`` row so it is
        recoverable (re-attachable), and any breakdown components / source-ref under the path are
        carried (source-ref) or dropped (components) accordingly. Derived totals (EBITDA) and the
        six reconciliation metrics recompute from the cleaned tree.
        """
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        ocr_json = dict(meta.ocr_json)
        kind = str(ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")

        path = (path or "").strip()
        parts = [p for p in path.split(".") if p]
        if len(parts) < 2 or ".." in path:
            raise ValueError("invalid path")
        if parts[0] not in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
            raise ValueError("unknown statement section")

        extracted = copy.deepcopy(ocr_json.get("extracted") or {})
        if not isinstance(extracted, dict):
            raise ValueError("Invalid extracted payload")

        node = _get_value_at_dotted_path(extracted, path)
        if node is None:
            raise ValueError("field not found")
        value = sum_numeric_leaves_in_audit_subtree(node)
        if value is None:
            raise ValueError("field has no numeric value to move")

        _delete_dotted_path(extracted, path)

        section = parts[0]
        leaf_key = parts[-1]
        doc_label = leaf_key.replace("_", " ")

        # New unmatched row (recoverable). Keep the id stable + unique within this file.
        unmatched = list(ocr_json.get("audit_financials_unmatched") or [])
        existing_ids = {str(u.get("id")) for u in unmatched if isinstance(u, dict)}
        new_id = "detached-" + path.replace(".", "-")
        if new_id in existing_ids:
            new_id = f"{new_id}-{uuid.uuid4().hex[:4]}"
        row: dict[str, Any] = {
            "id": new_id,
            "document_label": doc_label,
            "value": value,
            "section_hint": section,
            # The field came from a real canonical path, so its parent IS a valid section node:
            # tag it so the UI nests the detached line back inside the bucket it was pulled from
            # (Tier 2 — shown in-section, excluded from the Σ until re-attached). See
            # financial_section_tagging.assign_placement_paths.
            "placement_path": ".".join(parts[:-1]) if len(parts) >= 2 else None,
            "placement_tier": 2,
            "placement_source": "detach",
        }

        # Source-ref: carry the located page reference onto the unmatched row and drop the stale
        # map entries under this path (mirror of map's carry-on-attach, in reverse).
        refs = dict(ocr_json.get("source_refs") or {})
        carried = refs.get(path)
        if isinstance(carried, dict) and isinstance(carried.get("page"), int):
            row["source_ref"] = carried
        for rk in list(refs.keys()):
            if rk == path or rk.startswith(path + "."):
                refs.pop(rk, None)
        unmatched.append(row)

        # Drop any roll-up components for the removed path (and its descendants).
        components_map = {
            k: [c for c in (v or []) if isinstance(c, dict)]
            for k, v in (ocr_json.get("audit_financials_field_components") or {}).items()
            if isinstance(v, list)
        }
        for ck in list(components_map.keys()):
            if ck == path or ck.startswith(path + "."):
                components_map.pop(ck, None)

        # Finalize so derived totals recompute. Non-canonical keys (the usual duplicate case) are
        # NOT re-added by schema defaults; a canonical leaf reappears only as an empty placeholder.
        schema = await load_audit_financials_schema(self.db)
        extracted = finalize_audit_financials_extracted(extracted, schema)

        j = dict(meta.ocr_json or {})
        j["extracted"] = extracted
        j["audit_financials_unmatched"] = unmatched
        j["audit_financials_field_components"] = components_map
        j["source_refs"] = refs
        # The field no longer exists at this path — drop any stale manual-edit marker so it can't
        # resurface on a future re-use of the same path. Non-fatal.
        try:
            clear_marker(j, path)
        except Exception as e:  # pragma: no cover - marker must never block the edit
            logger.warning("manual-edit marker clear failed (non-fatal): %s", str(e)[:200])
        meta.ocr_json = j

        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=j.get("currency") if isinstance(j.get("currency"), str) else None,
            is_audit_financials=True,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=(
                f'Removed extracted field `{path}` ({format_audit_value(value)}) from '
                f'file "{file_obj.filename}" and moved it to the unmatched panel'
            ),
            meta={
                "event": "extraction.field_detached",
                "path": path,
                "value": value,
                "document_label": doc_label,
            },
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return {
            "file_id": file_id,
            "extracted": extracted,
            "audit_financials_unmatched": unmatched,
            "audit_financials_field_components": components_map,
            "source_refs": refs,
        }

    async def dismiss_audit_financial_unmatched(self, file_id: int, *, unmatched_id: str) -> dict[str, Any]:
        """Set ``dismissed: true`` on an unmatched row.

        The row stays in ``audit_financials_unmatched`` so it is always recoverable — the UI
        moves it to a collapsed 'dismissed' section rather than erasing it.
        """
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        unmatched: list[Any] = list(meta.ocr_json.get("audit_financials_unmatched") or [])
        idx = next(
            (i for i, u in enumerate(unmatched) if isinstance(u, dict) and str(u.get("id")) == unmatched_id),
            None,
        )
        if idx is None:
            raise ValueError("Unmatched row not found")
        row = dict(unmatched[idx])
        row["dismissed"] = True
        unmatched[idx] = row
        j = dict(meta.ocr_json)
        j["audit_financials_unmatched"] = unmatched
        meta.ocr_json = j
        await self.db.commit()
        await self.db.refresh(meta)
        return {"file_id": file_id, "audit_financials_unmatched": unmatched}

    async def restore_audit_financial_unmatched(self, file_id: int, *, unmatched_id: str) -> dict[str, Any]:
        """Clear ``dismissed`` on a previously dismissed unmatched row."""
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        unmatched: list[Any] = list(meta.ocr_json.get("audit_financials_unmatched") or [])
        idx = next(
            (i for i, u in enumerate(unmatched) if isinstance(u, dict) and str(u.get("id")) == unmatched_id),
            None,
        )
        if idx is None:
            raise ValueError("Unmatched row not found")
        row = dict(unmatched[idx])
        row.pop("dismissed", None)
        unmatched[idx] = row
        j = dict(meta.ocr_json)
        j["audit_financials_unmatched"] = unmatched
        meta.ocr_json = j
        await self.db.commit()
        await self.db.refresh(meta)
        return {"file_id": file_id, "audit_financials_unmatched": unmatched}

    async def set_audit_financial_field_components(
        self,
        file_id: int,
        *,
        path: str,
        components: list[dict[str, Any]],
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Replace the roll-up breakdown for a canonical leaf; set the leaf to the signed sum.

        ``components`` is a list of ``{label, value, sign}``. An empty list clears the
        breakdown and zeroes the leaf. Reversible by design — the reviewer can drop a wrong
        contributor or flip a sign here, and the leaf (and the six metrics) recompute.
        """
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        ocr_json = dict(meta.ocr_json)
        kind = str(ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")

        extracted = copy.deepcopy(ocr_json.get("extracted") or {})
        if not isinstance(extracted, dict):
            raise ValueError("Invalid extracted payload")

        schema = await load_audit_financials_schema(self.db)
        validate_extracted_value_path(schema, path)  # raises ValueError on a bad/foreign path

        clean: list[dict[str, Any]] = []
        for c in components or []:
            if not isinstance(c, dict):
                continue
            label = str(c.get("label") or "").strip()
            sign = "-" if c.get("sign") == "-" else "+"
            v = c.get("value")
            try:
                value = float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            if not label:
                continue
            clean.append(
                {
                    "id": str(c.get("id") or uuid.uuid4().hex[:8]),
                    "label": label,
                    "value": value,
                    "sign": sign,
                    "source": str(c.get("source") or "manual"),
                }
            )

        components_map = {
            k: [c for c in (val or []) if isinstance(c, dict)]
            for k, val in (ocr_json.get("audit_financials_field_components") or {}).items()
            if isinstance(val, list)
        }

        parts = path.split(".")
        parent = ensure_nested_dict(extracted, ".".join(parts[:-1]))
        leaf_key = parts[-1]
        if clean:
            components_map[path] = clean
            parent[leaf_key] = signed_components_total(clean)
        else:
            components_map.pop(path, None)
            parent[leaf_key] = 0.0

        extracted = finalize_audit_financials_extracted(extracted, schema)

        j = dict(meta.ocr_json or {})
        j["extracted"] = extracted
        j["audit_financials_field_components"] = components_map
        # Editing a leaf's contributors is a manual edit — flag it (or clear when the breakdown is
        # emptied and the leaf zeroed). Non-fatal.
        try:
            if clean:
                set_marker(
                    j,
                    path,
                    build_marker(
                        action=ACTION_FIELD_COMPONENTS,
                        reason=reason,
                        actor=get_audit_actor_email(),
                        previous_value=None,
                        new_value=_get_value_at_dotted_path(extracted, path),
                    ),
                )
            else:
                clear_marker(j, path)
        except Exception as e:  # pragma: no cover - marker must never block the edit
            logger.warning("manual-edit marker write failed (non-fatal): %s", str(e)[:200])
        meta.ocr_json = j
        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=j.get("currency") if isinstance(j.get("currency"), str) else None,
            is_audit_financials=True,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'Edited roll-up breakdown for `{path}` on file "{file_obj.filename}"',
            meta={
                "event": "extraction.field_components_edited",
                "path": path,
                "component_count": len(clean),
                "leaf_value": _get_value_at_dotted_path(extracted, path),
            },
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return {
            "file_id": file_id,
            "extracted": extracted,
            "audit_financials_field_components": components_map,
        }

    async def set_extraction_currency(self, *, file_id: int, currency: str) -> dict:
        """Manually set the currency label on a file's extraction metadata.

        Stores the value at ``ocr_json.currency`` and marks the origin via
        ``ocr_json.currency_source = "manual"`` so the UI can distinguish a manual
        override from an LLM-detected value. Purely a display-side label; no numbers
        are rewritten or converted.
        """
        code = (currency or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("currency must be a 3-letter ISO-4217 code")
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        ocr_json = dict(meta.ocr_json)
        before_currency = ocr_json.get("currency")
        ocr_json["currency"] = code
        ocr_json["currency_source"] = "manual"
        meta.ocr_json = ocr_json
        file_obj.original_currency = code
        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=ocr_json.get("extracted"),
            currency=code,
            is_audit_financials=True,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=(
                f'Reporting currency for file "{file_obj.filename}" updated from '
                f"{format_audit_value(before_currency)} → {code}"
            ),
            meta={"event": "extraction.currency_updated", "before": before_currency, "after": code},
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return await self.extraction_status(file_id=file_id)

    async def apply_extraction_currency_conversion(self, *, file_id: int, target_currency: str) -> dict:
        """Scale BS / P&amp;L / CFS numeric leaves by live FX rate; persist currency; sync FinancialData."""
        from fastapi import HTTPException

        tgt = (target_currency or "").strip().upper()
        if len(tgt) != 3 or not tgt.isalpha():
            raise ValueError("target_currency must be a 3-letter ISO-4217 code")

        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")

        kind = str(meta.ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")

        status = str(meta.ocr_json.get("status") or "").strip().lower()
        if status not in ("completed", "processed"):
            raise ValueError("Extraction must be finished before converting currency")

        ocr_json = dict(meta.ocr_json)
        cu = ocr_json.get("currency")
        src_currency = ""
        if isinstance(cu, str):
            src_currency = cu.strip().upper()
        if len(src_currency) != 3 or not src_currency.isalpha():
            raise ValueError("Set a reporting currency on this file before converting amounts")

        if src_currency == tgt:
            raise ValueError("Target currency matches current reporting currency")

        extracted_raw = ocr_json.get("extracted")
        if not isinstance(extracted_raw, dict):
            raise ValueError("Invalid extracted payload")

        # Resolve fy_end from the connected entity.
        entity_fy_end: Optional[str] = None
        if file_obj.entity_id is not None:
            entity_row = await self.db.get(Entity, file_obj.entity_id)
            if entity_row is not None:
                entity_fy_end = normalize_fy_end(entity_row.fy_end)
        if not entity_fy_end:
            raise ValueError(
                "Cannot convert currency: the file's entity has no financial year end (fy_end) set."
            )
        try:
            fy_date = fy_end_last_day(entity_fy_end)
        except ValueError:
            raise ValueError(
                f"Cannot convert currency: entity fy_end '{entity_fy_end}' is not a valid FY end."
            )

        schema = await load_audit_financials_schema(self.db)
        extracted_working = apply_schema_defaults_to_extracted(copy.deepcopy(extracted_raw), schema)

        from datetime import datetime, time, timezone as _tz
        at_dt = datetime.combine(fy_date, time(12, 0, 0), tzinfo=_tz.utc)
        try:
            rate, fx_ts = await fetch_historical_rate(
                self.db, from_currency=src_currency, to_currency=tgt, at=at_dt
            )
        except FxConversionUnavailable as exc:
            raise ValueError(
                f"Historical FX rate unavailable for {src_currency}→{tgt} at {fy_date.isoformat()}."
            ) from exc
        except HTTPException as exc:
            detail = getattr(exc, "detail", "")
            raise ValueError(detail if isinstance(detail, str) else "FX lookup failed") from exc

        extracted_scaled, scaled_count = scale_extracted_statement_amounts(extracted_working, rate)
        extracted_final = apply_audit_financials_formulas(consolidate_financial_synonyms(extracted_scaled))

        if scaled_count < 1:
            raise ValueError("No convertible numeric amounts were found under P&L / balance sheet / cash flow.")

        hist_entry: dict[str, Any] = {
            "from": src_currency,
            "to": tgt,
            "rate": rate,
            "fx_timestamp": fx_ts,
            "scaled_numeric_leaves": scaled_count,
        }
        hist = [h for h in (ocr_json.get("extraction_currency_conversion_history") or []) if isinstance(h, dict)]
        hist.append(hist_entry)

        j = dict(ocr_json)
        j["extracted"] = extracted_final
        j["currency"] = tgt
        j["currency_source"] = "converted"
        j["extraction_currency_conversion_history"] = hist[-20:]
        _um, _um_n = scale_unmatched_amounts(j.get("audit_financials_unmatched"), rate)
        if _um_n:
            j["audit_financials_unmatched"] = _um
        # All numeric leaves were scaled by ``rate`` — rescale any manual-edit markers' stored
        # ``previous_value`` so the "edited manually" popup shows the figure in the new currency,
        # not the pre-conversion one. Non-fatal. (The conversion itself is already audited above.)
        try:
            rescale_marker_previous_values(j, rate)
        except Exception as e:  # pragma: no cover - marker must never block the conversion
            logger.warning("manual-edit marker rescale failed (non-fatal): %s", str(e)[:200])
        meta.ocr_json = j

        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=extracted_final,
            currency=tgt,
            is_audit_financials=True,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=(
                f'Converted extracted statement amounts on "{file_obj.filename}" '
                f"{src_currency} → {tgt} at rate {rate:.6f}; {scaled_count} value(s); reporting currency updated."
            ),
            meta={"event": "extraction.currency_conversion.applied", **hist_entry},
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return await self.extraction_status(file_id=file_id)

    async def patch_audit_financial_extracted_value(
        self, *, file_id: int, path: str, value: Any, reason: Optional[str] = None
    ) -> dict:
        """Set a numeric (or null) leaf on ``ocr_json.extracted`` for audit_financials files."""
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        kind = str(meta.ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")
        schema = await load_audit_financials_schema(self.db)
        extracted = copy.deepcopy(meta.ocr_json.get("extracted") or {})
        if not isinstance(extracted, dict):
            raise ValueError("Invalid extracted payload")
        # Validate against this file's ACTUAL tree, not just the global schema: a schema
        # overlay may mark a field (e.g. property_plant_and_equipment) as a bucket while
        # this file extracted it as a scalar leaf, which would otherwise block a null/value patch.
        validate_extracted_value_path(schema, path, extracted=extracted)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("value must be a number or null")

        extracted = apply_schema_defaults_to_extracted(extracted, schema)
        before_val = _get_value_at_dotted_path(extracted, path)
        set_value_at_dotted_path(extracted, path, value)
        extracted = consolidate_financial_synonyms(extracted)
        extracted = apply_audit_financials_formulas(extracted)

        j = dict(meta.ocr_json or {})
        j["extracted"] = extracted
        # Manual-edit marker drives the "edited manually" badge + justification popup on read.
        # Setting a value marks the leaf; clearing it (null) removes the marker. Non-fatal.
        try:
            if value is None:
                clear_marker(j, path)
            else:
                set_marker(
                    j,
                    path,
                    build_marker(
                        action=ACTION_VALUE_PATCH,
                        reason=reason,
                        actor=get_audit_actor_email(),
                        previous_value=before_val,
                        new_value=value,
                    ),
                )
        except Exception as e:  # pragma: no cover - marker must never block the edit
            logger.warning("manual-edit marker write failed (non-fatal): %s", str(e)[:200])
        meta.ocr_json = j
        _currency = j.get("currency") if isinstance(j.get("currency"), str) else None
        # Always update the per-file breakdown so the file-level view is immediately
        # correct even when this file is not the primary AFS reconciliation source.
        await persist_file_metric_breakdown(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=_currency,
        )
        await sync_financial_data_from_audit_extraction(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=_currency,
            is_audit_financials=True,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=(
                f'Extracted field `{path}` on file "{file_obj.filename}" updated from '
                f"{format_audit_value(before_val)} → {format_audit_value(value)}"
            ),
            meta={
                "event": "extraction.field_updated",
                "path": path,
                "before": before_val,
                "after": value,
            },
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return await self.extraction_status(file_id=file_id)

    async def add_audit_financial_composite_field(
        self, *, file_id: int, path: str, reason: Optional[str] = None
    ) -> dict:
        """Create an empty composite (bucket {}) node at ``path`` in the extracted tree."""
        file_obj = await self._get_file_or_raise(file_id)
        meta = file_obj.ocr_metadata
        if meta is None or not isinstance(meta.ocr_json, dict):
            raise ValueError("No extraction metadata for this file")
        kind = str(meta.ocr_json.get("kind") or "").strip().lower()
        if kind not in ("audit_financials", "financials", "audit-financials"):
            raise ValueError("File is not an audit_financials extraction")
        parts = [p for p in path.strip().split(".") if p]
        if len(parts) < 2:
            raise ValueError("path must include a statement section and a field name")
        schema = await load_audit_financials_schema(self.db)
        section = parts[0]
        if section not in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
            raise ValueError(f"Unknown statement section: {section!r}")
        # Validate parent exists and is a dict container (not a scalar slot).
        # A schema-None slot is allowed here: the caller may be converting a canonical scalar
        # into a composite by pre-clearing it and then creating the bucket.
        parent_parts = parts[:-1]
        cur: Any = schema.get(section)
        for pk in parent_parts[1:]:
            if not isinstance(cur, dict):
                raise ValueError(f"Path segment {pk!r} is not a container")
            nxt = cur.get(pk)
            if nxt is None:
                cur = {}
                continue
            cur = nxt
        extracted = copy.deepcopy(meta.ocr_json.get("extracted") or {})
        if not isinstance(extracted, dict):
            raise ValueError("Invalid extracted payload")
        existing = _get_value_at_dotted_path(extracted, path)
        if isinstance(existing, dict):
            raise ValueError(f"A composite field already exists at {path!r}")
        if existing is not None:
            raise ValueError(f"A scalar value already exists at {path!r}; detach it first")
        set_value_at_dotted_path(extracted, path, {})
        j = dict(meta.ocr_json or {})
        j["extracted"] = extracted
        meta.ocr_json = j
        _currency = j.get("currency") if isinstance(j.get("currency"), str) else None
        await persist_file_metric_breakdown(
            self.db,
            file_row=file_obj,
            extracted=extracted,
            currency=_currency,
        )
        await CompanyAuditRecorder(self.db).log_file(
            file_obj,
            action=f'Composite field `{path}` created on file "{file_obj.filename}"',
            meta={"event": "extraction.composite_field_created", "path": path, "reason": reason},
        )
        await self.db.commit()
        await self.db.refresh(meta)
        return await self.extraction_status(file_id=file_id)

    async def get_source_refs(self, file_id: int) -> dict:
        """Return the stored source-ref map for a file.

        Source refs are populated by the pass-3 LLM call in ``run_audit_document_extraction``.
        Returns ``{file_id, source_refs, status}`` where status reflects whether the pass ran.
        """
        file_obj = await self._get_file_or_raise(file_id)
        ocr = file_obj.ocr_metadata
        ocr_json = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
        source_refs = ocr_json.get("source_refs")
        if not isinstance(source_refs, dict):
            source_refs = {}
        status = str(ocr_json.get("source_refs_status") or "not_available")
        return {
            "file_id": file_id,
            "source_refs": source_refs,
            "status": status,
        }

    async def get_entity_afs_source(self, entity_id: int) -> dict:
        """Resolve the audited-financials source for an entity (for source-ref viewing + the
        dashboard file picker).

        ``file_id`` / ``source_refs`` describe the **primary** AFS file (the one driving
        reconciliation, chosen via :func:`resolve_primary_afs_file`), so the source viewer opens
        the same file whose numbers the dashboard shows. ``files`` lists every candidate AFS file
        for the entity (newest first) with the primary flagged, for the "choose file" dropdown.

        Returns ``{entity_id, file_id, source_refs, status, files}``. ``file_id`` is ``None`` when
        the entity has no audited-financials file.
        """
        audit_kinds = {"audit_financials", "financials", "audit-financials"}
        primary = await resolve_primary_afs_file(self.db, entity_id=entity_id)

        all_files = (
            (
                await self.db.execute(
                    select(PortfolioFile)
                    .where(
                        PortfolioFile.entity_id == entity_id,
                        PortfolioFile.status != FileUploadStatus.DELETED,
                    )
                    .order_by(PortfolioFile.updated_at.desc(), PortfolioFile.id.desc())
                )
            )
            .scalars()
            .all()
        )
        candidates: list[dict] = []
        for f in all_files:
            ocr = f.ocr_metadata
            oj = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
            if str(oj.get("kind") or "").strip().lower() not in audit_kinds:
                continue
            candidates.append(
                {
                    "file_id": f.id,
                    "filename": f.filename,
                    "review_cycle_id": f.review_cycle_id,
                    "ocr_status": str(oj.get("status") or ""),
                    "is_primary": bool(primary is not None and f.id == primary.id),
                    "is_reconciliation_source": bool(f.is_reconciliation_source),
                }
            )

        if primary is None:
            return {
                "entity_id": entity_id,
                "file_id": None,
                "source_refs": {},
                "status": "not_available",
                "files": candidates,
            }
        ocr = primary.ocr_metadata
        oj = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
        source_refs = oj.get("source_refs") if isinstance(oj.get("source_refs"), dict) else {}
        return {
            "entity_id": entity_id,
            "file_id": primary.id,
            "source_refs": source_refs,
            "status": str(oj.get("source_refs_status") or "not_available"),
            "files": candidates,
        }

    async def set_entity_primary_afs_file(self, *, entity_id: int, file_id: int) -> dict:
        """Make ``file_id`` the authoritative AFS source for its (entity, cycle), then re-sync so
        reconciliation / breakdowns / query emails reflect the chosen file.

        Clears the flag on all sibling files (same entity + review cycle) first, then sets it on
        the chosen file — two flushes so the partial unique index is never momentarily violated.
        """
        audit_kinds = {"audit_financials", "financials", "audit-financials"}
        f = (
            await self.db.execute(
                select(PortfolioFile).where(
                    PortfolioFile.id == file_id,
                    PortfolioFile.status != FileUploadStatus.DELETED,
                )
            )
        ).scalar_one_or_none()
        if f is None:
            raise FileNotFoundError(f"File {file_id} not found")
        if f.entity_id != entity_id:
            raise ValueError("File is not attached to this entity")
        ocr = f.ocr_metadata
        oj = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
        if str(oj.get("kind") or "").strip().lower() not in audit_kinds:
            raise ValueError("File is not an audited-financials extraction")

        cycle = f.review_cycle_id
        siblings = (
            await self.db.execute(
                select(PortfolioFile).where(
                    PortfolioFile.entity_id == entity_id,
                    PortfolioFile.review_cycle_id == cycle,
                )
            )
        ).scalars().all()
        # Phase 1: clear all (zero primaries) so the partial unique index can't be violated.
        for s in siblings:
            s.is_reconciliation_source = False
        await self.db.flush()
        # Phase 2: set the chosen file.
        f.is_reconciliation_source = True
        await self.db.flush()

        # Push the chosen file's stored breakdown into FinancialMetricReconciliation.
        # Falls back to a full re-sync when the breakdown hasn't been computed yet.
        await push_file_breakdown_to_reconciliation(self.db, file_row=f)
        await self.db.commit()
        return await self.get_entity_afs_source(entity_id)

    async def _get_file_or_raise(self, file_id: int) -> PortfolioFile:
        obj = (
            await self.db.execute(
                select(PortfolioFile).where(
                    and_(
                        PortfolioFile.id == file_id,
                        PortfolioFile.status != FileUploadStatus.DELETED,
                    )
                )
            )
        ).scalar_one_or_none()
        if not obj:
            raise FileNotFoundError(f"File {file_id} not found")
        return obj


async def schedule_extraction_with_background(
    *,
    audit_service: AuditService,
    background_tasks: BackgroundTasks,
    file_id: int,
    kind: str,
) -> dict:
    """Orchestration entry: persist queued/running state, then run :func:`run_extraction_background` after the response.

    Keeps ``queue_extraction`` and ``BackgroundTasks.add_task`` in one place so routes
    cannot enqueue state without starting work (or the reverse).
    """
    out = await audit_service.queue_extraction(file_id=file_id, kind=kind)
    background_tasks.add_task(run_extraction_background, file_id, kind)
    return out


# Limits concurrent extractions to 2 so at least 2 Gunicorn workers remain
# available for API traffic and liveness probes. Single-pod assumption (replicas=1).
_extraction_semaphore = asyncio.Semaphore(2)


async def run_extraction_background(file_id: int, kind: str) -> None:
    """Entry point for FastAPI ``BackgroundTasks`` — own DB session per job."""
    async with _extraction_semaphore:
        try:
            async with AsyncSessionLocal() as db:
                svc = AuditService(db)
                await svc.run_extraction_work(file_id=file_id, kind=kind)
        except Exception:
            logger.exception("background extraction crashed file_id=%s kind=%s", file_id, kind)
