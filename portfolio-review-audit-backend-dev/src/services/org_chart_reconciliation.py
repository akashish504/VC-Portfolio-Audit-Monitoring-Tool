"""
Org-chart reconciliation service.

Two public entry points:
  apply_org_chart_record      — direct apply for companies with no existing org chart.
  reconcile_org_chart_record  — user-guided reconciliation for companies that already
                                 have entities/an org chart.

Both are atomic: all DB mutations happen inside one transaction that is either
committed fully or rolled back on any validation / runtime error.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.file_filters import ORG_CHART_BATCH_FILE_TAG, ORG_CHART_FILE_TAG
from src.db.models import Entity, File, OrgChartUploadRecord, PortfolioCompany
from src.schema.portfolio import EntityReviewStatus

logger = logging.getLogger(__name__)

# ── normalisation & auto-matching ──────────────────────────────────────────────


def _norm(value: Any) -> str | None:
    """Normalise a name or geolocation for matching: strip whitespace, lowercase.
    Returns None if the result is empty so empty/null values never match."""
    if value is None:
        return None
    s = str(value).strip().lower()
    return s if s else None


def compute_auto_matches(
    existing_entities: list[dict[str, Any]],
    extracted_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare existing entities against extracted entities and return auto-match groups.

    Matching rules (in priority order):
      1. Exact normalised name match (preferred).
      2. Normalised geolocation match if neither side is empty/null.

    Ties / ambiguity:
      - Each existing entity maps to at most one extracted entity.
      - Each extracted entity maps to at most one existing entity.
      - When multiple candidates could match, the first in iteration order wins
        (deterministic; caller should ensure a stable input order).
      - Name matches always beat geo-only matches.

    Returns a dict with keys:
      auto_matched_entities   — list of match dicts
      unmatched_existing      — existing entity dicts not auto-matched
      unmatched_extracted     — extracted row dicts not auto-matched
    """
    matched_existing_ids: set[int] = set()
    matched_extracted_llm_ids: set[int] = set()
    matches: list[dict[str, Any]] = []

    # Two-pass: name matches first, then geo-only for remainders.
    for pass_kind in ("name", "geo"):
        for ex_ent in existing_entities:
            eid = ex_ent["id"]
            if eid in matched_existing_ids:
                continue
            ex_name = _norm(ex_ent.get("name"))
            ex_geo = _norm(ex_ent.get("geolocation"))

            for ext_row in extracted_rows:
                lid = int(ext_row["llm_id"])
                if lid in matched_extracted_llm_ids:
                    continue

                if pass_kind == "name":
                    ext_name = _norm(ext_row.get("name"))
                    if ex_name and ext_name and ex_name == ext_name:
                        reason = "name"
                    else:
                        continue
                else:  # geo
                    ext_geo = _norm(ext_row.get("geolocation"))
                    if ex_geo and ext_geo and ex_geo == ext_geo:
                        reason = "geolocation"
                    else:
                        continue

                matched_existing_ids.add(eid)
                matched_extracted_llm_ids.add(lid)
                matches.append(
                    {
                        "existing_entity_id": eid,
                        "extracted_temp_id": lid,
                        "match_reason": reason,
                        "existing_entity": ex_ent,
                        "extracted_entity": ext_row,
                    }
                )
                break  # move to next existing entity

    unmatched_existing = [e for e in existing_entities if e["id"] not in matched_existing_ids]
    unmatched_extracted = [r for r in extracted_rows if int(r["llm_id"]) not in matched_extracted_llm_ids]

    return {
        "auto_matched_entities": matches,
        "unmatched_existing_entities": unmatched_existing,
        "unmatched_extracted_entities": unmatched_extracted,
    }


# ── helpers ────────────────────────────────────────────────────────────────────


def _build_parent_map(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Return child_llm_id → parent_llm_id from the children_ids graph."""
    parent_of: dict[int, int] = {}
    for r in rows:
        pid = int(r["llm_id"])
        for cid in r.get("children_ids") or []:
            ci = int(cid)
            if ci not in parent_of:
                parent_of[ci] = pid
    return parent_of


def _topo_order(rows: list[dict[str, Any]], parent_of: dict[int, int]) -> list[int]:
    """BFS from roots so parents are inserted before children."""
    by_llm = {int(r["llm_id"]): r for r in rows}
    all_ids = set(by_llm.keys())
    child_ids = set(parent_of.keys())
    roots = [lid for lid in all_ids if lid not in child_ids]
    if not roots:
        # fallback: nodes marked is_parent, then first id
        roots = [r["llm_id"] for r in rows if r.get("is_parent")]
        if not roots:
            roots = [min(all_ids)]

    order: list[int] = []
    seen: set[int] = set()
    queue: deque[int] = deque(roots)
    while queue:
        nid = int(queue.popleft())
        if nid in seen or nid not in by_llm:
            continue
        seen.add(nid)
        order.append(nid)
        for cid in by_llm[nid].get("children_ids") or []:
            queue.append(int(cid))
    for lid in all_ids:
        if lid not in seen:
            order.append(lid)
    return order


def _detect_cycle(
    entity_id: int,
    proposed_parent_id: int,
    existing_parents: dict[int, int | None],
) -> bool:
    """Return True if making entity_id a child of proposed_parent_id creates a cycle."""
    if entity_id == proposed_parent_id:
        return True
    cur: int | None = proposed_parent_id
    steps = 0
    while cur is not None:
        steps += 1
        if steps > 5000:
            return True
        if cur == entity_id:
            return True
        cur = existing_parents.get(cur)
    return False


async def _get_company_review_cycle(db: AsyncSession, portfolio_company_id: int) -> Optional[str]:
    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc and pc.review_cycle_id:
        return str(pc.review_cycle_id).strip()[:64] or None
    return None


# ── pending-update query ────────────────────────────────────────────────────────


async def get_pending_update(
    db: AsyncSession,
    portfolio_company_id: int,
) -> Optional[dict[str, Any]]:
    """
    Return info about the latest completed OrgChartUploadRecord for the company
    whose reconciliation_status is pending (NULL or 'pending').

    Returns None when no pending update exists.
    """
    result = await db.execute(
        select(OrgChartUploadRecord)
        .where(
            OrgChartUploadRecord.portfolio_company_id == portfolio_company_id,
            OrgChartUploadRecord.extraction_status == "completed",
            OrgChartUploadRecord.extracted_org_chart.isnot(None),
            or_(
                OrgChartUploadRecord.reconciliation_status == "pending",
                OrgChartUploadRecord.reconciliation_status.is_(None),
            ),
        )
        .order_by(OrgChartUploadRecord.id.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        return None

    # Existing entities for the company
    existing_entities_q = await db.execute(
        select(Entity).where(Entity.portfolio_company_id == portfolio_company_id)
    )
    existing_entities = list(existing_entities_q.scalars())

    # File counts per entity
    file_counts_q = await db.execute(
        select(File.entity_id, File.id)
        .where(
            File.portfolio_company_id == portfolio_company_id,
            File.entity_id.isnot(None),
        )
    )
    file_counts: dict[int, int] = {}
    for entity_id, _ in file_counts_q:
        file_counts[entity_id] = file_counts.get(entity_id, 0) + 1

    # Company org_chart_file_id
    pc = await db.get(PortfolioCompany, portfolio_company_id)
    has_existing_org_chart = bool(
        (pc and pc.org_chart_file_id) or existing_entities
    )

    existing_entities_dicts = [
        {
            "id": e.id,
            "name": e.name,
            "geolocation": e.geolocation,
            "entity_type": e.entity_type,
            "parent_entity_id": e.parent_entity_id,
            "is_parent": e.is_parent,
            "status": e.status,
            "file_count": file_counts.get(e.id, 0),
        }
        for e in existing_entities
    ]

    extracted_rows = list(record.extracted_org_chart or [])
    auto_match_result = compute_auto_matches(existing_entities_dicts, extracted_rows)

    return {
        "record_id": record.id,
        "file_id": record.file_id,
        "file_name": record.file_name,
        "extracted_org_chart": extracted_rows,
        "requires_reconciliation": has_existing_org_chart,
        "existing_entities": existing_entities_dicts,
        "current_org_chart_file_id": pc.org_chart_file_id if pc else None,
        # auto-match additions
        "auto_matched_entities": auto_match_result["auto_matched_entities"],
        "unmatched_existing_entities": auto_match_result["unmatched_existing_entities"],
        "unmatched_extracted_entities": auto_match_result["unmatched_extracted_entities"],
    }


# ── direct apply (no existing org chart) ──────────────────────────────────────


async def apply_org_chart_record(
    db: AsyncSession,
    record_id: int,
    applied_by: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Directly persist extracted entities to Entity rows.
    Must only be called when the company has no existing org chart.
    Returns the list of created entity dicts.
    Raises ValueError on guard failures.
    """
    logger.info("apply_org_chart_record start record_id=%s applied_by=%s", record_id, applied_by)

    record = await _load_record_for_apply(db, record_id)
    portfolio_company_id = record.portfolio_company_id  # type: ignore[assignment]
    # Capture scalar values from `record` before any flush — _persist_new_entities
    # calls db.flush() per entity which expires all session objects, making
    # attribute access on `record` unreliable afterwards.
    file_id: int | None = record.file_id
    rows = list(record.extracted_org_chart or [])

    logger.info(
        "apply_org_chart_record record_id=%s company=%s file_id=%s rows=%s",
        record_id, portfolio_company_id, file_id, len(rows),
    )

    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc is None:
        raise ValueError(f"PortfolioCompany {portfolio_company_id} not found")

    if not rows:
        raise ValueError("Record has no extracted org chart data")

    review_cycle = await _get_company_review_cycle(db, portfolio_company_id)
    logger.info("apply_org_chart_record record_id=%s review_cycle=%s — creating entities", record_id, review_cycle)

    created = await _persist_new_entities(db, portfolio_company_id, rows, review_cycle)
    logger.info("apply_org_chart_record record_id=%s entities_created=%s", record_id, len(created))

    # After _persist_new_entities all session objects are expired.  Use explicit
    # UPDATE statements rather than mutating expired ORM objects — async SQLAlchemy
    # silently drops attribute sets on expired instances in some configurations.
    now = datetime.now(timezone.utc)

    if file_id:
        logger.info("apply_org_chart_record record_id=%s attaching file_id=%s", record_id, file_id)
        # Attach the file to the company and promote its tag from the batch-staging
        # value to the standard org_chart tag so it is visible in the company file view.
        file_row = await db.get(File, file_id)
        if file_row:
            logger.info(
                "apply_org_chart_record record_id=%s file_id=%s current_tags=%s",
                record_id, file_id, file_row.tags,
            )
            new_tags = [t for t in (file_row.tags or []) if t != ORG_CHART_BATCH_FILE_TAG]
            if ORG_CHART_FILE_TAG not in new_tags:
                new_tags.append(ORG_CHART_FILE_TAG)
            file_updates: dict[str, Any] = {
                "portfolio_company_id": portfolio_company_id,
                "tags": new_tags,
            }
            if review_cycle:
                file_updates["review_cycle_id"] = review_cycle
            logger.info(
                "apply_org_chart_record record_id=%s updating file_id=%s new_tags=%s",
                record_id, file_id, new_tags,
            )
            await db.execute(
                update(File).where(File.id == file_id).values(**file_updates)
            )
            logger.info("apply_org_chart_record record_id=%s file UPDATE executed", record_id)
        else:
            logger.warning(
                "apply_org_chart_record record_id=%s file_id=%s not found in DB — skipping file attachment",
                record_id, file_id,
            )

        logger.info("apply_org_chart_record record_id=%s updating company org_chart_file_id=%s", record_id, file_id)
        await db.execute(
            update(PortfolioCompany)
            .where(PortfolioCompany.id == portfolio_company_id)
            .values(org_chart_file_id=file_id)
        )
        logger.info("apply_org_chart_record record_id=%s company UPDATE executed", record_id)
    else:
        logger.warning("apply_org_chart_record record_id=%s file_id is None — org_chart_file_id will NOT be set", record_id)

    logger.info("apply_org_chart_record record_id=%s marking record as applied", record_id)
    await db.execute(
        update(OrgChartUploadRecord)
        .where(OrgChartUploadRecord.id == record_id)
        .values(
            reconciliation_status="applied",
            applied_at=now,
            applied_by=applied_by,
            applied_entity_ids=[e["id"] for e in created],
        )
    )

    from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR
    actor = applied_by or SYSTEM_ACTOR
    await CompanyAuditRecorder(db).log_company(
        portfolio_company_id=portfolio_company_id,
        action=f"Org chart applied from batch record #{record_id} ({len(created)} entities created)",
        meta={"event": "org_chart.applied", "record_id": record_id, "entity_count": len(created)},
        actor_email=actor,
    )

    logger.info("apply_org_chart_record record_id=%s committing transaction", record_id)
    await db.commit()
    logger.info("apply_org_chart_record record_id=%s DONE — committed successfully", record_id)
    return created


async def _load_record_for_apply(db: AsyncSession, record_id: int) -> OrgChartUploadRecord:
    record = (
        await db.execute(select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        raise ValueError(f"OrgChartUploadRecord {record_id} not found")
    if record.extraction_status != "completed":
        raise ValueError(f"Record {record_id} extraction not completed (status={record.extraction_status})")
    if not record.extracted_org_chart:
        raise ValueError(f"Record {record_id} has no extracted_org_chart")
    if record.reconciliation_status in ("applied", "dismissed"):
        raise ValueError(f"Record {record_id} already reconciled/dismissed")
    if record.portfolio_company_id is None:
        raise ValueError(f"Record {record_id} is not mapped to a PortfolioCompany")
    return record


async def _persist_new_entities(
    db: AsyncSession,
    portfolio_company_id: int,
    rows: list[dict[str, Any]],
    review_cycle: Optional[str],
) -> list[dict[str, Any]]:
    """Create Entity rows from normalized extracted rows. Returns dicts with created ids."""
    by_llm = {int(r["llm_id"]): r for r in rows}
    parent_of = _build_parent_map(rows)
    order = _topo_order(rows, parent_of)
    llm_to_db: dict[int, int] = {}
    created: list[dict[str, Any]] = []

    for nid in order:
        r = by_llm[nid]
        parent_llm = parent_of.get(nid)
        parent_db_id = llm_to_db.get(parent_llm) if parent_llm is not None else None
        e = Entity(
            portfolio_company_id=portfolio_company_id,
            name=r["name"],
            geolocation=r.get("geolocation"),
            entity_type=r.get("entity_type"),
            parent_entity_id=parent_db_id,
            is_parent=bool(r.get("is_parent", False)),
            review_cycle=review_cycle,
            status=EntityReviewStatus.NOT_APPLICABLE.value,
            extra_data={},
        )
        db.add(e)
        await db.flush()
        llm_to_db[nid] = e.id
        created.append({
            "id": e.id,
            "llm_id": nid,
            "name": e.name,
            "geolocation": e.geolocation,
            "entity_type": e.entity_type,
            "parent_entity_id": e.parent_entity_id,
            "is_parent": e.is_parent,
        })

    return created


# ── reconcile (company already has org chart) ──────────────────────────────────

"""
Reconciliation payload shape:

  entity_mappings: list of
    {
      existing_entity_id:  int | None   — existing DB entity; null for pure-creates
      extracted_temp_id:   int | None   — llm_id from extracted_org_chart
      action:              "match" | "create" | "keep" | "archive"
      final_name:          str | None
      final_geolocation:   str | None
      final_entity_type:   str | None
      final_is_parent:     bool | None
    }

  parent_links: list of
    {
      child_ref:   str   — "existing:{id}" | "extracted:{llm_id}"
      parent_ref:  str | None
    }

  file_moves: list of
    {
      file_id:                      int
      target_entity_ref:            str | None   — "existing:{id}" | "extracted:{llm_id}" | null
      acknowledge_detached:         bool   — true to allow explicit detach
    }
"""

ARCHIVE_STATUS = "Archive Entity"


async def reconcile_org_chart_record(
    db: AsyncSession,
    record_id: int,
    portfolio_company_id: int,
    entity_mappings: list[dict[str, Any]],
    parent_links: list[dict[str, Any]],
    file_moves: list[dict[str, Any]],
    applied_by: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Apply user reconciliation decisions in one atomic transaction.
    Returns list of all final entity dicts (created + patched).
    Raises ValueError on any validation failure (no partial DB state survives).
    """
    record = await _load_record_for_apply(db, record_id)
    if record.portfolio_company_id != portfolio_company_id:
        raise ValueError(
            f"Record {record_id} belongs to company {record.portfolio_company_id}, "
            f"not {portfolio_company_id}"
        )

    pc = await db.get(PortfolioCompany, portfolio_company_id)
    if pc is None:
        raise ValueError(f"PortfolioCompany {portfolio_company_id} not found")

    review_cycle = await _get_company_review_cycle(db, portfolio_company_id)
    extracted_rows = {int(r["llm_id"]): r for r in (record.extracted_org_chart or [])}

    # Load all existing entities for this company (to validate ownership)
    existing_q = await db.execute(
        select(Entity).where(Entity.portfolio_company_id == portfolio_company_id)
    )
    all_existing: dict[int, Entity] = {e.id: e for e in existing_q.scalars()}

    # ── Merge server-side auto-matches with user-submitted mappings ───────────
    # Recompute auto-matches authoritatively so the server is the source of truth.
    existing_dicts = [
        {
            "id": e.id,
            "name": e.name,
            "geolocation": e.geolocation,
            "entity_type": e.entity_type,
            "parent_entity_id": e.parent_entity_id,
            "is_parent": e.is_parent,
            "status": e.status,
            "file_count": 0,
        }
        for e in all_existing.values()
    ]
    auto_result = compute_auto_matches(existing_dicts, list(extracted_rows.values()))

    # Build a set of (existing_id, extracted_llm_id) pairs already in user mappings.
    user_existing_ids: set[int] = set()
    user_extracted_ids: set[int] = set()
    for m in entity_mappings:
        if m.get("existing_entity_id") is not None:
            user_existing_ids.add(int(m["existing_entity_id"]))
        if m.get("extracted_temp_id") is not None:
            user_extracted_ids.add(int(m["extracted_temp_id"]))

    # Inject auto-match mappings for pairs not overridden by the user.
    merged_entity_mappings = list(entity_mappings)
    for am in auto_result["auto_matched_entities"]:
        eid = am["existing_entity_id"]
        lid = am["extracted_temp_id"]
        if eid in user_existing_ids or lid in user_extracted_ids:
            # User provided an explicit mapping for one of these — respect it.
            continue
        ext_row = extracted_rows[lid]
        merged_entity_mappings.append(
            {
                "existing_entity_id": eid,
                "extracted_temp_id": lid,
                "action": "match",
                "final_name": ext_row.get("name"),
                "final_geolocation": ext_row.get("geolocation"),
                "final_entity_type": ext_row.get("entity_type"),
                "final_is_parent": ext_row.get("is_parent"),
                "_auto_matched": True,
            }
        )

    entity_mappings = merged_entity_mappings

    # ── Phase 1: validate + apply entity_mappings ─────────────────────────────
    # Maps "extracted:{llm_id}" and "existing:{entity_id}" refs to final DB entity id
    ref_to_db_id: dict[str, int] = {}

    # First pass: process existing entity actions (match, keep, archive) so we have
    # their DB ids available when resolving parent_links.
    created_entities: list[Entity] = []

    for mapping in entity_mappings:
        action = mapping.get("action")
        existing_id: int | None = mapping.get("existing_entity_id")
        temp_id: int | None = mapping.get("extracted_temp_id")

        if action not in ("match", "create", "keep", "archive"):
            raise ValueError(f"Unknown action {action!r} in entity_mappings")

        if action == "create":
            # Will be handled in second pass after all refs are built
            continue

        if existing_id is not None:
            if existing_id not in all_existing:
                raise ValueError(
                    f"Existing entity {existing_id} not found or does not belong to company {portfolio_company_id}"
                )
            ent = all_existing[existing_id]
            ref_to_db_id[f"existing:{existing_id}"] = existing_id

            if action == "archive":
                before_status = ent.status
                ent.status = ARCHIVE_STATUS
                from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR as _SYSTEM_ACTOR
                await CompanyAuditRecorder(db).log_company_field_changes(
                    portfolio_company_id=portfolio_company_id,
                    changes={"status": (before_status, ARCHIVE_STATUS)},
                    entity_type="entity",
                    entity_id=ent.id,
                    actor_email=applied_by or _SYSTEM_ACTOR,
                )

            elif action in ("match", "keep"):
                if action == "match":
                    if mapping.get("final_name"):
                        ent.name = str(mapping["final_name"])[:255]
                    if "final_geolocation" in mapping:
                        ent.geolocation = (mapping["final_geolocation"] or "")[:128] or None
                    if "final_entity_type" in mapping:
                        ent.entity_type = mapping.get("final_entity_type") or None
                    if "final_is_parent" in mapping:
                        ent.is_parent = bool(mapping["final_is_parent"])

                if temp_id is not None:
                    ref_to_db_id[f"extracted:{temp_id}"] = existing_id

    # Second pass: handle "create" actions
    for mapping in entity_mappings:
        action = mapping.get("action")
        if action != "create":
            continue
        temp_id = mapping.get("extracted_temp_id")
        extracted_row = extracted_rows.get(int(temp_id)) if temp_id is not None else None

        name = (
            str(mapping.get("final_name") or "").strip()
            or (extracted_row["name"] if extracted_row else None)
        )
        if not name:
            raise ValueError(f"Cannot create entity: no name in mapping {mapping!r}")

        geolocation = (
            mapping.get("final_geolocation")
            or (extracted_row.get("geolocation") if extracted_row else None)
        )
        entity_type = (
            mapping.get("final_entity_type")
            or (extracted_row.get("entity_type") if extracted_row else None)
        )
        is_parent = mapping.get("final_is_parent", False)
        if is_parent is None and extracted_row:
            is_parent = extracted_row.get("is_parent", False)

        new_ent = Entity(
            portfolio_company_id=portfolio_company_id,
            name=name[:255],
            geolocation=(geolocation or "")[:128] or None,
            entity_type=entity_type or None,
            parent_entity_id=None,  # set during parent_links pass
            is_parent=bool(is_parent),
            review_cycle=review_cycle,
            status=EntityReviewStatus.NOT_APPLICABLE.value,
            extra_data={},
        )
        db.add(new_ent)
        await db.flush()
        created_entities.append(new_ent)

        if mapping.get("existing_entity_id") is not None:
            ref_to_db_id[f"existing:{mapping['existing_entity_id']}"] = new_ent.id
        if temp_id is not None:
            ref_to_db_id[f"extracted:{temp_id}"] = new_ent.id

    # ── Phase 2: apply parent_links ───────────────────────────────────────────
    # Build current parent map (entity_id → parent_entity_id) for cycle detection
    # Include newly created entities (parent_entity_id=None for now)
    current_parents: dict[int, int | None] = {e_id: e.parent_entity_id for e_id, e in all_existing.items()}
    for e in created_entities:
        current_parents[e.id] = None

    for link in parent_links:
        child_ref: str = link.get("child_ref", "")
        parent_ref: str | None = link.get("parent_ref")

        child_db_id = _resolve_ref(child_ref, ref_to_db_id)
        if child_db_id is None:
            raise ValueError(f"Cannot resolve child_ref {child_ref!r}")

        parent_db_id: int | None = None
        if parent_ref:
            parent_db_id = _resolve_ref(parent_ref, ref_to_db_id)
            if parent_db_id is None:
                raise ValueError(f"Cannot resolve parent_ref {parent_ref!r}")

        # Cycle check against the final parent map being built
        if parent_db_id is not None and _detect_cycle(child_db_id, parent_db_id, current_parents):
            raise ValueError(
                f"parent_links would create a cycle: {child_ref} → {parent_ref}"
            )

        # Apply to entity
        ent = all_existing.get(child_db_id) or next(
            (e for e in created_entities if e.id == child_db_id), None
        )
        if ent is None:
            raise ValueError(f"Entity {child_db_id} not found for parent_link")
        ent.parent_entity_id = parent_db_id
        current_parents[child_db_id] = parent_db_id

    # ── Phase 3: file_moves ───────────────────────────────────────────────────
    # Pre-load all files for this company
    files_q = await db.execute(
        select(File).where(File.portfolio_company_id == portfolio_company_id)
    )
    company_files: dict[int, File] = {f.id: f for f in files_q.scalars()}

    for move in file_moves:
        file_id: int = move["file_id"]
        file_row = company_files.get(file_id)
        if file_row is None:
            raise ValueError(
                f"File {file_id} not found or does not belong to company {portfolio_company_id}"
            )

        target_ref: str | None = move.get("target_entity_ref")
        ack_detached: bool = bool(move.get("acknowledge_detached", False))

        if target_ref:
            target_entity_id = _resolve_ref(target_ref, ref_to_db_id)
            if target_entity_id is None:
                raise ValueError(f"Cannot resolve target_entity_ref {target_ref!r}")
            file_row.entity_id = target_entity_id
            file_row.entity_detached_acknowledged = False
        else:
            # Explicit detach
            file_row.entity_id = None
            file_row.entity_detached_acknowledged = ack_detached

    # ── Phase 4: update company org_chart_file_id and file row ───────────────
    if record.file_id:
        pc.org_chart_file_id = record.file_id
        batch_file = await db.get(File, record.file_id)
        if batch_file:
            batch_file.portfolio_company_id = portfolio_company_id
            if review_cycle:
                batch_file.review_cycle_id = review_cycle
            # Promote from batch-staging tag to the standard org_chart tag so the
            # file is visible in the company file view (list_files excludes org_chart_batch).
            tags = [t for t in (batch_file.tags or []) if t != ORG_CHART_BATCH_FILE_TAG]
            if ORG_CHART_FILE_TAG not in tags:
                tags.append(ORG_CHART_FILE_TAG)
            batch_file.tags = tags

    # ── Phase 5: mark record reconciled ──────────────────────────────────────
    now = datetime.now(timezone.utc)
    all_final_ids = [e.id for e in all_existing.values()] + [e.id for e in created_entities]
    record.reconciliation_status = "applied"
    record.applied_at = now
    record.applied_by = applied_by
    record.applied_entity_ids = all_final_ids
    record.reconciliation_payload = {
        "entity_mappings": entity_mappings,
        "parent_links": parent_links,
        "file_moves": file_moves,
        "auto_matched_entities": auto_result["auto_matched_entities"],
    }

    await db.flush()

    from src.services.company_audit_recorder import CompanyAuditRecorder, SYSTEM_ACTOR
    actor = applied_by or SYSTEM_ACTOR
    await CompanyAuditRecorder(db).log_company(
        portfolio_company_id=portfolio_company_id,
        action=f"Org chart reconciled from batch record #{record_id} ({len(created_entities)} created, {sum(1 for m in entity_mappings if m.get('action') == 'archive')} archived)",
        meta={
            "event": "org_chart.reconciled",
            "record_id": record_id,
            "created": len(created_entities),
            "archived": sum(1 for m in entity_mappings if m.get("action") == "archive"),
        },
        actor_email=actor,
    )

    await db.commit()

    # Reload company to get the committed org_chart_file_id (may have changed above)
    refreshed_pc = await db.get(PortfolioCompany, portfolio_company_id)
    new_org_chart_file_id: int | None = refreshed_pc.org_chart_file_id if refreshed_pc else None

    # Return all final entities for this company
    final_q = await db.execute(
        select(Entity).where(Entity.portfolio_company_id == portfolio_company_id)
    )
    entities = [
        {
            "id": e.id,
            "name": e.name,
            "geolocation": e.geolocation,
            "entity_type": e.entity_type,
            "parent_entity_id": e.parent_entity_id,
            "is_parent": e.is_parent,
            "status": e.status,
        }
        for e in final_q.scalars()
    ]
    return {"entities": entities, "new_org_chart_file_id": new_org_chart_file_id}


async def dismiss_org_chart_record(
    db: AsyncSession,
    record_id: int,
    applied_by: Optional[str] = None,
) -> None:
    """Mark a pending record as dismissed so the dialog does not show again."""
    record = (
        await db.execute(select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        raise ValueError(f"OrgChartUploadRecord {record_id} not found")
    if record.reconciliation_status in ("applied",):
        raise ValueError(f"Record {record_id} already applied")
    record.reconciliation_status = "dismissed"
    record.applied_at = datetime.now(timezone.utc)
    record.applied_by = applied_by
    await db.commit()


# ── ref resolution ──────────────────────────────────────────────────────────────


def _resolve_ref(ref: str, ref_to_db_id: dict[str, int]) -> int | None:
    """Resolve "existing:{id}" or "extracted:{llm_id}" to a DB entity id."""
    if not ref:
        return None
    if ref in ref_to_db_id:
        return ref_to_db_id[ref]
    # Try direct numeric fallback for existing:{id}
    if ref.startswith("existing:"):
        try:
            return int(ref.split(":", 1)[1])
        except (ValueError, IndexError):
            pass
    return None
