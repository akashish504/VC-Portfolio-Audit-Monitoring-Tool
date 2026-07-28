"""
Backfill: re-locate audit source-refs deterministically (no LLM).

For every audited-financials file that already has stored ``source_refs`` + an ``extracted`` tree,
re-run the deterministic locator (pdfplumber) over the file's PDF and overwrite ``source_refs`` with
verified pages + bounding boxes (or ``unverified``). This upgrades files extracted before the
locator existed and removes the old inaccurate "inherited" pages. Cheap (no LLM), idempotent,
safe to re-run.

Usage:
    python -m src.scripts.data_manipulation.backfill_source_refs            # all files
    python -m src.scripts.data_manipulation.backfill_source_refs --file-id 26
    python -m src.scripts.data_manipulation.backfill_source_refs --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import logging
from typing import Optional

from sqlalchemy import select

from src.db.models import File, FileOCRMetadata
from src.db.session import SyncSessionLocal
from src.services.audit_document_extraction import _verify_and_annotate_source_refs
from src.utils.s3 import download_storage_uri

logger = logging.getLogger(__name__)


def _is_pdf(f: File) -> bool:
    ct = (f.content_type or "").lower()
    return "pdf" in ct or (f.filename or "").lower().endswith(".pdf")


def backfill_source_refs(
    *, limit: Optional[int] = None, file_id: Optional[int] = None, dry_run: bool = False
) -> dict:
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "errors": 0, "verified_refs": 0, "unverified_refs": 0}
    with SyncSessionLocal() as session:
        q = select(FileOCRMetadata, File).join(File, File.id == FileOCRMetadata.file_id)
        if file_id is not None:
            q = q.where(File.id == file_id)
        q = q.order_by(FileOCRMetadata.file_id.desc())
        if limit:
            q = q.limit(limit)

        for ocr, f in session.execute(q).all():
            stats["scanned"] += 1
            oj = ocr.ocr_json if isinstance(ocr.ocr_json, dict) else {}
            refs = oj.get("source_refs")
            extracted = oj.get("extracted")
            if not isinstance(refs, dict) or not refs or not isinstance(extracted, dict):
                stats["skipped"] += 1
                continue
            if not _is_pdf(f) or not f.storage_uri:
                stats["skipped"] += 1
                continue

            try:
                data = download_storage_uri(f.storage_uri)
            except Exception as exc:
                logger.warning("backfill: download failed file_id=%s (%s)", f.id, str(exc)[:200])
                stats["errors"] += 1
                continue
            try:
                new_refs = _verify_and_annotate_source_refs(file_bytes=data, refs=refs, extracted=extracted)
            except Exception as exc:
                logger.warning("backfill: locate failed file_id=%s (%s)", f.id, str(exc)[:200])
                stats["errors"] += 1
                continue

            v = sum(1 for r in new_refs.values() if isinstance(r, dict) and r.get("source") == "verified")
            u = sum(1 for r in new_refs.values() if isinstance(r, dict) and r.get("source") == "unverified")
            stats["verified_refs"] += v
            stats["unverified_refs"] += u
            logger.info("backfill file_id=%s: %d verified / %d unverified (was %d refs)", f.id, v, u, len(refs))

            if not dry_run:
                oj = dict(oj)
                oj["source_refs"] = new_refs
                oj["source_refs_status"] = "completed" if new_refs else "empty"
                ocr.ocr_json = oj  # reassign so SQLAlchemy marks the JSON column dirty
                session.add(ocr)
            stats["updated"] += 1

        if not dry_run:
            session.commit()

    logger.info("backfill done: %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Backfill deterministic audit source-refs (pages + bboxes).")
    ap.add_argument("--limit", type=int, default=None, help="Process at most N files (newest first).")
    ap.add_argument("--file-id", type=int, default=None, help="Process only this file id.")
    ap.add_argument("--dry-run", action="store_true", help="Compute + log, do not write.")
    args = ap.parse_args()
    backfill_source_refs(limit=args.limit, file_id=args.file_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
