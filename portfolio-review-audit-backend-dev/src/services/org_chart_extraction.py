"""
Org-chart file → LLM extraction (AWS Bedrock in deployment, OpenAI when configured locally).

Replaces entities for the portfolio company with structured output from the model.

Shared helpers (prefixed with no underscore) are also used by the batch upload flow
(src/services/org_chart_batch_upload.py).  _persist_entities and run_org_chart_extraction
remain unchanged for the existing single-company flow.
"""
from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings
from src.db.models import Entity, File, FileOCRMetadata, FileUploadStatus, OrgChartUploadRecord, PortfolioCompany
from src.schema.portfolio import EntityReviewStatus
from src.llm.client import completion_with_document
from src.llm.config import effective_document_extraction_provider, validate_llm_config
from src.llm.prompts import ORG_CHART_SYSTEM_PROMPT, ORG_CHART_USER_INSTRUCTION
from src.utils.s3 import download_storage_uri

logger = logging.getLogger(__name__)


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    # Extract the first ```...``` block from anywhere in the response (model often adds preamble prose)
    fence_match = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", s)
    if fence_match:
        return fence_match.group(1).strip()
    # No fence — strip any leading prose before the first [ or {
    bracket_match = re.search(r"[{\[]", s)
    if bracket_match:
        return s[bracket_match.start():].strip()
    return s


def _coerce_int_id(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except ValueError:
        return None


# Corporate-entity suffixes — any name containing one of these is kept as an entity.
_CORPORATE_SUFFIXES = re.compile(
    r"\b("
    r"ltd|limited|llc|llp|inc|incorporated|corp|corporation|co\.|company|"
    r"pte|pvt|plc|gmbh|ag|sa|sas|sarl|bv|nv|oy|ab|as|aps|sp\.?\s*z\.?\s*o\.?\s*o|"
    r"holdings?|holding|group|fund|trust|venture|partners?|capital|"
    r"enterprises?|industries|investments?|solutions?|services?|technologies?|"
    r"international|global|associates?|consulting|management|"
    r"sdn\.?\s*bhd|bhd|fze|fzco|wll|kscc|jsc|ojsc|cjsc|"
    r"foundation|charity|institute|authority|board"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_human_name(name: str) -> bool:
    """Return True when a name is likely an individual person rather than a legal entity.

    Heuristic: no recognisable corporate suffix AND the name is short (≤4 words),
    suggesting a personal name such as "John Smith" or "Maria José Santos".
    Falls back to False (keep the entity) when in doubt.
    """
    if _CORPORATE_SUFFIXES.search(name):
        return False
    # A comma in the name strongly indicates a location ("Haryana, India") or
    # a list — not a personal name.  Keep it as an entity.
    if "," in name:
        return False
    words = name.split()
    # Only flag short names with no corporate marker — longer strings are likely
    # fund/vehicle names that just happen to lack a standard suffix.
    return 1 < len(words) <= 4


_ENTITY_TYPE_MAP: dict[str, str] = {
    "intermediate holding": "Intermediate Holding",
    "associate": "Associate",
    "branch": "Branch",
    "ultimate holding": "Ultimate Holding",
    "other": "Other",
    "subsidiary": "Subsidiary",
    "holding": "Holding",
}


def _normalize_entity_type(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return _ENTITY_TYPE_MAP.get(s.lower())


def _normalize_org_chart_rows(arr: list[Any]) -> List[dict[str, Any]]:
    """New LLM shape: JSON array of {id, entity_name, geolocation, is_parent, children}."""
    out: List[dict[str, Any]] = []
    for row in arr:
        if not isinstance(row, dict):
            continue
        lid = _coerce_int_id(row.get("id"))
        if lid is None:
            continue
        name = str(row.get("entity_name") or row.get("name") or "").strip()
        if not name:
            continue
        if _looks_like_human_name(name):
            logger.debug("org_chart: skipping probable human name %r", name)
            continue
        raw_children = row.get("children")
        if raw_children is None:
            raw_children = []
        if not isinstance(raw_children, list):
            raw_children = []
        cids: list[int] = []
        for c in raw_children:
            ci = _coerce_int_id(c)
            if ci is not None:
                cids.append(ci)
        geo = str(row.get("geolocation", "") or "").strip()
        out.append(
            {
                "llm_id": lid,
                "name": name[:255],
                "geolocation": geo[:128] if geo else None,
                "entity_type": _normalize_entity_type(row.get("entity_type")),
                "is_parent": bool(row.get("is_parent", False)),
                "children_ids": cids,
            }
        )
    if not out:
        raise ValueError("No entities parsed from LLM output")
    return out


def _legacy_parent_name_rows(ent: list[Any]) -> List[dict[str, Any]]:
    """Legacy LLM shape: {entities: [{name, parent_name, ...}]}."""
    rows_in: List[dict[str, Any]] = []
    for row in ent:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        if _looks_like_human_name(name):
            logger.debug("org_chart: skipping probable human name %r (legacy path)", name)
            continue
        _g = row.get("geolocation")
        _geo = None
        if _g is not None and str(_g).strip():
            _geo = str(_g).strip()[:128]
        rows_in.append(
            {
                "name": name[:255],
                "geolocation": _geo,
                "entity_type": _normalize_entity_type(row.get("entity_type")),
                "parent_name": str(row.get("parent_name", "") or row.get("parent", "") or "").strip() or None,
                "is_parent": bool(row.get("is_parent", False)),
            }
        )
    if not rows_in:
        raise ValueError("No entities parsed from legacy LLM output")
    # Assign stable IDs by iteration order.  Track duplicates so a repeated name
    # gets its own unique ID rather than colliding with a prior entry — otherwise
    # _persist_entities silently drops one of them and the parent-child graph
    # uses the wrong surviving entity as the holding-company anchor.
    name_to_id: dict[str, int] = {}
    row_ids: list[int] = []
    for i, r in enumerate(rows_in, start=1):
        if r["name"] not in name_to_id:
            name_to_id[r["name"]] = i
        else:
            logger.warning("org_chart: duplicate entity name %r in legacy output; assigning unique id %s", r["name"], i)
        row_ids.append(i)
    out: List[dict[str, Any]] = []
    for row_idx, r in enumerate(rows_in):
        lid = row_ids[row_idx]
        pname = r.get("parent_name")
        children_ids = [row_ids[j] for j, c in enumerate(rows_in) if (c.get("parent_name") or "") == r["name"]]
        out.append(
            {
                "llm_id": lid,
                "name": r["name"],
                "geolocation": r.get("geolocation"),
                "entity_type": r.get("entity_type"),
                "is_parent": r.get("is_parent", False),
                "children_ids": children_ids,
            }
        )
    return out


def _parse_llm_entities(raw: str) -> List[dict[str, Any]]:
    s = _strip_json_fence(raw)
    if not s:
        raise ValueError("LLM returned an empty response; cannot parse org chart entities")
    data = json.loads(s)
    if isinstance(data, list):
        return _normalize_org_chart_rows(data)
    if isinstance(data, dict):
        ent = data.get("entities")
        if isinstance(ent, list) and ent and isinstance(ent[0], dict):
            first = ent[0]
            if "parent_name" in first and "children" not in first:
                return _legacy_parent_name_rows(ent)
        for key in ("data", "result", "items"):
            inner = data.get(key)
            if isinstance(inner, list):
                return _normalize_org_chart_rows(inner)
    raise ValueError(
        "LLM response must be a JSON array of entity objects (id, entity_name, geolocation, is_parent, children), "
        "or legacy {entities: [...]} with parent_name."
    )


async def _persist_entities(db: AsyncSession, portfolio_company_id: int, rows: List[dict[str, Any]]) -> None:
    """
    Persist hierarchy from normalized rows: each row has llm_id, name, geolocation, is_parent, children_ids.

    Parent/child links follow the directed graph implied by each node's children list.
    """
    await db.execute(
        update(Entity)
        .where(Entity.portfolio_company_id == portfolio_company_id)
        .values(parent_entity_id=None)
    )
    await db.execute(delete(Entity).where(Entity.portfolio_company_id == portfolio_company_id))
    # Flush so the FK ondelete="SET NULL" cascade fires and entity_id is null on affected files.
    await db.flush()
    # Mark files that just lost their entity as unacknowledged orphans.
    await db.execute(
        update(File)
        .where(File.portfolio_company_id == portfolio_company_id, File.entity_id.is_(None))
        .values(entity_detached_acknowledged=False)
    )

    by_llm: dict[int, dict[str, Any]] = {}
    for r in rows:
        lid = int(r["llm_id"])
        if lid in by_llm:
            logger.warning("org_chart: duplicate llm id %s; keeping last", lid)
        by_llm[lid] = r

    all_llm_ids = set(by_llm.keys())

    # Build parent_of only from deduplicated entries so that children of a
    # dropped duplicate are not incorrectly attributed to the surviving entity.
    parent_of: dict[int, int] = {}
    for r in by_llm.values():
        pid = int(r["llm_id"])
        for cid in r.get("children_ids") or []:
            ci = int(cid)
            if ci not in all_llm_ids:
                logger.warning("org_chart: child id %s not in entity list; skipping edge", ci)
                continue
            if ci in parent_of and parent_of[ci] != pid:
                logger.warning(
                    "org_chart: child %s has multiple parents (%s vs %s); keeping first",
                    ci,
                    parent_of[ci],
                    pid,
                )
            else:
                parent_of[ci] = pid

    child_ids = set(parent_of.keys())
    roots = [lid for lid in all_llm_ids if lid not in child_ids]
    if not roots:
        roots = [r["llm_id"] for r in rows if r.get("is_parent")]
        if not roots:
            roots = [min(all_llm_ids)]
        logger.warning("org_chart: no root from graph; using fallback roots=%s", roots)

    order: list[int] = []
    seen: set[int] = set()
    queue: deque[int] = deque(roots)
    while queue:
        nid = int(queue.popleft())
        if nid in seen:
            continue
        if nid not in by_llm:
            continue
        seen.add(nid)
        order.append(nid)
        for cid in by_llm[nid].get("children_ids") or []:
            ci = int(cid)
            if ci in by_llm:
                queue.append(ci)

    for lid in all_llm_ids:
        if lid not in seen:
            order.append(lid)

    llm_to_db: dict[int, int] = {}

    pc_row = await db.get(PortfolioCompany, portfolio_company_id)
    entity_review_cycle: Optional[str] = None
    if pc_row is not None and pc_row.review_cycle_id:
        rid = str(pc_row.review_cycle_id).strip()
        if rid:
            entity_review_cycle = rid[:64]

    for nid in order:
        r = by_llm[nid]
        plm = parent_of.get(nid)
        parent_db_id = llm_to_db.get(plm) if plm is not None else None
        e = Entity(
            portfolio_company_id=portfolio_company_id,
            name=r["name"],
            geolocation=r.get("geolocation"),
            entity_type=r.get("entity_type"),
            parent_entity_id=parent_db_id,
            is_parent=bool(r.get("is_parent", False)),
            review_cycle=entity_review_cycle,
            status=EntityReviewStatus.NOT_APPLICABLE.value,
            extra_data={},
        )
        db.add(e)
        await db.flush()
        llm_to_db[nid] = e.id


async def run_org_chart_extraction(db: AsyncSession, file_id: int) -> None:
    """
    Org-chart:
    - **LOCAL_DEV:** OpenAI only via ``completion_with_document`` (direct API).
    - **Deployment (Bedrock):** ``completion_with_document`` reads bytes from S3 then sends PDF to the model inline (no ``s3Location``).
    """
    file_row = (
        await db.execute(select(File).where(File.id == file_id))
    ).scalar_one_or_none()
    if not file_row or not file_row.storage_uri:
        raise ValueError("File not found or missing storage_uri")

    portfolio_company_id = file_row.portfolio_company_id
    provider = effective_document_extraction_provider(settings)
    if provider == "openai":
        validate_llm_config(settings, provider="openai")
    else:
        validate_llm_config(settings, provider="bedrock")

    logger.info(
        "org_chart LLM extraction file_id=%s company_id=%s provider=%s",
        file_id,
        portfolio_company_id,
        provider,
    )

    data = download_storage_uri(file_row.storage_uri)
    if not data:
        raise ValueError("Empty file in S3")
    raw = await completion_with_document(
        system_instruction=ORG_CHART_SYSTEM_PROMPT,
        user_instruction=ORG_CHART_USER_INSTRUCTION,
        file_bytes=data,
        filename=file_row.filename or "document.pdf",
        max_tokens=8192,
        http_timeout=float(settings.ORG_CHART_LLM_TIMEOUT),
    )

    rows = _parse_llm_entities(raw)
    await _persist_entities(db, portfolio_company_id, rows)

    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc:
        pc.org_chart_file_id = file_id

    meta = (
        await db.execute(select(FileOCRMetadata).where(FileOCRMetadata.file_id == file_id))
    ).scalar_one_or_none()
    if meta is None:
        meta = FileOCRMetadata(file_id=file_id, ocr_json={})
        db.add(meta)
        await db.flush()
    # Text is sent natively to the model (PDF via Bedrock/OpenAI); we do not store a separate OCR copy.
    meta.ocr_text = None
    oj = dict(meta.ocr_json or {})
    oj.update(
        {
            "status": "completed",
            "kind": "org_chart",
            "provider": provider,
            "entity_count": len(rows),
        }
    )
    meta.ocr_json = oj
    file_row.status = FileUploadStatus.PROCESSED

    from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR

    await CompanyAuditRecorder(db).log_file(
        file_row,
        action=f'Org chart extraction completed ({len(rows)} entities) for file "{file_row.filename}"',
        meta={"event": "extraction.completed", "kind": "org_chart", "entity_count": len(rows), "provider": provider},
        actor_email=SYSTEM_ACTOR,
    )
    await CompanyAuditRecorder(db).log_company(
        portfolio_company_id=portfolio_company_id,
        action=f'Org chart updated with {len(rows)} entities from file "{file_row.filename}"',
        meta={"event": "org_chart.updated", "file_id": file_id, "filename": file_row.filename, "entity_count": len(rows)},
        actor_email=SYSTEM_ACTOR,
    )

    await db.commit()
    await db.refresh(meta)
    logger.info(
        "org_chart extraction completed file_id=%s entities=%s",
        file_id,
        len(rows),
    )


# ---------------------------------------------------------------------------
# Shared helpers for the batch upload flow
# ---------------------------------------------------------------------------

def download_file_bytes(storage_uri: str) -> bytes:
    """Download bytes from a storage_uri (S3). Raises ValueError on empty."""
    data = download_storage_uri(storage_uri)
    if not data:
        raise ValueError(f"Empty file at {storage_uri}")
    return data


async def call_llm_org_chart(file_bytes: bytes, filename: str) -> str:
    """Call the LLM with the file bytes and return the raw string response."""
    provider = effective_document_extraction_provider(settings)
    if provider == "openai":
        validate_llm_config(settings, provider="openai")
    else:
        validate_llm_config(settings, provider="bedrock")
    return await completion_with_document(
        system_instruction=ORG_CHART_SYSTEM_PROMPT,
        user_instruction=ORG_CHART_USER_INSTRUCTION,
        file_bytes=file_bytes,
        filename=filename,
        max_tokens=8192,
        http_timeout=float(settings.ORG_CHART_LLM_TIMEOUT),
    )


def parse_org_chart_response(raw: str) -> List[dict[str, Any]]:
    """Parse raw LLM response into normalized entity rows."""
    return _parse_llm_entities(raw)


async def _auto_apply_if_no_existing(db: AsyncSession, record_id: int, portfolio_company_id: int) -> None:
    """Auto-apply an extracted org chart record when the company has no existing entities or org chart.

    Called immediately after successful extraction. Silently skips if the company already
    has entities or an org chart file — those cases require manual reconciliation.
    """
    from sqlalchemy import func
    from src.services.org_chart_reconciliation import apply_org_chart_record

    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc is None:
        return

    if pc.org_chart_file_id:
        logger.info(
            "auto-apply skipped record_id=%s: company %s already has org_chart_file_id",
            record_id, portfolio_company_id,
        )
        return
    entity_count = (
        await db.execute(
            select(func.count()).select_from(Entity).where(
                Entity.portfolio_company_id == portfolio_company_id
            )
        )
    ).scalar_one()

    if entity_count > 0:
        logger.info(
            "auto-apply skipped record_id=%s: company %s already has %d entities",
            record_id, portfolio_company_id, entity_count,
        )
        return

    logger.info(
        "auto-applying org chart record_id=%s for company %s (no existing org chart or entities)",
        record_id, portfolio_company_id,
    )
    await apply_org_chart_record(db, record_id=record_id, applied_by="system:auto_apply")
    logger.info("auto-apply complete record_id=%s company %s", record_id, portfolio_company_id)


async def run_org_chart_batch_record_extraction(db: AsyncSession, record_id: int) -> None:
    """Extract org chart for a single OrgChartUploadRecord and store the JSON result.

    After successful extraction, if the company has no existing org chart or entities the
    record is automatically applied (entities created, org_chart_file_id set) without
    requiring user reconciliation. If the company already has an org chart the record is
    left as reconciliation_status='pending' for the user to reconcile manually.
    """
    record = (
        await db.execute(select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        raise ValueError(f"OrgChartUploadRecord {record_id} not found")
    if not record.storage_uri:
        record.extraction_status = "failed"
        record.error_message = "No storage_uri on record"
        await db.commit()
        return

    # Capture portfolio_company_id before any flush/commit — db.commit() expires
    # all session objects, making attribute access on `record` unreliable afterwards.
    portfolio_company_id: int | None = record.portfolio_company_id

    logger.info(
        "extraction start record_id=%s company=%s file=%s",
        record_id, portfolio_company_id, record.file_name,
    )

    record.extraction_status = "processing"
    await db.flush()
    logger.info("extraction record_id=%s status=processing flushed", record_id)

    extraction_succeeded = False
    try:
        logger.info("extraction record_id=%s downloading from S3: %s", record_id, record.storage_uri)
        file_bytes = download_file_bytes(record.storage_uri)
        logger.info("extraction record_id=%s S3 download complete bytes=%s", record_id, len(file_bytes))

        logger.info("extraction record_id=%s calling LLM", record_id)
        raw = await call_llm_org_chart(file_bytes, record.file_name)
        logger.info("extraction record_id=%s LLM response length=%s", record_id, len(raw))

        rows = parse_org_chart_response(raw)
        logger.info("extraction record_id=%s parsed entities=%s", record_id, len(rows))

        record.extracted_org_chart = rows
        record.extraction_status = "completed"
        record.error_message = None
        record.reconciliation_status = "pending"
        extraction_succeeded = True
        logger.info(
            "batch org_chart extraction completed record_id=%s entities=%s",
            record_id,
            len(rows),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("batch org_chart extraction failed record_id=%s", record_id)
        record.extraction_status = "failed"
        record.error_message = str(exc)[:1024]
        record.reconciliation_status = None
        record.extracted_org_chart = None

    logger.info("extraction record_id=%s committing extraction result succeeded=%s", record_id, extraction_succeeded)
    await db.commit()
    logger.info("extraction record_id=%s commit done", record_id)

    # Auto-apply immediately if the company has no existing org chart or entities.
    # Use local variables — record is expired after db.commit().
    # Failures here are non-fatal — the record stays as 'pending' for manual apply.
    if extraction_succeeded and portfolio_company_id is not None:
        logger.info("extraction record_id=%s triggering auto-apply for company=%s", record_id, portfolio_company_id)
        try:
            await _auto_apply_if_no_existing(db, record_id, portfolio_company_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "auto-apply failed for record_id=%s; record stays pending for manual apply",
                record_id,
            )
    else:
        logger.info(
            "extraction record_id=%s skipping auto-apply: succeeded=%s company=%s",
            record_id, extraction_succeeded, portfolio_company_id,
        )
