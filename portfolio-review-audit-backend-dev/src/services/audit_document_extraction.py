"""
Audit portfolio files (non–org-chart): LLM extraction to structured JSON.

- **LOCAL_DEV:** OpenAI — ``completion_with_document`` (raw pass) + pass-2 mapping.
- **Deployment (Bedrock):** PDF bytes downloaded from stored ``s3://`` then passed inline to ``converse`` (no batch; no minimum record count); mapping via ``chat_completion`` when ``text_only_llm``.
- **audit_financials:** pass 2 returns ``mapped`` + ``audit_financials_unmatched`` for human-in-the-loop review.
- **XLSX files:** Pass 1 sends the workbook to Bedrock as a native ``xlsx``/``xls`` document block (Claude reads
  the file directly). On failure, falls back to openpyxl sheet/row text. Pass 3 (source refs) and the
  qualitative pass are skipped because they require a paginated PDF.

Does not create entities (org-chart only).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings
from src.db.models import File, FileOCRMetadata, FileUploadStatus
from src.llm.client import chat_completion, completion_with_document, completion_with_images
from src.llm.config import effective_document_extraction_provider, validate_llm_config
from src.llm.prompts import (
    AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL,
    AUDIT_FINANCIALS_RESIDUAL_ASSIGNMENT_SYSTEM_PROMPT,
    AUDIT_FINANCIALS_SECTION_TAGGING_SYSTEM_PROMPT,
    AUDIT_FINANCIALS_SYSTEM_PROMPT,
    AUDIT_QUALITATIVE_SYSTEM_PROMPT,
    SOURCE_REFS_SYSTEM_PROMPT,
    audit_financials_residual_assignment_user_instruction,
    audit_financials_section_tagging_user_instruction,
    audit_financials_schema_mapping_system_prompt,
    audit_system_prompt_for_kind,
    source_refs_user_prompt,
)
from src.services.document_text import (
    MAX_DOCUMENT_CHARS,
    detect_pdf_doc_type,
    extract_pdf_pages_text,
    extract_text_from_file_bytes,
    is_xlsx_filename,
    pdf_page_count,
    render_pdf_pages_to_png,
)
from src.services.extraction_guard import run_extraction_guard
from src.services.statement_locator import locate_from_page_texts, locate_statement_pages
from src.services.textract_client import detect_text_for_image, tables_for_image
from src.services.financial_audit_schema import (
    AUDIT_FINANCIALS_SCHEMA_CONFIG_KEY,
    build_audit_financials_mapping_json_schema,
    dedupe_duplicate_leaves,
    finalize_audit_financials_extracted,
    load_audit_financials_schema,
    set_value_at_dotted_path,
)
from src.services.financial_label_matching import enrich_unmatched_rows, prepare_label_matcher
from src.services.financial_section_tagging import (
    apply_llm_section_assignments,
    assign_placement_paths,
    llm_section_candidates,
)
from src.services.financial_deterministic_mapping import (
    apply_residual_assignments,
    build_leaf_catalog,
    candidates_for_line,
    is_total_label,
    split_confident_and_residual,
)
from src.services.audit_qualitative_extraction import split_combined_qualitative_response
from src.services.financial_data_extraction_sync import sync_financial_data_from_audit_extraction
from src.services.fx_inr_conversion import auto_convert_extracted_tree, maybe_auto_convert_extracted_tree_to_inr
from src.services.extraction_currency_scale import scale_unmatched_amounts
from src.services.fy_end import normalize_fy_end, fy_end_last_day
from src.utils.s3 import download_storage_uri

logger = logging.getLogger(__name__)


def _audit_kind_slug(kind: str) -> str:
    k = (kind or "audit").strip().lower().replace(" ", "_")
    k = re.sub(r"[^a-z0-9_-]+", "", k)
    return k or "audit"


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _sanitise_llm_json_string(s: str) -> str:
    """
    Replace bare control characters that make JSON invalid.

    LLMs occasionally copy verbatim newlines/tabs from PDF text into string
    values.  json.loads rejects these as unterminated strings.  We replace
    them at the raw character level — before any quote-balancing attempt —
    so the regex doesn't need to find a closing quote that may not exist.
    """
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', s) \
              .replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ') \
              .replace('\t', ' ')


def _extract_first_fenced_json_block(raw: str) -> Optional[str]:
    """If the model wraps JSON in a ```json ... ``` fence (possibly after prose), return the inner text."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", (raw or ""), re.IGNORECASE)
    if not m:
        return None
    inner = (m.group(1) or "").strip()
    return inner or None


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    candidates: list[str] = []
    fence = _extract_first_fenced_json_block(text)
    if fence:
        candidates.append(fence)
    candidates.append(_strip_json_fence(text))
    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            deduped.append(c)
    last_err: Optional[Exception] = None
    for cand in deduped:
        for attempt in (cand, _sanitise_llm_json_string(cand)):
            if not attempt.strip():
                continue
            try:
                data = json.loads(attempt)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError as e:
                last_err = e
                continue
    if last_err:
        raise ValueError(str(last_err))
    raise ValueError("LLM output must be a JSON object")


def _split_audit_financials_mapping_result(data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse pass-2 output: ``{ mapped, unmatched }`` or legacy flat canonical object."""
    if isinstance(data.get("mapped"), dict):
        mapped = data["mapped"]
        raw_u = data.get("unmatched")
        unmatched: list[dict[str, Any]] = []
        if isinstance(raw_u, list):
            for i, item in enumerate(raw_u):
                if isinstance(item, dict):
                    row = dict(item)
                    if not row.get("id"):
                        row["id"] = f"unmatched-{i}"
                    unmatched.append(row)
        return mapped, unmatched
    if _audit_financials_has_required_top_level(data):
        return data, []
    return data, []


def _audit_financials_has_required_top_level(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    for k in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        if k not in obj:
            return False
    return True


# Raw (pass-1) section names → the canonical statement they belong to. Used by the coverage
# guard to scope residuals to the three reconciled statements (skips SOCE, notes, metadata —
# they aren't part of the canonical schema and would only be noise in the unmatched panel).
_RAW_SECTION_TO_CANONICAL: dict[str, str] = {
    "profit_and_loss": "profit_and_loss",
    "statement_of_profit_and_loss": "profit_and_loss",
    "statement_of_profit_or_loss": "profit_and_loss",
    "statement_of_comprehensive_income": "profit_and_loss",
    "statement_of_profit_and_loss_and_other_comprehensive_income": "profit_and_loss",
    "income_statement": "profit_and_loss",
    "statement_of_operations": "profit_and_loss",
    "statement_of_earnings": "profit_and_loss",
    "statement_of_financial_performance": "profit_and_loss",
    "statement_of_income": "profit_and_loss",
    "balance_sheet": "balance_sheet",
    "statement_of_financial_position": "balance_sheet",
    "statement_of_financial_condition": "balance_sheet",
    "cash_flow_statement": "cash_flow_statement",
    "statement_of_cash_flows": "cash_flow_statement",
    "cash_flows": "cash_flow_statement",
}


def _audit_financials_coverage_residuals(
    raw_extracted: dict[str, Any],
    mapped_extracted: dict[str, Any],
    existing_unmatched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Numbers present in pass-1 but accounted for nowhere in ``mapped`` + ``unmatched``.

    The strict mapping schema can cause an equivalent-but-differently-worded line to be
    force-fit, buried in an ``other`` bucket, or silently omitted — and nothing currently
    catches the omission. This deterministic backstop matches pass-1 numeric leaves by value
    against everything that landed; any leftover (scoped to the three canonical statements)
    becomes a synthesized unmatched row so it surfaces for review instead of vanishing.

    Pass-1 is already consolidated-scoped (system prompt rule 6), so this does not resurface
    intentionally-dropped entity-only duplicates. Value matching is a multiset so repeated
    figures are consumed one-for-one.
    """
    if not isinstance(raw_extracted, dict) or not isinstance(mapped_extracted, dict):
        return []

    placed: Counter = Counter()
    for v in _flatten_numeric_leaves(mapped_extracted).values():
        placed[round(float(v), 2)] += 1
    for u in existing_unmatched or []:
        if isinstance(u, dict):
            n = _parse_leaf_number(u.get("value"))
            if n is not None:
                placed[round(float(n), 2)] += 1

    residuals: list[dict[str, Any]] = []
    idx = 0
    for path, v in _flatten_numeric_leaves(raw_extracted).items():
        section = _RAW_SECTION_TO_CANONICAL.get(path.split(".")[0].lower())
        if section is None:
            continue  # not one of the three reconciled statements → out of scope
        fv = float(v)
        if fv == 0:
            continue  # a reported zero force-fits anywhere; skip to avoid noise
        if fv.is_integer() and 1900 <= fv <= 2100:
            continue  # almost certainly a year, not an amount
        key = round(fv, 2)
        if placed.get(key, 0) > 0:
            placed[key] -= 1
            continue
        leaf_key = path.split(".")[-1]
        residuals.append(
            {
                "id": f"coverage-{idx}",
                "document_label": leaf_key.replace("_", " "),
                "value": fv,
                "section_hint": section,
                "source": "coverage_guard_missing",
                "raw_path": path,
                # So a missing-but-recovered subtotal isn't summed with its components in the UI.
                "is_total": is_total_label(leaf_key),
            }
        )
        idx += 1
    return residuals


# Currency-like strings we tolerate from the LLM and normalize to ISO-4217.
# Keys are uppercased + punctuation-stripped; values are 3-letter codes.
_CURRENCY_ALIASES: dict[str, str] = {
    "RS": "INR",
    "RS.": "INR",
    "INR": "INR",
    "RUPEE": "INR",
    "RUPEES": "INR",
    "INDIANRUPEE": "INR",
    "INDIANRUPEES": "INR",
    "₹": "INR",
    "USD": "USD",
    "US$": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "USDOLLAR": "USD",
    "USDOLLARS": "USD",
    "SGD": "SGD",
    "S$": "SGD",
    "SINGAPOREDOLLAR": "SGD",
    "HKD": "HKD",
    "HK$": "HKD",
    "GBP": "GBP",
    "£": "GBP",
    "POUND": "GBP",
    "STERLING": "GBP",
    "EUR": "EUR",
    "€": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "CNY": "CNY",
    "RMB": "CNY",
    "JPY": "JPY",
    "YEN": "JPY",
    "AED": "AED",
    "DHS": "AED",
    "DHS.": "AED",
    "DIRHAM": "AED",
    "DIRHAMS": "AED",
    "AUD": "AUD",
    "A$": "AUD",
    "CAD": "CAD",
    "C$": "CAD",
    "CHF": "CHF",
    "NZD": "NZD",
    "SAR": "SAR",
    "MYR": "MYR",
    "THB": "THB",
    "IDR": "IDR",
    "PHP": "PHP",
    "VND": "VND",
    "ZAR": "ZAR",
}


def _normalize_currency_code(v: Any) -> Optional[str]:
    """Best-effort ISO-4217 normalization for LLM output.

    Accepts a 3-letter code directly, or a common alias/symbol (``Rs.``, ``S$``,
    ``Rupees``) and maps it. Returns ``None`` if we cannot confidently resolve
    to a 3-letter uppercase code — callers should treat that as "unknown" rather
    than storing a garbage string that the UI would have to reject later.
    """
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    # Fast path: already a clean 3-letter code.
    if len(s) == 3 and s.isalpha():
        return s.upper()
    key = "".join(ch for ch in s.upper() if not ch.isspace())
    mapped = _CURRENCY_ALIASES.get(key)
    if mapped:
        return mapped
    # Last chance: the model sometimes emits "INR (Indian Rupees)" — keep only
    # the leading 3-letter code if the first three chars form one.
    head = key[:3]
    if len(head) == 3 and head.isalpha() and head in {v for v in _CURRENCY_ALIASES.values()}:
        return head
    return None


def _pick_currency_from_extraction(extracted: Any) -> Optional[str]:
    """Find the LLM-detected currency in a pass-1 extraction payload.

    Order: top-level ``currency`` → ``report_metadata.currency``. Both are
    run through :func:`_normalize_currency_code`.
    """
    if not isinstance(extracted, dict):
        return None
    direct = _normalize_currency_code(extracted.get("currency"))
    if direct:
        return direct
    rm = extracted.get("report_metadata")
    if isinstance(rm, dict):
        nested = _normalize_currency_code(rm.get("currency"))
        if nested:
            return nested
    return None


def _pick_figures_metrics_from_extraction(extracted: Any) -> tuple[Optional[str], Optional[float]]:
    """Read denomination metadata from pass-1 JSON (crore/lakh/thousands, etc.)."""
    if not isinstance(extracted, dict):
        return None, None
    denom_raw = extracted.get("figures_denomination")
    scale_raw = extracted.get("figures_scale_to_smallest_unit")

    denom: Optional[str] = None
    if isinstance(denom_raw, str) and denom_raw.strip():
        denom = denom_raw.strip()

    scale: Optional[float] = None
    if isinstance(scale_raw, (int, float)):
        sf = float(scale_raw)
        if math.isfinite(sf) and sf > 0:
            scale = sf
    elif isinstance(scale_raw, str):
        try:
            sf = float(scale_raw.strip())
            if math.isfinite(sf) and sf > 0:
                scale = sf
        except ValueError:
            pass

    return denom, scale


async def _assign_residual_with_llm(
    *,
    residual_lines: list[dict[str, Any]],
    candidate_index: list[dict[str, Any]],
) -> dict[str, Optional[str]]:
    """Constrained per-line assignment of the deterministic residual (text-only LLM).

    Reframes pass-2 from free-form tree building to "pick one path from a fixed catalog, or
    unmatched". Returns ``{line_id -> target_path_or_None}``; every target is hard-validated against
    band + role by :func:`apply_residual_assignments`, so a wrong band the model returns is bounced
    to unmatched rather than placed. Raises on transport/parse failure so the caller can fall back
    to the legacy LLM mapper.
    """
    lines_payload = [
        {
            "id": ln["id"],
            "label": ln["label"],
            "value": ln["value"],
            "statement": (ln.get("band") or {}).get("statement"),
            "side": (ln.get("band") or {}).get("side"),
            "currentness": (ln.get("band") or {}).get("currentness"),
            "activity": (ln.get("band") or {}).get("activity"),
            "is_total": bool(ln.get("is_total")),
            # Squeeze the universe per line: only the band/role-admissible canonical leaves (often a
            # handful) — so the model picks among "the non-current-asset leaves", not the whole schema.
            "candidates": candidates_for_line(ln, candidate_index),
        }
        for ln in residual_lines
    ]
    # Global catalog shown to the model is trimmed to only the leaves at least one line can reach, so
    # it never sees irrelevant statements/bands. apply_residual_assignments is still the hard backstop.
    reachable = {p for ln in lines_payload for p in ln["candidates"]}
    catalog = [c for c in build_leaf_catalog(candidate_index) if c["path"] in reachable]
    user_instruction = audit_financials_residual_assignment_user_instruction(
        catalog=catalog, lines=lines_payload
    )
    residual_tool = {
        "name": "emit_residual_assignments",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["id", "target"],
                    },
                }
            },
            "required": ["assignments"],
        },
    }
    raw = await chat_completion(
        [
            {"role": "system", "content": AUDIT_FINANCIALS_RESIDUAL_ASSIGNMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_instruction},
        ],
        # Generous budget: reasoning models (gpt-5/o-series) spend completion tokens on hidden
        # reasoning before emitting the tool call, and a statement can have 100+ residual lines.
        max_tokens=16384,
        http_timeout=float(settings.ORG_CHART_LLM_TIMEOUT),
        tool=residual_tool,
    )
    parsed = _parse_llm_json(raw)
    rows = parsed.get("assignments") if isinstance(parsed, dict) else None
    assignments: dict[str, Optional[str]] = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            target = row.get("target")
            assignments[row["id"]] = target if isinstance(target, str) else None
    return assignments


async def _assign_sections_with_llm(
    *, rows: list[dict[str, Any]], schema: dict[str, Any]
) -> dict[str, Optional[str]]:
    """Small-universe second-level SECTION pick for rows the deterministic tagger couldn't place.

    Each row is offered only its statement's handful of sections (its ``candidates``); the model
    routes the line to one or to ``"none"``. Returns ``{row_id -> section_path_or_None}``;
    :func:`financial_section_tagging.apply_llm_section_assignments` re-validates each target against
    the schema before it is applied, so a bad pick can only leave the row un-tagged (Tier 3), never
    place it anywhere invalid. Raises on transport/parse failure so the caller can degrade.
    """
    lines_payload: list[dict[str, Any]] = []
    for r in rows:
        band = r.get("band") if isinstance(r.get("band"), dict) else {}
        stmt = band.get("statement") or (
            r.get("section_hint") if r.get("section_hint") in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL else None
        )
        cands = llm_section_candidates(schema, stmt)
        if not cands:
            continue
        lines_payload.append(
            {
                "id": r.get("id"),
                "label": r.get("document_label") or r.get("key") or "",
                "value": r.get("value"),
                "statement": stmt,
                "candidates": cands,
            }
        )
    if not lines_payload:
        return {}

    section_tool = {
        "name": "emit_section_assignments",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["id", "target"],
                    },
                }
            },
            "required": ["assignments"],
        },
    }
    raw = await chat_completion(
        [
            {"role": "system", "content": AUDIT_FINANCIALS_SECTION_TAGGING_SYSTEM_PROMPT},
            {"role": "user", "content": audit_financials_section_tagging_user_instruction(lines=lines_payload)},
        ],
        max_tokens=8192,
        http_timeout=float(settings.ORG_CHART_LLM_TIMEOUT),
        tool=section_tool,
    )
    parsed = _parse_llm_json(raw)
    out_rows = parsed.get("assignments") if isinstance(parsed, dict) else None
    assignments: dict[str, Optional[str]] = {}
    for row in out_rows or []:
        if isinstance(row, dict) and row.get("id") is not None:
            target = row.get("target")
            assignments[str(row["id"])] = target if isinstance(target, str) and target != "none" else None
    return assignments


async def _map_audit_financials_with_retries(
    *,
    db: AsyncSession,
    file_bytes: bytes,
    filename: str,
    raw_extracted: dict[str, Any],
    schema: dict[str, Any],
    max_attempts: int = 3,
    text_only_llm: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], int, dict[str, Any]]:
    """
    Pass-2 mapping: raw -> { mapped, unmatched }, with validation/retries.

    Fourth tuple element is ``other_components``: ``{other_*_path -> [signed component dicts]}`` —
    the original labels of lines auto-tagged into ``other_*`` catch-all slots, so the UI can show
    what was classified there. Empty for the legacy LLM mapper (the model emits a bare number with
    no source label to retain).

    ``text_only_llm``: use ``chat_completion`` (no document) — for deployment Bedrock after pass-1 extraction (text-only mapping).

    When ``settings.EXTRACTION_USE_DETERMINISTIC_MAPPER`` is set, a **hybrid** pass runs first:
    (1) a deterministic band-locked mapper places the confident, metric-critical lines (no LLM) —
    these are authoritative and cannot be cross-band by construction; (2) the LLM is asked to place
    only the *residual* lines as a constrained per-line assignment, and every assignment is
    re-validated against the source band + arithmetic role before it is accepted, so the LLM cannot
    reintroduce a cross-band placement. Signals ``attempts=0``. On any failure it falls through to
    the legacy LLM tree mapper below.
    """
    if settings.EXTRACTION_USE_DETERMINISTIC_MAPPER:
        try:
            cand_idx, alias_idx = await prepare_label_matcher(db, schema)
            other_components: dict[str, Any] = {}
            mapped, placed_paths, residual = split_confident_and_residual(
                raw_extracted, schema, cand_idx, alias_idx, other_components=other_components
            )
            if not _audit_financials_has_required_top_level(mapped):
                raise ValueError("deterministic mapper output missing required top-level")
            confident_count = len(_flatten_numeric_leaves(mapped))
            assignments: dict[str, Optional[str]] = {}
            if residual:
                assignments = await _assign_residual_with_llm(
                    residual_lines=residual, candidate_index=cand_idx
                )
            unmatched = apply_residual_assignments(
                mapped, placed_paths, residual, assignments, cand_idx, other_components=other_components
            )
            logger.info(
                "audit_financials hybrid mapper: confident=%d residual=%d placed_total=%d unmatched=%d other_slots=%d",
                confident_count,
                len(residual),
                len(_flatten_numeric_leaves(mapped)),
                len(unmatched),
                len(other_components),
            )
            return mapped, unmatched, 0, other_components
        except Exception as e:
            logger.warning(
                "hybrid deterministic mapper failed (%s); falling back to LLM mapper", str(e)[:300]
            )

    system = audit_financials_schema_mapping_system_prompt(schema=schema)
    raw_json = json.dumps(raw_extracted, ensure_ascii=False)
    user_instruction = (
        "Map the following raw extracted JSON into the canonical schema as per the system message.\n\n"
        f"Raw extracted JSON:\n{raw_json}"
    )
    # Enforce the canonical structure at the source via a forced tool/function call: the model
    # must return ``{mapped, unmatched}`` matching this JSON Schema, so it cannot invent sibling
    # keys at canonical positions (the cause of duplicate / mis-ordered lines). Overflow stays
    # capturable via the open ``other`` buckets + the ``unmatched`` array. ``finalize_*`` remains
    # the deterministic backstop (esp. for ordering and any residual deviation).
    mapping_tool = {
        "name": "emit_audit_financials_mapping",
        "schema": build_audit_financials_mapping_json_schema(schema),
    }
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Pass-2 is a structural remap of the pass-1 JSON, so it runs text-only on both
            # providers (Bedrock already did) with the tool enforcing the output shape.
            mapped_raw = await chat_completion(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_instruction},
                ],
                max_tokens=8192,
                http_timeout=float(settings.ORG_CHART_LLM_TIMEOUT),
                tool=mapping_tool,
            )
            parsed = _parse_llm_json(mapped_raw)
            mapped, unmatched = _split_audit_financials_mapping_result(parsed)
            if _audit_financials_has_required_top_level(mapped):
                # Legacy LLM mapper emits bare numbers at other_* slots — no source label to retain.
                return mapped, unmatched, attempt, {}
            raise ValueError("Mapped output missing required top-level sections")
        except Exception as e:
            last_exc = e
            logger.warning(
                "audit_financials mapping validation failed attempt=%s/%s err=%s",
                attempt,
                max_attempts,
                str(e)[:500],
            )
    logger.warning(
        "audit_financials mapping failed after %s attempts; using empty canonical shell: %s",
        max_attempts,
        last_exc,
    )
    shell = {k: {} for k in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL}
    merged = finalize_audit_financials_extracted(shell, schema)
    return merged, [], max_attempts, {}


async def _run_audit_qualitative_pass(
    *, file_bytes: bytes, filename: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Single LLM pass: auditor opinion + reporting scope + CARO/IFC/engagement metadata (schema v2).
    Non-fatal: returns (None, None) on total failure; either side may be None if partial.
    """
    user_instruction = (
        "Extract audit qualitative metadata from this document. "
        "Prioritize the Independent Auditor's Report, CARO, and IFC sections near the front of the pack. "
        "Return only the JSON object described in the system message (version 2)."
    )
    last_err: Optional[Exception] = None
    for attempt in range(1, 3):
        try:
            raw = await completion_with_document(
                system_instruction=AUDIT_QUALITATIVE_SYSTEM_PROMPT,
                user_instruction=user_instruction
                if attempt == 1
                else (
                    user_instruction
                    + " Your previous response was invalid or incomplete. Return ONLY valid JSON matching version 2."
                ),
                file_bytes=file_bytes,
                filename=filename,
                max_tokens=8192,
                http_timeout=float(settings.BEDROCK_PDF_LLM_TIMEOUT),
            )
            parsed = _parse_llm_json(raw)
            opinion, qualitative = split_combined_qualitative_response(parsed)
            if opinion is not None or qualitative is not None:
                return opinion, qualitative
            last_err = ValueError("qualitative pass returned empty opinion and qualitative")
        except Exception as e:
            last_err = e
            logger.warning(
                "audit qualitative pass attempt %s failed: %s",
                attempt,
                str(e)[:400],
            )
    if last_err:
        logger.warning("audit qualitative pass failed after retries: %s", str(last_err)[:400])
    return None, None


def _parse_leaf_number(v: Any) -> Optional[float]:
    """Match financial_data_extraction_sync: allow int/float and common numeric strings."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return x
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        if not s:
            return None
        if s.startswith("(") and s.endswith(")"):
            s = f"-{s[1:-1]}"
        try:
            x = float(s)
        except ValueError:
            return None
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return x
    return None


def _flatten_numeric_leaves(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Recursively collect every leaf numeric (int/float, not bool) value as a dotted-path → value map."""
    result: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return result
    for k, v in obj.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, bool):
            continue
        if isinstance(v, dict):
            result.update(_flatten_numeric_leaves(v, path))
            continue
        n = _parse_leaf_number(v)
        if n is not None:
            result[path] = n
    return result


async def _run_source_refs_pass(
    *,
    file_bytes: bytes,
    filename: str,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    """
    Separate LLM pass: ask the model to locate each leaf numeric value in the document.

    Always uses ``completion_with_document`` so the active provider (OpenAI file part or
    Bedrock inline PDF bytes) receives the actual file — required for real page numbers and
    verbatim snippets. Non-PDF types fall back to extracted text inside the client.

    Returns a flat ``{dotted_path: {"page": int, "text_snippet": str}}`` dict.
    Never raises — returns an empty dict on any failure so the caller can store it safely.

    This pass does NOT modify ``extracted``; it is purely additive metadata.
    """
    flat = _flatten_numeric_leaves(extracted)
    if not flat:
        return {}

    flat_json = json.dumps(flat, ensure_ascii=False, separators=(",", ":"))
    user_msg = source_refs_user_prompt(flat_json)

    try:
        raw = await completion_with_document(
            system_instruction=SOURCE_REFS_SYSTEM_PROMPT,
            user_instruction=user_msg,
            file_bytes=file_bytes,
            filename=filename,
            max_tokens=16384,
            # Same ceiling as primary ``completion_with_document`` for audit PDFs (large output).
            http_timeout=float(settings.BEDROCK_PDF_LLM_TIMEOUT),
        )

        parsed = _parse_llm_json(raw)
        validated: dict[str, Any] = {}
        for path, ref in parsed.items():
            if not isinstance(path, str) or not isinstance(ref, dict):
                continue
            page = ref.get("page")
            snippet = ref.get("text_snippet")
            if not isinstance(page, int) or page < 1:
                try:
                    page = int(page)  # type: ignore[arg-type]
                except Exception:
                    continue
            if not isinstance(snippet, str) or not snippet.strip():
                continue
            # Strip control characters that would break JSON serialisation.
            snippet = re.sub(r'[\x00-\x1f\x7f]', ' ', snippet).strip()
            if not snippet:
                continue
            validated[path] = {"page": page, "text_snippet": snippet[:120]}
        return validated
    except Exception as exc:
        logger.warning("source_refs pass failed (non-fatal): %s", str(exc)[:400])
        _raw = locals().get("raw")
        logger.debug(
            "source_refs raw LLM response (first 2000 chars): %s",
            (_raw[:2000] if isinstance(_raw, str) else "<not set>"),
        )
        return {}


# Minimum normalised-snippet length to trust a deterministic page match (avoids trivial hits).
# ── Deterministic source-ref locator (pdfplumber: real text + word coordinates) ──
#
# We never trust the LLM's page number. Instead we anchor on what the document actually contains —
# the LLM's verbatim snippet (its own wording, which resolves the semantic-mapping problem) and the
# value sitting on the same line as a label token — and keep a ref ONLY when we can locate it.
# No inheritance, no guessing: a ref is "verified" (exact page + bbox) or "unverified" (no link).

_SR_MIN_SNIPPET_NORM = 8      # min normalised-snippet length to trust a snippet hit
_SR_MIN_VALUE_DIGITS = 4      # a value needs this many digits to be distinctive enough to anchor on
_SR_LINE_Y_TOL = 3.0          # points: words within this vertical gap belong to the same visual line
_SR_VALUE_MAX_MATCHES = 6     # value+label hits beyond this are too ambiguous to trust
_SR_STOPWORDS = {
    "and", "the", "of", "to", "for", "other", "total", "net", "less", "add", "from",
    "year", "current", "non", "with", "per", "tax", "before",
}


def _sr_norm_text(s: str) -> str:
    """Lowercase + drop everything but [a-z0-9] so spacing/grouping/punctuation don't block matches."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _sr_label_tokens(path: str) -> set:
    """Significant words from a dotted leaf path's last segment — the label anchor for value matching."""
    leaf = path.rsplit(".", 1)[-1]
    return {w.lower() for w in leaf.split("_") if len(w) >= 4 and w.lower() not in _SR_STOPWORDS}


def _sr_value_digit_candidates(value: float, scale: Optional[float]) -> set:
    """Digit-only strings the value could be printed as (document scale + decimal variants)."""
    out: set = set()
    absval = abs(float(value))
    if absval == 0:
        return out
    scales = [float(scale)] if (scale and scale > 0) else []
    scales += [1.0, 1e2, 1e3, 1e5, 1e6, 1e7]
    for sc in scales:
        printed = absval / sc
        if printed < 1:
            continue
        for dec in (0, 1, 2):
            digits = re.sub(r"[^0-9]", "", f"{printed:.{dec}f}")
            if len(digits) >= _SR_MIN_VALUE_DIGITS:
                out.add(digits)
    return out


def _sr_extract_pages(file_bytes: bytes) -> list:
    """Return [(width, height, lines, page_norm), ...]; each line = {norm, x0, x1, top, bottom}."""
    from io import BytesIO

    import pdfplumber

    pages: list = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            try:
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
            except Exception:
                words = []
            lines: list = []
            for w in sorted(words, key=lambda d: (round(float(d["top"])), float(d["x0"]))):
                top, bottom, x0, x1 = float(w["top"]), float(w["bottom"]), float(w["x0"]), float(w["x1"])
                if lines and abs(top - lines[-1]["_anchor"]) <= _SR_LINE_Y_TOL:
                    ln = lines[-1]
                    ln["_words"].append(w["text"])
                    ln["x0"], ln["x1"] = min(ln["x0"], x0), max(ln["x1"], x1)
                    ln["top"], ln["bottom"] = min(ln["top"], top), max(ln["bottom"], bottom)
                else:
                    lines.append({"_words": [w["text"]], "_anchor": top, "x0": x0, "x1": x1, "top": top, "bottom": bottom})
            for ln in lines:
                ln["norm"] = _sr_norm_text(" ".join(ln["_words"]))
            page_norm = "".join(ln["norm"] for ln in lines)
            pages.append((float(page.width or 0), float(page.height or 0), lines, page_norm))
    return pages


def _verify_and_annotate_source_refs(
    *,
    file_bytes: bytes,
    refs: dict[str, Any],
    extracted: dict[str, Any],
    ocr_pages: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Deterministically locate each numeric leaf's exact page (+ bounding box) using the PDF's real
    text and word coordinates (pdfplumber). A ref is ``verified`` ONLY when located:
      - the LLM's verbatim snippet (the document's own wording) is found on a line, OR
      - the value's printed form sits on the same line as a label token.
    Anything else is ``unverified`` (no page change, no bbox) and the UI shows no link.
    No inheritance / no guessing. Additive and non-fatal: on any error refs come back ``unverified``.

    ``ocr_pages`` (``{page_number: ocr_text}``) is an optional fallback for **scanned** PDFs that
    have no text layer — reuse the per-page OCR text the extraction already produced. In that mode
    matching is page-level only (page number, no bounding box). Digital PDFs are unaffected.
    """
    def _all_unverified() -> dict:
        return {p: {**r, "source": "unverified"} for p, r in refs.items() if isinstance(r, dict)}

    try:
        pages = _sr_extract_pages(file_bytes)
    except Exception as exc:  # corrupt/encrypted/unsupported — degrade, never break extraction
        logger.warning("source_refs locate: PDF text extraction failed (%s)", str(exc)[:200])
        pages = []

    if not any(p[3] for p in pages):  # no text layer (scanned) → fall back to reused OCR text
        norm_ocr: dict = {}
        if ocr_pages:
            for k, v in dict(ocr_pages).items():
                try:
                    norm_ocr[int(k)] = str(v or "")
                except (TypeError, ValueError):
                    continue
        if norm_ocr:
            mx = max(norm_ocr)
            # Page-level pages: page text only, no line coords (matches by page number, no bbox).
            pages = [(0.0, 0.0, [], _sr_norm_text(norm_ocr.get(p, ""))) for p in range(1, mx + 1)]
        if not any(p[3] for p in pages):
            return _all_unverified()

    scale = _parse_leaf_number(extracted.get("figures_scale_to_smallest_unit")) if isinstance(extracted, dict) else None
    flat = _flatten_numeric_leaves(extracted)

    def _bbox(ln, w, h):
        if w <= 0 or h <= 0:
            return None
        return [round(ln["x0"] / w, 4), round(ln["top"] / h, 4), round(ln["x1"] / w, 4), round(ln["bottom"] / h, 4)]

    def _locate_snippet(snippet: str, hint: int):
        nsnip = _sr_norm_text(snippet)
        if len(nsnip) < _SR_MIN_SNIPPET_NORM:
            return None
        line_cands, page_cands = [], []
        for pi, (w, h, lines, pnorm) in enumerate(pages):
            if nsnip not in pnorm:
                continue
            for li, ln in enumerate(lines):
                if nsnip in ln["norm"] or (li + 1 < len(lines) and nsnip in ln["norm"] + lines[li + 1]["norm"]):
                    line_cands.append((pi + 1, ln, w, h))
                    break
            if not lines:  # OCR page (no line coords) → page-level hit, no bbox
                page_cands.append((pi + 1, None, w, h))
        cands = line_cands or page_cands
        if not cands:
            return None
        page, ln, w, h = min(cands, key=lambda c: abs(c[0] - hint))
        return page, (_bbox(ln, w, h) if ln else None)

    def _locate_value(value, path: str, hint: int):
        digits = _sr_value_digit_candidates(value, scale)
        toks = _sr_label_tokens(path)
        if not digits or not toks:
            return None
        line_cands, page_cands = [], []
        for pi, (w, h, lines, pnorm) in enumerate(pages):
            if not any(d in pnorm for d in digits):
                continue
            for ln in lines:
                nm = ln["norm"]
                if any(d in nm for d in digits) and any(t in nm for t in toks):
                    line_cands.append((pi + 1, ln, w, h))
            if not lines and any(t in pnorm for t in toks):  # OCR page → page-level (digit matched)
                page_cands.append((pi + 1, None, w, h))
        cands = line_cands or page_cands
        if not cands or len(cands) > _SR_VALUE_MAX_MATCHES:
            return None
        page, ln, w, h = min(cands, key=lambda c: abs(c[0] - hint))
        return page, (_bbox(ln, w, h) if ln else None)

    out: dict[str, Any] = {}
    # Stage 1: every LLM ref → snippet first, then value+label fallback.
    for path, ref in refs.items():
        if not isinstance(ref, dict):
            continue
        hint = ref.get("page") if isinstance(ref.get("page"), int) else 1
        loc = _locate_snippet(ref.get("text_snippet") or "", hint)
        if loc is None and path in flat:
            loc = _locate_value(flat[path], path, hint)
        if loc is not None:
            page, bbox = loc
            out[path] = {**ref, "page": page, "source": "verified", **({"bbox": bbox} if bbox else {})}
        else:
            out[path] = {**ref, "source": "unverified"}

    # Stage 2: leaves the LLM never ref'd → try value+label location (accurate coverage recovery).
    for path, value in flat.items():
        if path in out:
            continue
        loc = _locate_value(value, path, 1)
        if loc is not None:
            page, bbox = loc
            out[path] = {"page": page, "text_snippet": "", "source": "verified", **({"bbox": bbox} if bbox else {})}

    return out


def _annotate_unmatched_source_refs(
    file_bytes: bytes, unmatched: list, scale: Any = None, ocr_pages: Optional[dict] = None
) -> None:
    """Locate each unmatched line in the PDF and stamp ``source_ref`` on the row (mutates in place).

    Purely additive: reuses the same deterministic value+label locator as canonical fields. An
    unmatched line's ``document_label`` is the document's own wording, so value+label matches
    reliably with **no LLM call**. ``ocr_pages`` enables the scanned-PDF fallback (page-level).
    Rows that can't be located are left as-is (no ``source_ref``). Best-effort — callers wrap in
    try/except so it can never break extraction.
    """
    if not isinstance(unmatched, list) or not unmatched:
        return
    tree: dict[str, Any] = {}
    if scale is not None:
        tree["figures_scale_to_smallest_unit"] = scale
    keyed: list[tuple[dict, str]] = []
    for i, row in enumerate(unmatched):
        if not isinstance(row, dict):
            continue
        val = row.get("value")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        label = str(row.get("document_label") or row.get("key") or "").strip()
        if not label:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "line"
        # Unique key per row; the trailing "__<i>" is dropped by the label-token length filter,
        # so the matcher still anchors on the label words only.
        key = f"{slug}__{i}"
        tree[key] = val
        keyed.append((row, key))
    if not keyed:
        return
    located = _verify_and_annotate_source_refs(
        file_bytes=file_bytes, refs={}, extracted=tree, ocr_pages=ocr_pages
    )
    for row, key in keyed:
        ref = located.get(key)
        if isinstance(ref, dict) and ref.get("source") == "verified" and isinstance(ref.get("page"), int):
            row["source_ref"] = {
                "page": ref["page"],
                "text_snippet": ref.get("text_snippet") or "",
                "source": "verified",
            }


# For scanned docs we OCR pages just to locate statements; cap to bound cost on pathological PDFs
# (real audit reports keep their statements well within the first ~80 pages).
_SCANNED_OCR_PAGE_CAP = 80


def _is_financials_kind(kind: str) -> bool:
    return (kind or "").strip().lower() in ("audit_financials", "financials", "audit-financials")


def _statement_image_user_prompt(digit_block: str, *, scanned: bool) -> str:
    """User message for the image+text primary pass: images give column layout, the digit block
    (PDF text layer for digital, Textract cells for scanned) is the authoritative digit source."""
    digit_source = "Textract-extracted table cells" if scanned else "the PDF's exact text layer"
    if len(digit_block) > MAX_DOCUMENT_CHARS:
        digit_block = digit_block[:MAX_DOCUMENT_CHARS] + "\n\n[truncated]"
    return (
        'Extraction kind: "audit_financials". The attached image(s) are the primary financial-'
        "statement pages of an audit report. Use the IMAGES to SEE the table layout and pick the "
        "single correct column (latest period, Group/Consolidated) exactly as the system message "
        f"specifies. For EXACT digit values use {digit_source} below — it is authoritative for the "
        'numbers (note a leading digit may be split by a space, e.g. "5 39,211" means 539211; '
        '"1 5,796,022" means 15796022 — recombine such splits). Answer with ONLY the JSON object.'
        f"\n\n=== Statement page text ({digit_source}) ===\n{digit_block}"
    )


async def _run_statement_image_primary_pass(
    *, file_bytes: bytes, filename: str, storage_uri: Optional[str]
) -> Optional[tuple[str, dict]]:
    """
    Image+text primary extraction for audit-financials PDFs. Renders only the located statement
    pages and has the LLM read them (columns visible) with the strict prompt, anchoring digits to
    the PDF text layer (digital) or Textract table cells (scanned).

    Returns ``(raw_llm_json_text, meta)`` or ``None`` to signal the caller to fall back to the
    legacy whole-document pass (no statements located, scanned+Textract disabled/unavailable, etc).
    """
    target_px = settings.EXTRACTION_RENDER_TARGET_PX
    timeout = float(settings.BEDROCK_PDF_LLM_TIMEOUT)
    doc_type = detect_pdf_doc_type(file_bytes)

    if doc_type == "digital":
        loc = locate_statement_pages(file_bytes)
        if not loc.pages_to_render:
            logger.info("image pipeline: no statement pages located in digital PDF; using legacy pass")
            return None
        page_text = extract_pdf_pages_text(file_bytes, loc.pages_to_render)
        digit_block = "\n\n".join(
            f"--- Page {i + 1} ---\n{(page_text.get(i, '') or '').strip()}" for i in loc.pages_to_render
        )
        user = _statement_image_user_prompt(digit_block, scanned=False)
        images = render_pdf_pages_to_png(file_bytes, loc.pages_to_render, target_px)
        raw = await completion_with_images(
            system_instruction=AUDIT_FINANCIALS_SYSTEM_PROMPT,
            user_instruction=user,
            images=images,
            max_tokens=8192,
            http_timeout=timeout,
        )
        return raw, {
            "doc_type": "digital",
            "statement_pages": [i + 1 for i in loc.pages_to_render],
            "missing_primary": loc.missing_primary,
        }

    # --- scanned / hybrid: scanned pages need Textract for OCR and table extraction. ---
    # "hybrid" = audit report text is digital but financial statement pages are scanned images
    # embedded in the same PDF (e.g. Consolida-style). The digital locator picks up Notes pages
    # as false positives; Textract OCR on all pages locates and extracts the real statements.
    if not settings.EXTRACTION_TEXTRACT_ENABLED:
        logger.info("image pipeline: scanned/hybrid PDF but Textract disabled; using legacy pass")
        return None
    n_pages = pdf_page_count(file_bytes)
    if n_pages == 0:
        return None
    cap = min(n_pages, _SCANNED_OCR_PAGE_CAP)
    all_images = render_pdf_pages_to_png(file_bytes, list(range(cap)), target_px)

    # OCR every page (bounded concurrency) just to LOCATE the statements.
    sem = asyncio.Semaphore(8)

    async def _ocr(img: bytes) -> str:
        async with sem:
            return await asyncio.to_thread(detect_text_for_image, img)

    ocr_texts = await asyncio.gather(*[_ocr(img) for img in all_images])
    loc = locate_from_page_texts(list(ocr_texts))
    if not loc.pages_to_render:
        logger.info("image pipeline: no statements located in scanned OCR; using legacy pass")
        return None

    stmt_indices = [i for i in loc.pages_to_render if i < len(all_images)]
    stmt_images = [all_images[i] for i in stmt_indices]
    blocks = []
    for i in stmt_indices:
        tables = await asyncio.to_thread(tables_for_image, all_images[i])
        tbl_text = "\n\n".join(t.to_lines() for t in tables) if tables else ocr_texts[i]
        blocks.append(f"--- Page {i + 1} ---\n{tbl_text}")
    user = _statement_image_user_prompt("\n\n".join(blocks), scanned=True)
    raw = await completion_with_images(
        system_instruction=AUDIT_FINANCIALS_SYSTEM_PROMPT,
        user_instruction=user,
        images=stmt_images,
        max_tokens=8192,
        http_timeout=timeout,
    )
    return raw, {
        "doc_type": "scanned",
        "statement_pages": [i + 1 for i in stmt_indices],
        "missing_primary": loc.missing_primary,
        # Per-page OCR text (1-based) — reused by the source-ref locator so scanned docs get page
        # references with no extra Textract cost. Kept transient (not persisted into ocr_json).
        "ocr_pages_text": {i + 1: (ocr_texts[i] or "") for i in range(len(ocr_texts))},
    }


async def _sync_auditor_to_pcm(
    db: AsyncSession,
    file_row: File,
    auditor_firm: Optional[str],
    auditor_tier_label: Optional[str],
) -> None:
    """Write auditor name + category back to all matching PCM rows for this deal / review cycle."""
    if not auditor_firm:
        return
    if file_row.portfolio_company_id is None:
        return

    from src.db.models import PortfolioCompany, PortfolioCompanyMetadata

    pc = await db.get(PortfolioCompany, file_row.portfolio_company_id)
    if pc is None or not pc.company_id:
        return

    review_cycle_id = file_row.review_cycle_id or pc.review_cycle_id

    stmt = select(PortfolioCompanyMetadata).where(
        PortfolioCompanyMetadata.deal_id == pc.company_id,
        PortfolioCompanyMetadata.review_cycle_id == review_cycle_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        logger.debug(
            "_sync_auditor_to_pcm: no PCM rows for deal_id=%s review_cycle_id=%s",
            pc.company_id,
            review_cycle_id,
        )
        return

    for row in rows:
        row.auditor = auditor_firm
        row.category_of_auditor = auditor_tier_label
    logger.info(
        "_sync_auditor_to_pcm: updated %d PCM row(s) deal_id=%s review_cycle_id=%s auditor=%r tier=%r",
        len(rows),
        pc.company_id,
        review_cycle_id,
        auditor_firm,
        auditor_tier_label,
    )


async def run_audit_document_extraction(db: AsyncSession, file_id: int, kind: str) -> None:
    file_row = (await db.execute(select(File).where(File.id == file_id))).scalar_one_or_none()
    if not file_row or not file_row.storage_uri:
        raise ValueError("File not found or missing storage_uri")

    data = download_storage_uri(file_row.storage_uri)
    if not data:
        raise ValueError("Empty file in S3")

    provider = effective_document_extraction_provider(settings)
    if provider == "openai":
        validate_llm_config(settings, provider="openai")
    else:
        validate_llm_config(settings, provider="bedrock")

    logger.info(
        "audit_document LLM extraction file_id=%s kind=%s provider=%s",
        file_id,
        kind,
        provider,
    )

    filename = file_row.filename or "document.pdf"
    is_pdf = filename.lower().strip().endswith(".pdf")
    is_xlsx = is_xlsx_filename(filename)

    system = audit_system_prompt_for_kind(kind, is_spreadsheet=is_xlsx)
    base_user_instruction = (
        f'Extraction kind: "{kind}". Read the attached document and answer with JSON '
        f"exactly as specified in the system message."
    )

    user_instruction = base_user_instruction
    if not is_pdf and not is_xlsx:
        extracted_text = extract_text_from_file_bytes(filename, data)
        if not extracted_text.strip():
            logger.warning(
                "audit_document extraction: empty text from non-PDF file_id=%s filename=%s",
                file_id,
                filename,
            )
        if len(extracted_text) > MAX_DOCUMENT_CHARS:
            extracted_text = extracted_text[:MAX_DOCUMENT_CHARS] + "\n\n[truncated]"
        user_instruction = f"{base_user_instruction}\n\nDocument text:\n\n{extracted_text}"

    # Image+text statement pipeline (opt-in): for audit-financials PDFs, render only the located
    # statement pages and let the LLM read them (columns visible) instead of the whole document —
    # fixes the multi-column year/entity mixing. Falls back to the legacy pass on any miss/error.
    image_meta: Optional[dict] = None
    raw: Optional[str] = None
    if settings.EXTRACTION_USE_IMAGE_PIPELINE and is_pdf and _is_financials_kind(kind):
        try:
            result = await _run_statement_image_primary_pass(
                file_bytes=data, filename=filename, storage_uri=file_row.storage_uri
            )
            if result is not None:
                raw, image_meta = result
                logger.info("audit_document: used image+text statement pipeline meta=%s", image_meta)
        except Exception as e:
            logger.warning(
                "image+text pipeline failed (%s); falling back to legacy whole-document pass",
                str(e)[:300],
            )

    if raw is None:
        raw = await completion_with_document(
            system_instruction=system,
            user_instruction=user_instruction,
            file_bytes=data,
            filename=filename,
            storage_uri=file_row.storage_uri,
            max_tokens=8192,
            # Use the PDF-specific timeout (default 360 s) — large financial PDFs with
            # max_tokens=8192 can take 3-4 min on Claude Sonnet 4.x via Bedrock.
            http_timeout=float(settings.BEDROCK_PDF_LLM_TIMEOUT),
        )

    try:
        extracted = _parse_llm_json(raw)
    except Exception as e:
        logger.warning("audit_document JSON parse failed, storing raw: %s", e)
        extracted = {"parse_error": str(e)[:500], "raw_model_text": raw[:12000]}

    # Pass-2: map audit_financials into { mapped, unmatched } for human review (never raises — falls back to empty shell).
    mapped_extracted: Optional[dict[str, Any]] = None
    mapping_attempts: Optional[int] = None
    audit_unmatched: list[dict[str, Any]] = []
    audit_field_components: dict[str, Any] = {}
    financial_schema: Optional[dict[str, Any]] = None
    k_fin = (kind or "").strip().lower() in ("audit_financials", "financials", "audit-financials")
    if k_fin and isinstance(extracted, dict) and "parse_error" not in extracted:
        financial_schema = await load_audit_financials_schema(db)
        mapped_extracted, audit_unmatched, mapping_attempts, audit_field_components = (
            await _map_audit_financials_with_retries(
                db=db,
                file_bytes=data,
                filename=filename,
                raw_extracted=extracted,
                schema=financial_schema,
                max_attempts=3,
                text_only_llm=(provider == "bedrock"),
            )
        )
        # Dedupe guard: the mapper sometimes emits the same line twice (nested inside a sub-group
        # AND as a flat sibling), inflating the parent total. Remove the double-counted copies and
        # surface them in the unmatched panel (recoverable) so totals reconcile. Best-effort.
        try:
            if isinstance(mapped_extracted, dict):
                mapped_extracted, _dupes = dedupe_duplicate_leaves(mapped_extracted, financial_schema)
                if _dupes:
                    audit_unmatched = list(audit_unmatched) + _dupes
                    logger.info(
                        "audit_financials dedupe moved %d duplicate leaf(s) to unmatched file_id=%s",
                        len(_dupes),
                        file_id,
                    )
        except Exception as e:
            logger.warning("audit_financials dedupe step failed (non-fatal): %s", str(e)[:300])

        # Finalize the mapped tree (schema defaults → synonym consolidation → formulas → ordering)
        # BEFORE the coverage guard so the guard checks against the same leaf set that will be
        # stored — the raw mapper tree has ~2× more numeric leaves (intermediate nodes, open
        # ``other`` buckets) that inflate the "placed" counter and hide dropped lines.
        try:
            if mapped_extracted is not None and financial_schema is not None:
                mapped_extracted = finalize_audit_financials_extracted(mapped_extracted, financial_schema)
        except Exception as e:
            logger.warning("audit_financials finalize step failed (non-fatal): %s", str(e)[:300])

        # Coverage guard: catch pass-1 lines the mapper dropped/buried (silent misses), then
        # enrich every unmatched row with a deterministic canonical suggestion + alias hits so
        # the HITL panel can offer one-click attach. Best-effort — never block extraction.
        # Hoisted so the section-tagging step below can reuse the alias memory even if enrich throws.
        cand_idx: list = []
        alias_idx: dict = {}
        try:
            residuals = _audit_financials_coverage_residuals(extracted, mapped_extracted or {}, audit_unmatched)
            if residuals:
                logger.info(
                    "audit_financials coverage guard found %d unaccounted line(s) file_id=%s",
                    len(residuals),
                    file_id,
                )
            audit_unmatched = list(audit_unmatched) + residuals
            cand_idx, alias_idx = await prepare_label_matcher(db, financial_schema)
            audit_unmatched = enrich_unmatched_rows(
                audit_unmatched, candidate_index=cand_idx, aliases_index=alias_idx
            )
        except Exception as e:
            logger.warning("audit_financials coverage/enrich step failed (non-fatal): %s", str(e)[:400])

        # Section tagging: stamp every unmatched row with a `placement_path` (deepest confident
        # section) so the UI nests it inside that section instead of floating at the statement root.
        # Deterministic first (suggestion ancestor / band); a small-universe LLM pick resolves the
        # rest. Non-fatal — a failure just leaves rows un-tagged (Tier 3), the safe default.
        try:
            audit_unmatched, _need_llm = assign_placement_paths(
                audit_unmatched, financial_schema, aliases_index=alias_idx
            )
            if _need_llm:
                _sec = await _assign_sections_with_llm(rows=_need_llm, schema=financial_schema)
                _promoted = apply_llm_section_assignments(_need_llm, _sec, financial_schema)
                logger.info(
                    "audit_financials section tagging: deterministic=%d llm_resolved=%d/%d file_id=%s",
                    sum(
                        1
                        for r in audit_unmatched
                        if isinstance(r, dict)
                        and r.get("placement_source") in ("suggestion", "band", "memory")
                    ),
                    _promoted,
                    len(_need_llm),
                    file_id,
                )
        except Exception as e:
            logger.warning("audit_financials section tagging failed (non-fatal): %s", str(e)[:400])

        # Auto-accept: non-total rows with suggestion confidence >= 0.85 are placed directly
        # into mapped_extracted without requiring HITL, provided the target slot is empty.
        AUTO_ACCEPT_CONFIDENCE = 0.85
        try:
            if mapped_extracted is not None:
                still_unmatched: list[Any] = []
                auto_accepted = 0
                for row in audit_unmatched:
                    if not isinstance(row, dict):
                        still_unmatched.append(row)
                        continue
                    if row.get("is_total"):
                        still_unmatched.append(row)
                        continue
                    sug = row.get("suggestion")
                    if not isinstance(sug, dict):
                        still_unmatched.append(row)
                        continue
                    conf = float(sug.get("confidence") or 0.0)
                    full_path = sug.get("full_path") or f"{sug.get('parent_path', '')}.{sug.get('key', '')}"
                    if conf < AUTO_ACCEPT_CONFIDENCE or not full_path.strip("."):
                        still_unmatched.append(row)
                        continue
                    # Only place into an empty slot — never silently overwrite.
                    parts = full_path.split(".")
                    cur: Any = mapped_extracted
                    for p in parts:
                        cur = cur.get(p) if isinstance(cur, dict) else None
                    if cur is not None:
                        still_unmatched.append(row)
                        continue
                    raw_val = row.get("value")
                    try:
                        val: Any = float(raw_val) if raw_val is not None else 0.0
                    except (TypeError, ValueError):
                        val = 0.0
                    set_value_at_dotted_path(mapped_extracted, full_path, val)
                    auto_accepted += 1
                audit_unmatched = still_unmatched
                if auto_accepted:
                    logger.info(
                        "audit_financials auto-accepted %d high-confidence non-total row(s) file_id=%s",
                        auto_accepted,
                        file_id,
                    )
        except Exception as e:
            logger.warning("audit_financials auto-accept step failed (non-fatal): %s", str(e)[:400])

    # Qualitative pass (auditor opinion, CARO, IFC) is skipped for Excel workbooks —
    # supplementary schedules do not contain an auditor's report narrative.
    opinion_stored: Optional[dict[str, Any]] = None
    qualitative_stored: Optional[dict[str, Any]] = None
    if k_fin and not is_xlsx:
        try:
            opinion_stored, qualitative_stored = await _run_audit_qualitative_pass(
                file_bytes=data, filename=filename
            )
        except Exception as e:
            logger.warning("audit qualitative pass failed (non-fatal): %s", str(e)[:400])
    elif k_fin and is_xlsx:
        logger.info("audit qualitative pass skipped for Excel file file_id=%s", file_id)

    meta = (
        await db.execute(select(FileOCRMetadata).where(FileOCRMetadata.file_id == file_id))
    ).scalar_one_or_none()
    if meta is None:
        meta = FileOCRMetadata(file_id=file_id, ocr_json={})
        db.add(meta)
        await db.flush()

    meta.ocr_text = None
    oj = dict(meta.ocr_json or {})
    # mapped_extracted is already finalized (done before coverage guard above).
    ex_for_store: Any = mapped_extracted if mapped_extracted is not None else extracted
    extra: dict[str, Any] = {
        "status": "completed",
        "kind": kind,
        "provider": provider,
        "file_format": "xlsx" if is_xlsx else ("pdf" if is_pdf else "other"),
        "extracted": ex_for_store,
        "raw_extracted": extracted,
    }
    if mapping_attempts is not None:
        extra["mapping_attempts"] = mapping_attempts
        extra["schema_config_key"] = AUDIT_FINANCIALS_SCHEMA_CONFIG_KEY
        extra["audit_financials_unmatched"] = audit_unmatched
        # Labels of lines auto-tagged into ``other_*`` catch-all slots, so the UI can show what was
        # classified there (surfaced via the existing field-components breakdown). Empty → omit.
        if audit_field_components:
            extra["audit_financials_field_components"] = audit_field_components
    oj.update(extra)

    # Preserve any prior manual override; otherwise let the LLM fill it.
    if k_fin and oj.get("currency_source") != "manual":
        detected = _pick_currency_from_extraction(extracted)
        if detected:
            oj["currency"] = detected
            oj["currency_source"] = "auto"
            file_row.original_currency = detected
        else:
            logger.info("audit_financials extraction did not yield a currency file_id=%s", file_id)

    if k_fin and isinstance(extracted, dict) and "parse_error" not in extracted:
        fd, fs = _pick_figures_metrics_from_extraction(extracted)
        if fd:
            oj["figures_denomination"] = fd
        else:
            oj.pop("figures_denomination", None)
        if fs is not None:
            oj["figures_scale_to_smallest_unit"] = fs
        else:
            oj.pop("figures_scale_to_smallest_unit", None)

    # Pass-3 (source refs): locate each numeric value in the PDF — non-fatal separate call.
    # Skipped for Excel workbooks: the pass requires a paginated PDF and page-based refs.
    # Runs only when primary extraction succeeded and we have a mapped tree to annotate.
    _source_ref_input = ex_for_store if isinstance(ex_for_store, dict) else extracted
    if is_xlsx:
        oj["source_refs"] = {}
        oj["source_refs_status"] = "skipped_xlsx"
        logger.info("source_refs pass skipped for Excel file_id=%s", file_id)
    elif isinstance(_source_ref_input, dict) and "parse_error" not in _source_ref_input:
        logger.info("source_refs pass starting file_id=%s kind=%s", file_id, kind)
        source_refs = await _run_source_refs_pass(
            file_bytes=data,
            filename=filename,
            extracted=_source_ref_input,
        )
        # Pass-3b: deterministically verify each ref's page against the PDF text + tag confidence.
        # Non-fatal: on any failure the refs are returned annotated "unverified".
        # For scanned PDFs, reuse the per-page OCR text the image pipeline already produced.
        _ocr_pages = image_meta.get("ocr_pages_text") if isinstance(image_meta, dict) else None
        try:
            source_refs = await asyncio.to_thread(
                _verify_and_annotate_source_refs,
                file_bytes=data,
                refs=source_refs,
                extracted=_source_ref_input,
                ocr_pages=_ocr_pages,
            )
        except Exception as exc:  # never let verification break extraction
            logger.warning("source_refs verify pass failed (non-fatal): %s", str(exc)[:300])
        oj["source_refs"] = source_refs
        oj["source_refs_status"] = "completed" if source_refs else "empty"
        logger.info(
            "source_refs pass done file_id=%s refs_count=%d", file_id, len(source_refs)
        )
        # Pass-3c (additive): also locate the unmatched lines so the HITL attach UI can show
        # where each sits in the PDF. Deterministic value+label only (no LLM) — an unmatched
        # line's document_label is the document's own wording, so it matches reliably.
        _um = oj.get("audit_financials_unmatched")
        if isinstance(_um, list) and _um:
            try:
                await asyncio.to_thread(
                    _annotate_unmatched_source_refs,
                    data,
                    _um,
                    oj.get("figures_scale_to_smallest_unit"),
                    _ocr_pages,
                )
                logger.info(
                    "unmatched source_refs located file_id=%s n=%d/%d",
                    file_id,
                    sum(1 for r in _um if isinstance(r, dict) and r.get("source_ref")),
                    len(_um),
                )
            except Exception as exc:  # never let it break extraction
                logger.warning("unmatched source_refs locate failed (non-fatal): %s", str(exc)[:300])
    else:
        oj["source_refs"] = {}
        oj["source_refs_status"] = "skipped"

    if k_fin and isinstance(ex_for_store, dict) and "parse_error" not in ex_for_store:
        src_cur = oj.get("currency") if isinstance(oj.get("currency"), str) else None
        if src_cur:
            # Determine target currency and fy_end date from the attached entity/company.
            target_cur: Optional[str] = None
            fy_date = None
            if file_row.entity_id is not None:
                from src.db.models import Entity as _Entity, PortfolioCompany as _PC
                ent = (await db.execute(select(_Entity).where(_Entity.id == file_row.entity_id))).scalar_one_or_none()
                if ent is not None:
                    fy_end_str = normalize_fy_end(ent.fy_end)
                    if fy_end_str:
                        try:
                            fy_date = fy_end_last_day(fy_end_str)
                        except ValueError:
                            fy_date = None
                    if ent.portfolio_company_id is not None:
                        pc = await db.get(_PC, ent.portfolio_company_id)
                        if pc is not None and pc.currency:
                            target_cur = pc.currency.strip().upper()

            if fy_date is not None and target_cur and target_cur != src_cur:
                # Entity attached with fy_end and company has a target currency — use historical rate.
                converted_tree, new_cur, fx_meta = await auto_convert_extracted_tree(
                    db,
                    ex_for_store,
                    from_currency=src_cur,
                    target_currency=target_cur,
                    fy_end_date=fy_date,
                    log_context=f"file_id={file_id}",
                )
            elif fy_date is not None and src_cur not in ("USD", "INR"):
                # Entity attached with fy_end but no company currency — fall back to INR.
                converted_tree, new_cur, fx_meta = await auto_convert_extracted_tree(
                    db,
                    ex_for_store,
                    from_currency=src_cur,
                    target_currency="INR",
                    fy_end_date=fy_date,
                    log_context=f"file_id={file_id}",
                )
            else:
                # No entity/fy_end yet — skip conversion; link_file will trigger it later.
                converted_tree, new_cur, fx_meta = ex_for_store, src_cur, None

            if fx_meta is not None:
                ex_for_store = converted_tree
                oj["extracted"] = converted_tree
                oj["currency"] = new_cur
                oj["currency_source"] = "converted"
                hist = [
                    h
                    for h in (oj.get("extraction_currency_conversion_history") or [])
                    if isinstance(h, dict)
                ]
                hist.append(fx_meta)
                oj["extraction_currency_conversion_history"] = hist[-20:]
                # Scale the unmatched rows by the same rate so the unmatched panel stays in the
                # converted currency (otherwise re-attaching a row injects a wrong-currency value).
                _rate = fx_meta.get("rate")
                if isinstance(_rate, (int, float)) and _rate > 0:
                    _um, _um_n = scale_unmatched_amounts(oj.get("audit_financials_unmatched"), float(_rate))
                    if _um_n:
                        oj["audit_financials_unmatched"] = _um

    if k_fin and qualitative_stored:
        engagement = qualitative_stored.get("auditor_engagement")
        if isinstance(engagement, dict) and isinstance(engagement.get("summary"), str):
            oj["qualitative_audit_report"] = engagement["summary"]
    elif k_fin:
        oj.pop("qualitative_audit_report", None)

    # Deterministic guard + pipeline provenance (non-blocking: annotates confidence only).
    if k_fin:
        try:
            guard = run_extraction_guard(extracted if isinstance(extracted, dict) else {})
            oj["guard"] = guard.as_dict()
            oj["extraction_confidence"] = guard.confidence
        except Exception as e:
            logger.warning("extraction guard failed (non-fatal): %s", str(e)[:200])
        if image_meta:
            oj["extraction_pipeline"] = "image_text"
            oj["doc_type"] = image_meta.get("doc_type")
            oj["statement_pages"] = image_meta.get("statement_pages")
        else:
            oj.setdefault("extraction_pipeline", "legacy")

    meta.ocr_json = oj
    meta.auditor_opinion = opinion_stored if k_fin else None
    meta.audit_qualitative = qualitative_stored if k_fin else None
    file_row.status = FileUploadStatus.PROCESSED

    if k_fin and qualitative_stored:
        _eng = qualitative_stored.get("auditor_engagement") or {}
        _firm = _eng.get("auditor_firm") if isinstance(_eng, dict) else None
        _tier_label = _eng.get("auditor_tier_label") if isinstance(_eng, dict) else None
        await _sync_auditor_to_pcm(db, file_row, _firm, _tier_label)

    if k_fin and isinstance(ex_for_store, dict) and "parse_error" not in ex_for_store:
        await sync_financial_data_from_audit_extraction(
            db,
            file_row=file_row,
            extracted=ex_for_store,
            currency=oj.get("currency") if isinstance(oj.get("currency"), str) else None,
            is_audit_financials=True,
        )

    from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR

    await CompanyAuditRecorder(db).log_file(
        file_row,
        action=f'Extraction completed ({kind}) for file "{filename}"',
        meta={"event": "extraction.completed", "kind": kind, "provider": provider},
        actor_email=SYSTEM_ACTOR,
    )

    await db.commit()
    logger.info("audit_document extraction completed file_id=%s kind=%s", file_id, kind)
