"""Extract plain text from uploaded file bytes (PDF via pypdf, XLSX via openpyxl, else UTF-8)."""

from __future__ import annotations

import io
import logging
from typing import List

logger = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 90_000


def _strip_surrogates(text: str) -> str:
    """Replace lone UTF-16 surrogate code points (U+D800–U+DFFF) with U+FFFD.

    pypdf and some other parsers emit lone surrogates when decoding certain
    PDF encodings.  These are invalid in UTF-8 and rejected by the Claude /
    Bedrock APIs with a 400 "surrogates not allowed" error.  Sanitising at the
    extraction boundary prevents the tainted string from reaching any JSON
    serialisation or HTTP call site.
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")

# Maximum rows emitted per sheet — caps very large workbooks to protect LLM context.
_XLSX_MAX_ROWS_PER_SHEET = 2_000


def is_xlsx_filename(filename: str) -> bool:
    """Return True for .xlsx / .xls files."""
    n = (filename or "").lower().strip()
    return n.endswith(".xlsx") or n.endswith(".xls")


def extract_xlsx_text(filename: str, data: bytes) -> str:
    """
    Convert an Excel workbook to LLM-consumable text using openpyxl.

    Format per sheet:
        ## Sheet: <name>
        Row 1: col_a_val\tcol_b_val\t...
        Row 2: ...

    - ``data_only=True`` resolves formulas to their last-computed value.
    - Skips rows where every cell is blank.
    - Row numbers are 1-based (matching Excel's own numbering) so the LLM
      can reference them as source locations.
    - Truncated at ``_XLSX_MAX_ROWS_PER_SHEET`` rows per sheet.
    """
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("openpyxl not installed; falling back to raw bytes decode for %s", filename)
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:
        logger.warning("openpyxl failed to open %s: %s", filename, exc)
        return ""

    parts: List[str] = []
    for sheet_name in wb.sheetnames:
        try:
            ws = wb[sheet_name]
        except Exception:
            continue
        parts.append(f"## Sheet: {sheet_name}")
        rows_emitted = 0
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            # Skip rows where every cell is None or empty string.
            if not any(c is not None and str(c).strip() for c in row):
                continue
            cells = "\t".join("" if c is None else _strip_surrogates(str(c)) for c in row)
            parts.append(f"Row {row_idx}: {cells}")
            rows_emitted += 1
            if rows_emitted >= _XLSX_MAX_ROWS_PER_SHEET:
                parts.append(f"[Sheet '{sheet_name}' truncated at {_XLSX_MAX_ROWS_PER_SHEET} rows]")
                break
        parts.append("")  # blank line between sheets

    wb.close()
    return "\n".join(parts)


def extract_text_from_file_bytes(filename: str, data: bytes) -> str:
    """Return normalized text for LLM consumption."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore[import-untyped]

            reader = PdfReader(io.BytesIO(data))
            parts: List[str] = []
            for page in reader.pages:
                parts.append(_strip_surrogates(page.extract_text() or ""))
            return "\n".join(parts)
        except Exception as e:
            logger.warning("PDF text extract failed: %s", e)
            return ""
    if is_xlsx_filename(name):
        return extract_xlsx_text(name, data)
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


# --- PDF rasterization + doc-type detection (image+text financial-statement pipeline) ---

# A page yielding fewer extractable chars than this has no usable text layer (scanned/blank).
_SCANNED_TEXT_MIN_CHARS = 40
_RENDER_DEFAULT_TARGET_PX = 2000  # long-edge target; callers usually pass the settings value
_RENDER_MIN_ZOOM = 1.0  # never below native 72 dpi
_RENDER_MAX_ZOOM = 4.0  # cap (~288 dpi) so oversized pages don't explode payloads


def detect_pdf_doc_type(data: bytes) -> str:
    """
    Classify a PDF as ``"digital"`` (has an extractable text layer), ``"scanned"`` (image-only),
    or ``"hybrid"`` (mix of digital and scanned pages — e.g. audit report text is digital but
    financial statement pages are embedded scanned images).

    Heuristic:
    - ``"scanned"``  — majority (>50%) of pages have no text layer.
    - ``"hybrid"``   — minority of pages have no text layer but at least one image-only page
                       exists. These documents need Textract to recover the scanned pages.
    - ``"digital"``  — all (or nearly all) pages have a text layer.

    Defaults to ``"digital"`` on any error — the safer branch (never spuriously forces the
    costlier scanned path).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf not installed; assuming digital PDF")
        return "digital"
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning("detect_pdf_doc_type: could not open PDF (%s); assuming digital", exc)
        return "digital"
    try:
        n = doc.page_count
        if n == 0:
            return "digital"
        no_text = sum(
            1 for pg in doc
            if len(_strip_surrogates(pg.get_text() or "").strip()) < _SCANNED_TEXT_MIN_CHARS
        )
        if no_text > n * 0.5:
            return "scanned"
        if no_text > 0:
            return "hybrid"
        return "digital"
    finally:
        doc.close()


def pdf_page_count(data: bytes) -> int:
    """Number of pages in the PDF (0 on error)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return 0
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return 0
    try:
        return doc.page_count
    finally:
        doc.close()


def render_pdf_pages_to_png(
    data: bytes,
    page_indices: List[int],
    target_long_edge_px: int = _RENDER_DEFAULT_TARGET_PX,
) -> List[bytes]:
    """
    Render the given **0-based** ``page_indices`` to PNG bytes, **page-size agnostic**.

    Resolution is derived from each page's own ``rect`` (A4/Letter/A3/landscape all read from the
    file — nothing hardcoded): ``zoom = target_long_edge_px / max(page dims)``, clamped to
    ``[_RENDER_MIN_ZOOM, _RENDER_MAX_ZOOM]``. Page ``/Rotate`` is honored by ``get_pixmap``. Returns
    one PNG per valid index (out-of-range indices skipped with a warning), in ``page_indices`` order.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("pymupdf is required to render PDF pages") from exc
    doc = fitz.open(stream=data, filetype="pdf")
    out: List[bytes] = []
    try:
        for idx in page_indices:
            if idx < 0 or idx >= doc.page_count:
                logger.warning(
                    "render_pdf_pages_to_png: page index %s out of range (0..%s)",
                    idx,
                    doc.page_count - 1,
                )
                continue
            page = doc[idx]
            rect = page.rect
            long_edge = max(rect.width, rect.height) or 1.0
            zoom = max(_RENDER_MIN_ZOOM, min(_RENDER_MAX_ZOOM, target_long_edge_px / long_edge))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            out.append(pix.tobytes("png"))
    finally:
        doc.close()
    return out


def extract_pdf_pages_text(data: bytes, page_indices: List[int]) -> "dict[int, str]":
    """
    Return ``{page_index: text}`` for the given 0-based pages via PyMuPDF's fast text extraction —
    the exact-digit anchor for the hybrid (digital) extraction path. Empty string for pages with
    no text layer.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf not installed; cannot extract per-page text")
        return {}
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning("extract_pdf_pages_text: could not open PDF (%s)", exc)
        return {}
    out: "dict[int, str]" = {}
    try:
        for idx in page_indices:
            if 0 <= idx < doc.page_count:
                out[idx] = _strip_surrogates(doc[idx].get_text() or "")
    finally:
        doc.close()
    return out
