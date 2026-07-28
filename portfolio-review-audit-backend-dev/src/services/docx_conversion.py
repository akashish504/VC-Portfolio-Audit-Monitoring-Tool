"""
Convert office/image uploads to PDF via LibreOffice (headless), for downstream PDF/Claude extraction.

Supports: .docx, .doc, .pptx, .ppt, .png, .jpg, .jpeg (and anything else LibreOffice can open).
Requires ``soffice`` on PATH or ``LIBREOFFICE_PATH`` pointing to the binary (e.g. Debian: ``/usr/bin/soffice``).
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from src.configs.env import settings

logger = logging.getLogger(__name__)

_DOCX_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",  # legacy .doc — try conversion; LO may handle
)

# Extensions that must be converted to PDF before LLM extraction.
# PDF and Excel are sent natively; everything else goes through LibreOffice.
EXTENSIONS_REQUIRING_CONVERSION: frozenset[str] = frozenset({
    ".docx", ".doc",
    ".pptx", ".ppt",
    ".png", ".jpg", ".jpeg",
})


def is_docx_filename(filename: str) -> bool:
    n = (filename or "").strip().lower()
    return n.endswith(".docx") or n.endswith(".doc")


def looks_like_docx(filename: str, content_type: Optional[str]) -> bool:
    if is_docx_filename(filename):
        return True
    ct = (content_type or "").lower().split(";")[0].strip()
    return ct in _DOCX_CT


def needs_pdf_conversion(filename: str) -> bool:
    """Return True if this file must be converted to PDF before LLM extraction."""
    ext = Path((filename or "").strip()).suffix.lower()
    return ext in EXTENSIONS_REQUIRING_CONVERSION


def _safe_key_segment(name: str) -> str:
    """Match :func:`audit_service._s3_key` sanitization for the filename segment."""
    return (name or "").replace(" ", "_")


def key_with_replaced_filename(s3_key: str, new_filename: str) -> str:
    """Same directory as ``s3_key``, last path segment replaced by ``new_filename`` (sanitized)."""
    safe = _safe_key_segment(Path(new_filename).name)
    prefix = s3_key.rsplit("/", 1)[0] if "/" in s3_key else ""
    return f"{prefix}/{safe}" if prefix else safe


def find_soffice() -> str:
    override = (settings.LIBREOFFICE_PATH or os.environ.get("LIBREOFFICE_PATH", "")).strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    for candidate in (
        shutil.which("soffice") or "",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/opt/libreoffice/program/soffice",
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        "LibreOffice (soffice) not found. Install libreoffice-writer or set LIBREOFFICE_PATH to the soffice binary."
    )


def convert_bytes_to_pdf(
    file_bytes: bytes,
    original_filename: str,
    *,
    timeout_sec: Optional[int] = None,
) -> tuple[bytes, str]:
    """
    Write bytes to a temp file, run headless LibreOffice --convert-to pdf, return (pdf_bytes, output_basename).

    Works for any format LibreOffice can open: .docx, .doc, .pptx, .ppt, .png, .jpg, .jpeg, etc.
    ``output_basename`` is ``<stem>.pdf`` (spaces preserved in basename).
    """
    max_bytes = int(settings.MAX_DOCX_UPLOAD_BYTES)
    if len(file_bytes) > max_bytes:
        raise ValueError(f"File exceeds maximum allowed size ({max_bytes // (1024 * 1024)} MiB).")

    timeout = timeout_sec if timeout_sec is not None else int(settings.DOCX_TO_PDF_TIMEOUT_SEC)
    base = Path(original_filename or "document").name
    # Ensure the temp file has the correct extension so LibreOffice picks the right import filter.
    if not Path(base).suffix:
        base = f"{base}.pdf"

    soffice = find_soffice()
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / base
        in_path.write_bytes(file_bytes)
        out_dir = Path(tmp) / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(in_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"LibreOffice conversion timed out after {timeout}s") from e
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or str(e))[:2000]
            logger.warning("LibreOffice failed: %s", err)
            raise RuntimeError(f"LibreOffice conversion failed: {err[:500]}") from e
        if proc.stderr and "error" in proc.stderr.lower():
            logger.debug("LibreOffice stderr: %s", proc.stderr[:500])

        # Default output: same stem as input + .pdf
        pdf_name = in_path.stem + ".pdf"
        pdf_path = out_dir / pdf_name
        if not pdf_path.is_file():
            pdfs = list(out_dir.glob("*.pdf"))
            if len(pdfs) == 1:
                pdf_path = pdfs[0]
                pdf_name = pdf_path.name
            else:
                raise RuntimeError(
                    f"LibreOffice did not produce expected PDF (looked for {pdf_name!r} in {out_dir})"
                )

        return pdf_path.read_bytes(), pdf_name


def convert_docx_bytes_to_pdf(
    docx_bytes: bytes,
    original_filename: str,
    *,
    timeout_sec: Optional[int] = None,
) -> tuple[bytes, str]:
    """Thin wrapper around :func:`convert_bytes_to_pdf` kept for backwards compatibility."""
    return convert_bytes_to_pdf(docx_bytes, original_filename, timeout_sec=timeout_sec)


def maybe_convert_to_pdf(
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str],
) -> tuple[bytes, str, str]:
    """
    Convert to PDF via LibreOffice if the file format requires it, otherwise return unchanged.

    Formats that trigger conversion: .docx, .doc, .pptx, .ppt, .png, .jpg, .jpeg.
    PDF and Excel (.xlsx/.xls) are returned unchanged — those are sent natively to the LLM.
    """
    if not needs_pdf_conversion(filename):
        guessed = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return file_bytes, filename, guessed

    pdf_bytes, pdf_name = convert_bytes_to_pdf(file_bytes, filename)
    logger.info("Converted %r to PDF: %r (%s bytes)", filename, pdf_name, len(pdf_bytes))
    return pdf_bytes, pdf_name, "application/pdf"
