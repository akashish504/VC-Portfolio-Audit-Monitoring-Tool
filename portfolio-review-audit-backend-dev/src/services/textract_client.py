"""
AWS Textract helpers for the SCANNED-document extraction path.

A scanned PDF has no text layer, so neither the statement locator nor the digit anchor work.
Textract recovers both:

  * :func:`ocr_text_by_page` — async ``StartDocumentTextDetection`` over the S3 PDF → per-page
    OCR text, used to *locate* the statement pages (reusing the keyword locator on OCR text).
  * :func:`tables_for_image` — sync ``AnalyzeDocument(TABLES)`` on a rendered statement page →
    structured cells (row × column), the exact-digit source the LLM maps from.

Textract is not available in every region (absent in eu-north-1), so we call it in
``settings.TEXTRACT_REGION`` (default us-east-1). All functions degrade gracefully — on any
error they return empty so the caller can fall back (e.g. vision-only) without crashing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import boto3

from src.configs.env import settings

logger = logging.getLogger(__name__)

_TEXTRACT_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # sync AnalyzeDocument hard limit
_OCR_POLL_INTERVAL_SEC = 3
_OCR_MAX_WAIT_SEC = 180


def _client():
    """Textract client in the configured region (Bedrock token is irrelevant to Textract)."""
    return boto3.client("textract", region_name=settings.TEXTRACT_REGION)


@dataclass
class TextractTable:
    """A detected table as a dense grid of cell strings (row-major, 1-based collapsed to lists)."""

    rows: List[List[str]] = field(default_factory=list)

    def to_lines(self) -> str:
        """Pipe-delimited text the LLM can read as the exact-digit source."""
        return "\n".join(" | ".join(c for c in row) for row in self.rows)


def tables_for_image(png_bytes: bytes) -> List[TextractTable]:
    """
    Run ``AnalyzeDocument(TABLES)`` on a single rendered page image and return its tables as
    cell grids. Returns ``[]`` on any failure (caller falls back to vision-only digits).
    """
    if not png_bytes or len(png_bytes) > _TEXTRACT_MAX_IMAGE_BYTES:
        logger.warning(
            "textract tables_for_image: image missing or too large (%s bytes)",
            len(png_bytes or b""),
        )
        return []
    try:
        resp = _client().analyze_document(
            Document={"Bytes": png_bytes}, FeatureTypes=["TABLES"]
        )
    except Exception as exc:
        logger.warning("textract analyze_document failed: %s", str(exc)[:200])
        return []

    blocks = {b["Id"]: b for b in resp.get("Blocks", [])}

    def _cell_text(cell: dict) -> str:
        words: List[str] = []
        for rel in cell.get("Relationships", []) or []:
            if rel.get("Type") == "CHILD":
                for cid in rel.get("Ids", []):
                    w = blocks.get(cid) or {}
                    if w.get("BlockType") in ("WORD", "SELECTION_ELEMENT"):
                        words.append(w.get("Text", ""))
        return " ".join(t for t in words if t)

    tables: List[TextractTable] = []
    for b in resp.get("Blocks", []):
        if b.get("BlockType") != "TABLE":
            continue
        cells: Dict[int, Dict[int, str]] = {}
        for rel in b.get("Relationships", []) or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids", []):
                cell = blocks.get(cid) or {}
                if cell.get("BlockType") != "CELL":
                    continue
                cells.setdefault(cell["RowIndex"], {})[cell["ColumnIndex"]] = (
                    _cell_text(cell)
                )
        rows = [[cells[r][c] for c in sorted(cells[r])] for r in sorted(cells)]
        if rows:
            tables.append(TextractTable(rows=rows))
    logger.info("textract tables_for_image: %d table(s)", len(tables))
    return tables


def detect_text_for_image(png_bytes: bytes) -> str:
    """
    Sync OCR of a single rendered page image via ``DetectDocumentText`` (bytes — region
    independent, unlike the async S3 path which requires Textract and the bucket to share a
    region). Returns the page's text (LINE blocks joined) or ``""`` on failure.
    """
    if not png_bytes or len(png_bytes) > _TEXTRACT_MAX_IMAGE_BYTES:
        return ""
    try:
        resp = _client().detect_document_text(Document={"Bytes": png_bytes})
    except Exception as exc:
        logger.warning("textract detect_document_text failed: %s", str(exc)[:200])
        return ""
    return "\n".join(
        b.get("Text", "")
        for b in resp.get("Blocks", [])
        if b.get("BlockType") == "LINE"
    )


def ocr_text_by_page(s3_bucket: str, s3_key: str) -> Dict[int, str]:
    """
    OCR a multi-page PDF already in S3 via async ``StartDocumentTextDetection`` and return
    ``{page_number(1-based): text}``. Used to locate statements in scanned PDFs. Returns ``{}``
    on any failure or timeout.
    """
    tx = _client()
    try:
        start = tx.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
        )
        job_id = start["JobId"]
    except Exception as exc:
        logger.warning(
            "textract start_document_text_detection failed: %s", str(exc)[:200]
        )
        return {}

    waited = 0
    status = "IN_PROGRESS"
    while status == "IN_PROGRESS" and waited < _OCR_MAX_WAIT_SEC:
        time.sleep(_OCR_POLL_INTERVAL_SEC)
        waited += _OCR_POLL_INTERVAL_SEC
        try:
            resp = tx.get_document_text_detection(JobId=job_id)
            status = resp.get("JobStatus", "FAILED")
        except Exception as exc:
            logger.warning(
                "textract get_document_text_detection failed: %s", str(exc)[:200]
            )
            return {}
    if status != "SUCCEEDED":
        logger.warning(
            "textract OCR job %s ended status=%s after %ss", job_id, status, waited
        )
        return {}

    pages: Dict[int, List[str]] = {}
    next_token: Optional[str] = None
    while True:
        if next_token:
            resp = tx.get_document_text_detection(JobId=job_id, NextToken=next_token)
        for b in resp.get("Blocks", []):
            if b.get("BlockType") == "LINE":
                pages.setdefault(b.get("Page", 1), []).append(b.get("Text", ""))
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return {pno: "\n".join(lines) for pno, lines in pages.items()}
