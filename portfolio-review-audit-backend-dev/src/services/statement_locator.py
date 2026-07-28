"""
Locate the primary financial-statement pages (P&L / Balance Sheet / Cash Flow / SOCE) inside
an audit-report PDF, so the image+text extraction pipeline renders only those pages instead of
the whole document.

Deterministic, no LLM: each page's text layer is scanned for statement header phrases (plural
aware) and gated by numeric density (filters table-of-contents and notes mentions). A density
fallback catches balance sheets titled only "...Financial Position" whose header words wrap
across lines.

Validated against a real corpus: on digital reports it located all three primary statements
(after the plural fix); it returns nothing for scanned PDFs (no text layer) — the caller detects
that via :func:`document_text.detect_pdf_doc_type` and routes those to the Textract path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

# Header phrases per statement type. ``statements?`` / ``sheets?`` etc. tolerate singular+plural.
_STATEMENT_PATTERNS: Dict[str, List[str]] = {
    "profit_loss": [
        r"statements? of profit",
        r"profit and loss",
        r"profit or loss",
        r"income statements?",
        r"statements? of comprehensive income",
        r"statements? of operations",
        r"statements? of income",
        r"statements? of earnings",
        r"statements? of financial performance",
    ],
    "balance_sheet": [
        r"(consolidated )?balance sheets?",
        r"statements? of financial position",
        r"statements? of financial condition",
    ],
    "cash_flow": [
        r"cash flows? statements?",
        r"statements? of cash\s*flows?",
    ],
    "changes_equity": [
        r"statements? of changes in equity",
        r"changes in equity",
    ],
}
# The three statements every complete audit report should contain.
PRIMARY_STATEMENTS = ("profit_loss", "balance_sheet", "cash_flow")

_NUM_RE = re.compile(r"\d[\d,]{2,}")
_DENSITY_MIN = 15  # a real statement page is number-dense; filters TOC / prose mentions
_BS_DENSITY_FALLBACK = (
    50  # balance sheet titled only "...Financial Position" (header wraps lines)
)
_TEXT_MIN_CHARS = 40  # below this a page has no usable text layer
# A primary statement carries its name as a page TITLE (top of page); notes only mention the
# name inline, deep in the body. Matching the header only within the title region cleanly
# separates the primary statements from the dozens of notes pages that restate the figures.
_TITLE_REGION_CHARS = 500
# A statement may span several consecutive pages (e.g. Assets / Liabilities / Equity, or a
# multi-page cash flow). Walk forward from the primary page collecting continuation pages, but
# cap the run so a pathological layout can never swallow the whole document.
_MAX_STATEMENT_PAGES = 3
# Strong "we've left the statements block" signal: the notes section header, or a leading
# numbered note ("1. General information"). Stops continuation before notes pages that restate
# the same figures (which would otherwise be pulled in as dense, untitled continuation pages).
_NOTES_RE = re.compile(
    r"notes? to the (consolidated )?financial statements|^\s*1[\.\)]\s+\w"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


@dataclass
class StatementLocation:
    """Result of locating statement pages. Page indices are 0-based."""

    by_statement: Dict[str, List[int]] = field(default_factory=dict)
    pages_to_render: List[int] = field(default_factory=list)
    missing_primary: List[str] = field(default_factory=list)
    has_text_layer: bool = True

    @property
    def found_all_primary(self) -> bool:
        return not self.missing_primary


def locate_statement_pages(data: bytes) -> StatementLocation:
    """
    Return a :class:`StatementLocation` for the PDF ``data`` (digital path).

    For digital PDFs this yields the statement pages to render. For scanned PDFs (no text
    layer) it returns ``has_text_layer=False`` with everything missing — the signal for the
    caller to fall back to the Textract/scanned path (see :func:`locate_from_page_texts`).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf not installed; statement locator unavailable")
        return StatementLocation(
            missing_primary=list(PRIMARY_STATEMENTS), has_text_layer=False
        )
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning("locate_statement_pages: could not open PDF (%s)", exc)
        return StatementLocation(
            missing_primary=list(PRIMARY_STATEMENTS), has_text_layer=False
        )
    try:
        # Sort text blocks by visual (y) position before joining so that the title region
        # reflects what the reader sees at the top of the page, not internal storage order.
        # Some PDF generators (e.g. Deloitte reports) store the header block last even though
        # it renders at the top, causing get_text() to return numbers before the title.
        def _visual_text(pg) -> str:
            try:
                blocks = sorted(pg.get_text("blocks"), key=lambda b: b[1])
                return "".join(b[4] for b in blocks)
            except Exception:
                return pg.get_text() or ""

        page_texts = [_visual_text(pg) for pg in doc]
    finally:
        doc.close()
    return locate_from_page_texts(page_texts)


def locate_from_page_texts(page_texts: List[str]) -> StatementLocation:
    """
    Core locator over a list of per-page text strings (0-based order). Used directly by the
    scanned path with Textract OCR text, and by :func:`locate_statement_pages` after PyMuPDF
    text extraction.
    """
    try:
        pages = []
        for raw in page_texts:
            full = _norm(raw)
            head = full[:_TITLE_REGION_CHARS]
            titles = {
                stmt
                for stmt, pats in _STATEMENT_PATTERNS.items()
                if any(re.search(pat, head) for pat in pats)
            }
            pages.append(
                {
                    "full": full,  # whole page (recall fallback)
                    "head": head,  # title region (precise classification)
                    "titles": titles,  # statement types whose header is in this page's title
                    "chars": len(raw.strip()),
                    "nums": len(_NUM_RE.findall(raw)),
                }
            )
        n = len(pages)
        has_text = any(p["chars"] >= _TEXT_MIN_CHARS for p in pages)

        def _continuation(primary: int, stmt: str) -> List[int]:
            # Walk backward first: some PDFs place data pages before the header page (the header
            # sits at the bottom of the physical layout and PyMuPDF reads it after the data).
            # Guards: number-dense, no OTHER statement's title in this page, not a notes page.
            preceding: List[int] = []
            prv = primary - 1
            while (
                prv >= 0
                and len(preceding) < _MAX_STATEMENT_PAGES - 1
                and pages[prv]["nums"] >= _DENSITY_MIN
                and pages[prv]["titles"] <= {stmt}
                and not _NOTES_RE.search(pages[prv]["head"])
            ):
                preceding.insert(0, prv)
                prv -= 1
            out = preceding + [primary]
            # Walk forward for continuation pages (multi-page statements, e.g. Assets then
            # Liabilities on the next page). Same guards; total cap shared with backward pages.
            nxt = primary + 1
            while (
                nxt < n
                and len(out) < _MAX_STATEMENT_PAGES
                and pages[nxt]["nums"] >= _DENSITY_MIN
                and pages[nxt]["titles"] <= {stmt}
                and not _NOTES_RE.search(pages[nxt]["head"])
            ):
                out.append(nxt)
                nxt += 1
            return out

        by_statement: Dict[str, List[int]] = {}
        for stmt, patterns in _STATEMENT_PATTERNS.items():
            # 1) precise: first page whose TITLE region names this statement and is number-dense.
            primary = next(
                (
                    i
                    for i, p in enumerate(pages)
                    if stmt in p["titles"] and p["nums"] >= _DENSITY_MIN
                ),
                None,
            )
            # 2) balance-sheet title wrapped across lines.
            if primary is None and stmt == "balance_sheet":
                primary = next(
                    (
                        i
                        for i, p in enumerate(pages)
                        if "financial position" in p["head"]
                        and p["nums"] >= _BS_DENSITY_FALLBACK
                    ),
                    None,
                )
            # 3) recall fallback: first dense page mentioning the header ANYWHERE (handles layouts
            #    where the title sits below the title region). Notes come later, so first wins.
            #    Skip pages 0-1 (cover/contents) — a real statement is never there, but those
            #    pages can carry statement names + a few summary figures (false positives).
            if primary is None:
                primary = next(
                    (
                        i
                        for i, p in enumerate(pages)
                        if i >= 2
                        and any(re.search(pat, p["full"]) for pat in patterns)
                        and p["nums"] >= _DENSITY_MIN
                    ),
                    None,
                )
            if primary is not None:
                by_statement[stmt] = _continuation(primary, stmt)

        missing = [s for s in PRIMARY_STATEMENTS if s not in by_statement]
        pages_to_render = sorted({i for idxs in by_statement.values() for i in idxs})

        logger.info(
            "statement locator: text_layer=%s found=%s missing_primary=%s pages=%s",
            has_text,
            {k: v for k, v in by_statement.items()},
            missing,
            pages_to_render,
        )
        return StatementLocation(
            by_statement=by_statement,
            pages_to_render=pages_to_render,
            missing_primary=missing,
            has_text_layer=has_text,
        )
    except Exception as exc:
        logger.warning("locate_from_page_texts failed (%s)", exc)
        return StatementLocation(
            missing_primary=list(PRIMARY_STATEMENTS), has_text_layer=False
        )
