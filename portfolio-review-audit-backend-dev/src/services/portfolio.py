from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import Integer, cast, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from src.db.file_filters import exclude_org_chart_uploads_clause
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Entity,
    File,
    FinancialDataSnowflake,
    FinancialMetricReconciliation,
    ManualReconciliationQuery,
    PortfolioCompany,
    ReviewCycle,
)
from src.schema.portfolio import (
    ENTITY_IN_REVIEW_TRACKER_STATUSES,
    EntityCreate,
    EntityReviewStatus,
    EntityPatch,
    FileCreate,
    FilePatch,
    FinancialMetricReconciliationPatch,
    FinancialMetricReconciliationRead,
    ManualReconciliationQueryCreate,
    ManualReconciliationQueryPatch,
    PortfolioCompanyBulkUpsertRequest,
    PortfolioCompanyCreate,
    PortfolioCompanyPatch,
    PortfolioCompanyRead,
)
from src.services.financial_data_extraction_sync import (
    push_file_breakdown_to_reconciliation,
    resolve_financial_review_cycle,
    sync_financial_data_for_file_id,
)
from src.services.financial_reconciliation import (
    RECON_METRIC_KEYS,
    fetch_canonical_snowflake_map,
    fetch_usd_to_inr_rate,
    reconciliation_row_can_set_enable_true,
    resolve_mis_for_recon_row,
    thresholds_for_company,
)
from src.services.company_audit_recorder import (
    CompanyAuditRecorder,
    FINANCIAL_METRIC_LABELS,
    format_audit_value,
    is_unassigned_portfolio_company,
    metric_values_differ,
)
from src.services.audit_log_context import is_review_cycle_adjustments_context
from src.services.company_audit_context import get_audit_actor_email
from src.services.manual_edit_marker import (
    ACTION_AMOUNT_EDIT,
    ACTION_FIELD_EDIT,
    ACTION_METRIC_EDIT,
    build_marker,
    set_marker,
)
from src.services.fx_service import FxConversionUnavailable, fetch_historical_rate
from src.services.settings_audit_recorder import (
    ReviewCycleAuditRecorder,
    build_cycle_field_action,
)
from src.services.fy_end import (
    apply_fy_end_fields,
    normalize_fy_end,
    fy_end_from_legacy_date,
    fy_end_last_day,
    months_for_review_cycle_id,
    months_for_review_cycle_name,
)

logger = logging.getLogger(__name__)

_FINANCIAL_METRIC_KEYS = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")
# Reconciliation review-state columns whose manual edits get an "edited manually" marker.
_RECON_FIELD_EDIT_COLUMNS = ("status", "variance_category", "company_response", "reviewer_remarks")


class PortfolioService:
    @staticmethod
    def _snowflake_payload(row: FinancialDataSnowflake) -> dict[str, Any]:
        p = row.payload
        return dict(p) if isinstance(p, dict) else {}

    @staticmethod
    def _snowflake_entity_id(row: FinancialDataSnowflake) -> Optional[int]:
        eid = getattr(row, "entity_id", None)
        if eid is not None:
            try:
                return int(eid)
            except (TypeError, ValueError):
                pass
        raw = PortfolioService._snowflake_payload(row).get("entity_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _snowflake_metric_value(row: FinancialDataSnowflake, metric: str) -> Any:
        v = getattr(row, metric, None)
        if v is not None:
            return v
        return PortfolioService._snowflake_payload(row).get(metric)

    # ---- PortfolioCompany ----
    @staticmethod
    async def list_portfolio_companies(
        db: AsyncSession,
        *,
        q: Optional[str],
        review_cycle_id: Optional[str] = None,
        review_stage: Optional[str] = None,
        has_discrepancy_type: Optional[str] = None,
        has_discrepancy_category: Optional[str] = None,
        limit: int,
        offset: int,
    ) -> Tuple[list[PortfolioCompany], int]:
        stmt = select(PortfolioCompany)
        count_stmt = select(func.count(PortfolioCompany.id))
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
            count_stmt = count_stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
        if review_stage:
            # Case/whitespace tolerant (CSV and UIs vary; deployment data must still match).
            rs = review_stage.strip()
            stage_norm = func.lower(func.trim(PortfolioCompany.review_stage))
            stmt = stmt.where(stage_norm == rs.lower())
            count_stmt = count_stmt.where(stage_norm == rs.lower())
        if q:
            like = f"%{q.strip()}%"
            text_match = or_(
                PortfolioCompany.company_id.ilike(like),
                PortfolioCompany.name.ilike(like),
                PortfolioCompany.fund.ilike(like),
                PortfolioCompany.investment_lead.ilike(like),
                PortfolioCompany.auditor.ilike(like),
            )
            stmt = stmt.where(text_match)
            count_stmt = count_stmt.where(text_match)
        if has_discrepancy_type and has_discrepancy_type.strip():
            type_norm = has_discrepancy_type.strip().lower()
            disc_match = exists(
                select(1).where(
                    FinancialMetricReconciliation.portfolio_company_id == PortfolioCompany.id,
                    func.lower(func.trim(FinancialMetricReconciliation.metric_key)) == type_norm,
                )
            )
            stmt = stmt.where(disc_match)
            count_stmt = count_stmt.where(disc_match)
        elif has_discrepancy_category and has_discrepancy_category.strip():
            cat_norm = has_discrepancy_category.strip().lower()
            disc_match = exists(
                select(1).where(
                    FinancialMetricReconciliation.portfolio_company_id == PortfolioCompany.id,
                    func.lower(func.trim(FinancialMetricReconciliation.category)) == cat_norm,
                )
            )
            stmt = stmt.where(disc_match)
            count_stmt = count_stmt.where(disc_match)
        total = (await db.execute(count_stmt)).scalar_one()
        items = (
            (await db.execute(stmt.order_by(PortfolioCompany.id).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return items, total

    _DEFAULT_IN_REVIEW_STATUS = "Not applicable"

    @staticmethod
    async def list_in_review_tracker_rows(
        db: AsyncSession,
        *,
        q: Optional[str],
        review_cycle_id: Optional[str] = None,
        entity_status: Optional[str] = None,
        limit: int,
        offset: int,
    ) -> Tuple[list[dict[str, Any]], int]:
        from collections import defaultdict

        # Audit state lives on entities now. A company belongs in the In Review Tracker
        # when it has at least one entity in one of the tracker statuses (the active-review
        # set plus "Financials to be received"). When an explicit entity_status filter is
        # supplied, gate on companies having an entity in exactly that status instead.
        if entity_status:
            gate = exists(
                select(1).where(
                    Entity.portfolio_company_id == PortfolioCompany.id,
                    func.trim(Entity.status) == entity_status.strip(),
                )
            )
        else:
            gate = exists(
                select(1).where(
                    Entity.portfolio_company_id == PortfolioCompany.id,
                    Entity.status.in_(ENTITY_IN_REVIEW_TRACKER_STATUSES),
                )
            )
        stmt = select(PortfolioCompany).where(gate)
        stmt = stmt.where(~PortfolioCompany.company_id.like("sys-unassigned-%"))
        if review_cycle_id:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    PortfolioCompany.company_id.ilike(like),
                    PortfolioCompany.name.ilike(like),
                )
            )
        companies = (
            await db.execute(stmt.order_by(PortfolioCompany.name, PortfolioCompany.id))
        ).scalars().all()
        if not companies:
            return [], 0

        company_ids = [c.id for c in companies]
        # Source entities (and their status) directly from the entities table so the
        # status column mirrors the Audit Tracker. Only entities in a tracker status
        # are listed; any other entity (Scoped Out, Not applicable, Approved, etc.)
        # is excluded.
        entity_stmt = (
            select(
                Entity.portfolio_company_id,
                Entity.id,
                Entity.name,
                Entity.status,
            )
            .where(
                Entity.portfolio_company_id.in_(company_ids),
                Entity.status.in_(ENTITY_IN_REVIEW_TRACKER_STATUSES),
            )
            .order_by(Entity.name, Entity.id)
        )
        entity_by_company: dict[int, list[tuple[int, str, Optional[str]]]] = defaultdict(list)
        seen_entity_keys: set[tuple[int, int]] = set()
        for pc_id, eid, ename, estatus in (await db.execute(entity_stmt)).all():
            key = (int(pc_id), int(eid))
            if key in seen_entity_keys:
                continue
            seen_entity_keys.add(key)
            entity_by_company[int(pc_id)].append((int(eid), str(ename), estatus))

        rows: list[dict[str, Any]] = []
        for pc in companies:
            entities = entity_by_company.get(pc.id, [])
            base = {
                "portfolio_company_id": pc.id,
                "company_name": pc.name,
                "review_cycle_id": pc.review_cycle_id,
                "contact_name": pc.contact_name,
            }
            if entities:
                for eid, ename, estatus in entities:
                    ent_status = (estatus or "").strip() or None
                    if entity_status and ent_status != entity_status:
                        continue
                    rows.append(
                        {
                            **base,
                            "entity_id": eid,
                            "entity_name": ename,
                            "entity_status": ent_status,
                        }
                    )
            else:
                if entity_status:
                    continue
                rows.append(
                    {
                        **base,
                        "entity_id": None,
                        "entity_name": None,
                        "entity_status": None,
                    }
                )

        total = len(rows)
        page = rows[offset : offset + limit]
        return page, total

    @staticmethod
    async def list_cycle_entities(
        db: AsyncSession,
        *,
        q: Optional[str],
        review_cycle_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int,
        offset: int,
    ) -> Tuple[list[dict[str, Any]], int]:
        """Audit Tracker rows: every entity in the review cycle, with company context.

        Companies that have at least one entity produce one row per entity (entity
        fields populated, has_entities=True).  Companies with no entities at all
        produce exactly one placeholder row (entity fields None, has_entities=False,
        entity_status="Upload org chart / create entities") so the user can discover
        the company and navigate to the company view to create entities.

        The ``status`` filter only applies to real entity rows; placeholder rows are
        always included unless they are excluded by the search term ``q``.
        """
        _PLACEHOLDER_STATUS = "Upload org chart / create entities"

        # ── 1. company-level base query (no entity join yet) ─────────────────
        pc_stmt = select(PortfolioCompany).where(
            ~PortfolioCompany.company_id.like("sys-unassigned-%")
        )
        if review_cycle_id:
            pc_stmt = pc_stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id)
        if q:
            like = f"%{q.strip()}%"
            pc_stmt = pc_stmt.where(
                or_(
                    PortfolioCompany.company_id.ilike(like),
                    PortfolioCompany.name.ilike(like),
                )
            )

        all_companies: list[PortfolioCompany] = (
            await db.execute(pc_stmt.order_by(PortfolioCompany.name, PortfolioCompany.id))
        ).scalars().all()

        if not all_companies:
            return [], 0

        company_ids = [pc.id for pc in all_companies]

        # ── 2. fetch all entities for these companies in one query ───────────
        # No entity-name filter here — companies already narrowed by q above, so we
        # want ALL entities for matching companies (not just those whose name matches).
        ent_q_stmt = select(Entity).where(Entity.portfolio_company_id.in_(company_ids))
        all_entities: list[Entity] = (
            await db.execute(ent_q_stmt.order_by(Entity.name, Entity.id))
        ).scalars().all()

        # group entities by company id
        from collections import defaultdict
        entities_by_pc: dict[int, list[Entity]] = defaultdict(list)
        for ent in all_entities:
            entities_by_pc[ent.portfolio_company_id].append(ent)

        # ── 3. build rows ────────────────────────────────────────────────────
        all_rows: list[dict[str, Any]] = []
        for pc in all_companies:
            pc_base = PortfolioCompanyRead.model_validate(pc).model_dump()
            has_org_chart = pc.org_chart_file_id is not None
            entities = entities_by_pc.get(pc.id, [])

            if not entities:
                # placeholder row — skip if a status filter is active (placeholder
                # has no real status to match against)
                if status:
                    continue
                all_rows.append({
                    **pc_base,
                    "entity_id": None,
                    "entity_name": None,
                    "entity_status": _PLACEHOLDER_STATUS,
                    "entity_type": None,
                    "parent_entity_id": None,
                    "entity_is_parent": False,
                    "entity_review_cycle": None,
                    "has_entities": False,
                    "has_org_chart": has_org_chart,
                    "entity_comments": None,
                    "entity_one_desk_email_status": None,
                    "entity_geolocation": None,
                })
            else:
                for ent in entities:
                    if status and (ent.status or "").strip() != status.strip():
                        continue
                    all_rows.append({
                        **pc_base,
                        "entity_id": ent.id,
                        "entity_name": ent.name,
                        "entity_status": ent.status,
                        "entity_type": ent.entity_type,
                        "parent_entity_id": ent.parent_entity_id,
                        "entity_is_parent": bool(ent.is_parent),
                        "entity_review_cycle": ent.review_cycle,
                        "has_entities": True,
                        "has_org_chart": has_org_chart,
                        "entity_comments": ent.comments,
                        "entity_one_desk_email_status": ent.one_desk_email_status,
                        "entity_geolocation": ent.geolocation,
                    })

        total = len(all_rows)
        items = all_rows[offset: offset + limit]
        return items, total

    @staticmethod
    async def create_portfolio_company(
        db: AsyncSession, payload: PortfolioCompanyCreate
    ):
        obj = PortfolioCompany(**payload.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        if not is_unassigned_portfolio_company(obj) and not is_review_cycle_adjustments_context():
            await CompanyAuditRecorder(db).log_company(
                portfolio_company_id=obj.id,
                action=f'Portfolio company "{obj.name}" created',
                meta={"event": "portfolio_company.created", "company_id": obj.company_id},
                company=obj,
            )
        return obj

    @staticmethod
    def _placeholder_company_id_for_cycle(review_cycle_id: str) -> str:
        """Stable synthetic ``company_id`` for uploads with no company selected (≤64 chars, unique per cycle)."""
        h = hashlib.sha256(review_cycle_id.strip().encode()).hexdigest()[:16]
        return f"sys-unassigned-{h}"

    @staticmethod
    async def get_or_create_unassigned_portfolio_for_cycle(
        db: AsyncSession, review_cycle_id: str
    ) -> PortfolioCompany:
        """
        When an audit file is uploaded with a review cycle but no portfolio company, attach the file
        to a per-cycle placeholder row so ``files.portfolio_company_id`` stays non-null.
        """
        cid = PortfolioService._placeholder_company_id_for_cycle(review_cycle_id)
        rc = review_cycle_id.strip()
        stmt = select(PortfolioCompany).where(
            PortfolioCompany.company_id == cid,
            PortfolioCompany.review_cycle_id == (rc[:128] if rc else None),
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing
        rc = review_cycle_id.strip()
        create = PortfolioCompanyCreate(
            company_id=cid,
            name="Unassigned (audit files)",
            review_cycle_id=rc[:128] if rc else None,
        )
        return await PortfolioService.create_portfolio_company(db, create)

    @staticmethod
    async def bulk_upsert_portfolio_companies(
        db: AsyncSession,
        payload: PortfolioCompanyBulkUpsertRequest,
    ) -> list[PortfolioCompany]:
        """
        Upsert order per row:

        1. If ``company_id`` and ``review_cycle_id`` are set, find by that pair and update or create.
        2. If ``company_id`` is set without ``review_cycle_id``, find the latest row for that ``company_id``.
        3. Else if ``review_cycle_id`` and ``name`` are set, find by **same name** (case-insensitive,
           trimmed) **and** same ``review_cycle_id`` and update the first match — avoids duplicate
           rows from CSV uploads that omit ``company_id``.
        4. Otherwise insert with a synthetic ``company_id``.
        """
        out: list[PortfolioCompany] = []
        cycle_ctx = is_review_cycle_adjustments_context()
        created_count = 0
        updated_count = 0
        cycle_ids: set[str] = set()

        for item in payload.items:
            cid = (item.company_id or "").strip()
            rcid = (item.review_cycle_id or "").strip() if item.review_cycle_id else ""
            existing: Optional[PortfolioCompany] = None

            if cid:
                stmt = select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
                if rcid:
                    stmt = stmt.where(PortfolioCompany.review_cycle_id == rcid)
                else:
                    stmt = stmt.order_by(PortfolioCompany.id.desc())
                existing = (await db.execute(stmt.limit(1))).scalar_one_or_none()

            if existing is None:
                rcid = (item.review_cycle_id or "").strip() if item.review_cycle_id else ""
                name_norm = (item.name or "").strip()
                if rcid and name_norm:
                    stmt = (
                        select(PortfolioCompany)
                        .where(
                            PortfolioCompany.review_cycle_id == rcid,
                            func.lower(func.trim(PortfolioCompany.name)) == name_norm.lower(),
                        )
                        .order_by(PortfolioCompany.id)
                        .limit(1)
                    )
                    existing = (await db.execute(stmt)).scalar_one_or_none()

            if existing:
                dump = item.model_dump(exclude_unset=True)
                track_fields = list(dict.fromkeys([*dump.keys(), "name"]))
                before = CompanyAuditRecorder.snapshot_obj(existing, track_fields)
                existing.name = item.name
                for k, v in dump.items():
                    if k in ("company_id", "name"):
                        continue
                    setattr(existing, k, v)
                await db.flush()
                await db.refresh(existing)
                if cycle_ctx:
                    updated_count += 1
                    if existing.review_cycle_id:
                        cycle_ids.add(str(existing.review_cycle_id))
                else:
                    after = CompanyAuditRecorder.snapshot_obj(existing, track_fields)
                    changes = CompanyAuditRecorder.diff_snapshots(before, after)
                    await CompanyAuditRecorder(db).log_company_field_changes(
                        portfolio_company_id=existing.id,
                        changes=changes,
                        company=existing,
                    )
                out.append(existing)
            elif cid:
                dump = item.model_dump(exclude_unset=True)
                dump["company_id"] = cid
                dump["name"] = item.name
                create = PortfolioCompanyCreate(**dump)
                row = await PortfolioService.create_portfolio_company(db, create)
                out.append(row)
                if cycle_ctx:
                    created_count += 1
                    if row.review_cycle_id:
                        cycle_ids.add(str(row.review_cycle_id))
            else:
                synthetic = f"bulk-{uuid.uuid4().hex[:16]}"
                dump = item.model_dump(exclude_unset=True)
                dump["company_id"] = synthetic
                dump["name"] = item.name
                create = PortfolioCompanyCreate(**dump)
                row = await PortfolioService.create_portfolio_company(db, create)
                out.append(row)
                if cycle_ctx:
                    created_count += 1
                    if row.review_cycle_id:
                        cycle_ids.add(str(row.review_cycle_id))

        if cycle_ctx and (created_count or updated_count):
            cycle_id = next(iter(cycle_ids), None)
            if len(cycle_ids) == 1:
                cycle_label = cycle_id
            elif cycle_ids:
                cycle_label = ", ".join(sorted(cycle_ids)[:3])
                if len(cycle_ids) > 3:
                    cycle_label += f" (+{len(cycle_ids) - 3} more)"
            else:
                cycle_label = "—"
            total = created_count + updated_count
            action = (
                f"CSV import: {total} companies processed for review cycle "
                f'"{cycle_label}" ({created_count} created, {updated_count} updated)'
            )
            await ReviewCycleAuditRecorder(db).log(
                action=action,
                review_cycle_id=cycle_id if len(cycle_ids) == 1 else None,
                meta={
                    "event": "cycle.csv_import",
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "review_cycle_ids": sorted(cycle_ids),
                },
                search_text=f"{action} {cycle_label}",
            )
        return out

    @staticmethod
    async def get_portfolio_company(db: AsyncSession, portfolio_company_id: int):
        obj = await db.get(PortfolioCompany, portfolio_company_id)
        if not obj:
            return None
        return obj

    @staticmethod
    async def get_portfolio_company_by_company_id(
        db: AsyncSession,
        company_id: str,
        *,
        review_cycle_id: Optional[str] = None,
    ) -> Optional[PortfolioCompany]:
        cid = (company_id or "").strip()
        if not cid:
            return None
        stmt = select(PortfolioCompany).where(PortfolioCompany.company_id == cid)
        rc = (review_cycle_id or "").strip() if review_cycle_id else ""
        if rc:
            stmt = stmt.where(PortfolioCompany.review_cycle_id == rc).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

        rows = (
            await db.execute(
                select(PortfolioCompany, ReviewCycle.starts_at)
                .outerjoin(ReviewCycle, ReviewCycle.id == PortfolioCompany.review_cycle_id)
                .where(PortfolioCompany.company_id == cid)
                .order_by(ReviewCycle.starts_at.desc().nulls_last(), PortfolioCompany.id.desc())
                .limit(1)
            )
        ).first()
        if not rows:
            return None
        return rows[0]

    @staticmethod
    async def update_company_fy_end(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        fy_end: str,
    ) -> PortfolioCompany:
        obj = await PortfolioService.get_portfolio_company(db, portfolio_company_id)
        if not obj:
            raise ValueError("Portfolio company not found")
        normalized, legacy_date = apply_fy_end_fields(fy_end=fy_end)
        before = CompanyAuditRecorder.snapshot_obj(obj, ["fy_end", "fy_end_date"])
        obj.fy_end = normalized
        obj.fy_end_date = legacy_date
        await db.flush()
        after = CompanyAuditRecorder.snapshot_obj(obj, ["fy_end", "fy_end_date"])
        changes = CompanyAuditRecorder.diff_snapshots(before, after)
        if changes:
            await CompanyAuditRecorder(db).log_company_field_changes(
                portfolio_company_id=portfolio_company_id,
                changes=changes,
                company=obj,
            )
        await db.refresh(obj)
        return obj

    @staticmethod
    async def patch_portfolio_company(
        db: AsyncSession, portfolio_company_id: int, payload: PortfolioCompanyPatch
    ):
        obj = await PortfolioService.get_portfolio_company(db, portfolio_company_id)
        if not obj:
            return None
        data = payload.model_dump(exclude_unset=True)
        if "fy_end" in data:
            raw_fy = data.get("fy_end")
            if raw_fy is None or (isinstance(raw_fy, str) and not raw_fy.strip()):
                data["fy_end"] = None
            else:
                normalized, legacy_date = apply_fy_end_fields(fy_end=str(raw_fy))
                data["fy_end"] = normalized
                data["fy_end_date"] = legacy_date
        # review_stage is no longer a managed state field — audit state lives on
        # entities.status. Ignore any stray review_stage in the payload so it can't be
        # written back onto the dormant company column.
        data.pop("review_stage", None)
        before = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys())) if data else {}
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        if data:
            after = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys()))
            changes = CompanyAuditRecorder.diff_snapshots(before, after)
            if is_review_cycle_adjustments_context():
                recorder = ReviewCycleAuditRecorder(db)
                for field, (before_val, after_val) in changes.items():
                    action = build_cycle_field_action(
                        company_name=obj.name or f"Company id {portfolio_company_id}",
                        field=field,
                        before=before_val,
                        after=after_val,
                        review_cycle_id=obj.review_cycle_id,
                    )
                    await recorder.log(
                        action=action,
                        review_cycle_id=obj.review_cycle_id,
                        meta={
                            "event": "cycle.company_field_updated",
                            "portfolio_company_id": portfolio_company_id,
                            "field": field,
                        },
                        search_text=f"{obj.name} {action}",
                    )
            else:
                await CompanyAuditRecorder(db).log_company_field_changes(
                    portfolio_company_id=portfolio_company_id,
                    changes=changes,
                    company=obj,
                )
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete_portfolio_company(
        db: AsyncSession, portfolio_company_id: int
    ) -> bool:
        obj = await PortfolioService.get_portfolio_company(db, portfolio_company_id)
        if not obj:
            return False
        if not is_unassigned_portfolio_company(obj):
            name = obj.name
            await CompanyAuditRecorder(db).log_company(
                portfolio_company_id=portfolio_company_id,
                action=f'Portfolio company "{name}" deleted',
                meta={"event": "portfolio_company.deleted"},
                company=obj,
            )
        await db.delete(obj)
        return True

    # ---- Entity ----
    @staticmethod
    async def list_entities(
        db: AsyncSession,
        *,
        portfolio_company_id: Optional[int],
        review_cycle: Optional[str] = None,
        limit: int,
        offset: int,
    ) -> Tuple[list[Entity], int]:
        stmt = select(Entity).options(selectinload(Entity.portfolio_company))
        count_stmt = select(func.count(Entity.id)).select_from(Entity)
        if portfolio_company_id is not None:
            stmt = stmt.where(Entity.portfolio_company_id == portfolio_company_id)
            count_stmt = count_stmt.where(Entity.portfolio_company_id == portfolio_company_id)
        if review_cycle is not None:
            stmt = stmt.where(Entity.review_cycle == review_cycle)
            count_stmt = count_stmt.where(Entity.review_cycle == review_cycle)
        total = (await db.execute(count_stmt)).scalar_one()
        items = (
            (await db.execute(stmt.order_by(Entity.id).limit(limit).offset(offset))).scalars().all()
        )
        return items, total

    @staticmethod
    async def create_entity(db: AsyncSession, payload: EntityCreate):
        pc = await PortfolioService.get_portfolio_company(db, payload.portfolio_company_id)
        if not pc:
            return None
        data = payload.model_dump()
        incoming = data.get("review_cycle")
        rc = incoming.strip() if isinstance(incoming, str) else ""
        if rc:
            data["review_cycle"] = rc[:64]
        elif pc.review_cycle_id:
            rid = str(pc.review_cycle_id).strip()
            data["review_cycle"] = rid[:64] if rid else None
        else:
            data["review_cycle"] = None
        if data.get("status") is None:
            data["status"] = EntityReviewStatus.NOT_APPLICABLE.value
        obj = Entity(**data)
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        await CompanyAuditRecorder(db).log_company(
            portfolio_company_id=payload.portfolio_company_id,
            action=f'Entity "{obj.name}" created',
            meta={"event": "entity.created", "entity_id": obj.id},
        )
        return obj

    @staticmethod
    async def get_entity(db: AsyncSession, entity_id: int):
        stmt = select(Entity).where(Entity.id == entity_id)
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def patch_entity(
        db: AsyncSession, entity_id: int, payload: EntityPatch
    ):
        obj = await PortfolioService.get_entity(db, entity_id)
        if not obj:
            return None
        pc_id = obj.portfolio_company_id
        data = payload.model_dump(exclude_unset=True)
        before = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys())) if data else {}
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        if data:
            after = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys()))
            changes = CompanyAuditRecorder.diff_snapshots(before, after)
            await CompanyAuditRecorder(db).log_company_field_changes(
                portfolio_company_id=pc_id,
                changes=changes,
                entity_type="entity",
                entity_id=entity_id,
            )
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete_entity(db: AsyncSession, entity_id: int) -> bool:
        obj = await PortfolioService.get_entity(db, entity_id)
        if not obj:
            return False
        pc_id = obj.portfolio_company_id
        ent_name = obj.name
        await CompanyAuditRecorder(db).log_company(
            portfolio_company_id=pc_id,
            action=f'Entity "{ent_name}" deleted',
            meta={"event": "entity.deleted", "entity_id": entity_id},
        )
        await db.delete(obj)
        return True

    @staticmethod
    async def clear_org_chart_and_entities(
        db: AsyncSession, portfolio_company_id: int
    ) -> Optional[dict[str, Optional[int]]]:
        """
        Remove the org chart file reference, delete the stored org-chart file row (if any),
        and delete **all** entities for this portfolio company (LLM-extracted and manually added).
        """
        pc = await PortfolioService.get_portfolio_company(db, portfolio_company_id)
        if not pc:
            return None

        file_id_to_delete = pc.org_chart_file_id
        pc.org_chart_file_id = None
        await db.flush()

        await db.execute(
            update(Entity)
            .where(Entity.portfolio_company_id == portfolio_company_id)
            .values(parent_entity_id=None)
        )
        ent_res = await db.execute(delete(Entity).where(Entity.portfolio_company_id == portfolio_company_id))
        deleted_entities = int(ent_res.rowcount or 0)

        deleted_file_id: Optional[int] = None
        deleted_filename: Optional[str] = None
        if file_id_to_delete is not None:
            file_row = await PortfolioService.get_file(db, file_id_to_delete)
            if file_row and (file_row.filename or "").strip():
                deleted_filename = file_row.filename.strip()
            if await PortfolioService.delete_file(db, file_id_to_delete):
                deleted_file_id = file_id_to_delete

        await db.flush()
        cleared_action = f"Org chart cleared ({deleted_entities} entities removed)"
        if deleted_filename:
            cleared_action = (
                f'Org chart cleared ({deleted_entities} entities removed) from file "{deleted_filename}"'
            )
        await CompanyAuditRecorder(db).log_company(
            portfolio_company_id=portfolio_company_id,
            action=cleared_action,
            meta={
                "event": "org_chart.cleared",
                "deleted_entities": deleted_entities,
                "deleted_file_id": deleted_file_id,
                "filename": deleted_filename,
            },
        )
        return {"deleted_entities": deleted_entities, "deleted_file_id": deleted_file_id}

    # ---- FinancialMetricReconciliation ----
    @staticmethod
    async def list_financial_metric_reconciliation(
        db: AsyncSession,
        *,
        portfolio_company_id: Optional[int],
        entity_id: Optional[int],
        limit: int,
        offset: int,
    ):
        stmt = select(FinancialMetricReconciliation).options(
            selectinload(FinancialMetricReconciliation.entity),
        )
        count_stmt = select(func.count(FinancialMetricReconciliation.id)).select_from(
            FinancialMetricReconciliation
        )
        if portfolio_company_id is not None:
            stmt = stmt.where(FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id)
            count_stmt = count_stmt.where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id
            )
        if entity_id is not None:
            stmt = stmt.where(FinancialMetricReconciliation.entity_id == entity_id)
            count_stmt = count_stmt.where(FinancialMetricReconciliation.entity_id == entity_id)
        total = (await db.execute(count_stmt)).scalar_one()
        rows = (
            (
                await db.execute(
                    stmt.order_by(
                        FinancialMetricReconciliation.entity_id,
                        FinancialMetricReconciliation.review_cycle.desc(),
                        FinancialMetricReconciliation.metric_key.asc(),
                        FinancialMetricReconciliation.id.asc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    @staticmethod
    def _reconciliation_snowflake_keys(
        rows: list[FinancialMetricReconciliation],
    ) -> set[tuple[int, str]]:
        keys: set[tuple[int, str]] = set()
        for row in rows:
            rc = (row.review_cycle or "").strip()
            if rc:
                keys.add((row.portfolio_company_id, rc))
        return keys

    @staticmethod
    async def build_reconciliation_reads(
        db: AsyncSession,
        rows: list[FinancialMetricReconciliation],
        *,
        entity_names: Optional[dict[int, str]] = None,
    ) -> list[FinancialMetricReconciliationRead]:
        sf_map = await fetch_canonical_snowflake_map(db, PortfolioService._reconciliation_snowflake_keys(rows))
        items: list[FinancialMetricReconciliationRead] = []
        for row in rows:
            read = FinancialMetricReconciliationRead.model_validate(row)
            if entity_names is not None:
                read.entity_name = entity_names.get(row.entity_id)
            sf = sf_map.get((row.portfolio_company_id, (row.review_cycle or "").strip()))
            mis_amount, mis_currency = resolve_mis_for_recon_row(row, sf)
            read.mis_amount = mis_amount
            read.mis_currency = mis_currency
            items.append(read)
        return items

    @staticmethod
    async def get_financial_metric_reconciliation(db: AsyncSession, reconciliation_id: int):
        stmt = (
            select(FinancialMetricReconciliation)
            .options(selectinload(FinancialMetricReconciliation.entity))
            .where(FinancialMetricReconciliation.id == reconciliation_id)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def patch_financial_metric_reconciliation(
        db: AsyncSession,
        reconciliation_id: int,
        payload: FinancialMetricReconciliationPatch,
    ):
        obj = await PortfolioService.get_financial_metric_reconciliation(db, reconciliation_id)
        if not obj:
            return None
        data = payload.model_dump(exclude_unset=True)
        edit_reason_raw = data.pop("edit_reason", None)
        edit_reason = (
            (edit_reason_raw or "").strip()
            if isinstance(edit_reason_raw, str)
            else ""
        )
        recorder = CompanyAuditRecorder(db)
        # Collect (field, before, after, action) so we can stamp manual-edit markers (drives the
        # "edited manually" badge + justification popup) after the loop.
        marker_writes: list[tuple[str, Any, Any, str]] = []

        if {"mis_amount", "mis_currency"}.intersection(payload.model_fields_set):
            raise ValueError(
                "MIS amounts are read from FinancialDataSnowflake; update Snowflake data instead"
            )

        enable_val = data.pop("enable", None)

        amt_touch = {"afs_amount", "afs_currency"}.intersection(payload.model_fields_set)
        if amt_touch and not edit_reason:
            raise ValueError(
                "edit_reason is required when updating AFS metric amounts or currency fields"
            )

        # Apply scalar updates first so breach checks observe final amounts.
        for k, v in data.items():
            if k == "extra_data":
                base = dict(obj.extra_data or {})
                if isinstance(v, dict):
                    base.update(v)
                    setattr(obj, "extra_data", base)
                continue

            mk = (obj.metric_key or "").strip().lower()
            if mk not in FINANCIAL_METRIC_LABELS:
                if k == "status":
                    before_status = getattr(obj, k, None)
                    if before_status != v:
                        marker_writes.append((k, before_status, v, ACTION_FIELD_EDIT))
                        setattr(obj, k, v)
                        await recorder.log_company(
                            portfolio_company_id=obj.portfolio_company_id,
                            action=(
                                f"Reconciliation row status updated from "
                                f"{before_status or '—'} \u2192 {v or '—'}"
                                f" (metric: {mk or obj.metric_key}, row id: {obj.id})"
                            ),
                            meta={
                                "event": "financial_metric_reconciliation.status_changed",
                                "entity_id": obj.entity_id,
                                "row_id": obj.id,
                                "metric_key": obj.metric_key,
                                "field": "status",
                                "before": before_status,
                                "after": v,
                            },
                        )
                    else:
                        setattr(obj, k, v)
                else:
                    setattr(obj, k, v)
                continue

            before = getattr(obj, k, None)
            source_: Optional[str] = None
            if k in ("afs_amount", "afs_currency"):
                source_ = "extracted"

            if source_ and metric_values_differ(before, v):
                await recorder.log_financial_metric_change(
                    portfolio_company_id=obj.portfolio_company_id,
                    metric_key=mk,
                    before=before,
                    after=v,
                    source=source_,
                    entity_id=obj.entity_id,
                    row_id=obj.id,
                    edit_reason=edit_reason or None,
                )
                marker_writes.append((k, before, v, ACTION_AMOUNT_EDIT))
            elif k in _RECON_FIELD_EDIT_COLUMNS and before != v:
                marker_writes.append((k, before, v, ACTION_FIELD_EDIT))

            setattr(obj, k, v)

        # Stamp manual-edit markers for the AFS fields that changed. Non-fatal: a marker
        # failure must never block the edit. Keyed by column (e.g. "afs_amount").
        if marker_writes:
            try:
                actor = get_audit_actor_email()
                ed = dict(obj.extra_data or {})
                for fk, before, after, action in marker_writes:
                    set_marker(
                        ed,
                        fk,
                        build_marker(
                            action=action,
                            reason=edit_reason or None,
                            actor=actor,
                            previous_value=before,
                            new_value=after,
                        ),
                    )
                obj.extra_data = ed
            except Exception as e:  # pragma: no cover - marker must never block the edit
                logger.warning("recon manual-edit marker write failed (non-fatal): %s", str(e)[:200])

        pct_m, abs_m, pct_l, abs_l = await thresholds_for_company(db)
        usd_inr = await fetch_usd_to_inr_rate(db)
        sf_map = await fetch_canonical_snowflake_map(
            db, PortfolioService._reconciliation_snowflake_keys([obj])
        )
        sf = sf_map.get((obj.portfolio_company_id, (obj.review_cycle or "").strip()))
        mis_amount, mis_currency = resolve_mis_for_recon_row(obj, sf)
        if enable_val is True and not reconciliation_row_can_set_enable_true(
            obj,
            mis_amount=mis_amount,
            mis_currency=mis_currency,
            pct_by_metric=pct_m,
            abs_by_metric=abs_m,
            pct_by_label=pct_l,
            abs_by_label=abs_l,
            usd_to_inr_rate=usd_inr,
        ):
            raise ValueError(
                "enable=true is only allowed when MIS and AFS are both present and variance exceeds thresholds"
            )
        if enable_val is not None:
            obj.enable = bool(enable_val)

        await db.flush()
        # A manual metric edit must re-run reconciliation so the breach is recomputed AND
        # the entity status advances (e.g. → "Discrepancy identified"), matching the
        # extraction / Snowflake-sync paths. reconcile reads the stored afs_amount, so the
        # manually-entered value is preserved (not re-extracted/clobbered).
        if obj.portfolio_company_id and obj.entity_id and (obj.review_cycle or "").strip():
            from src.services.reconciliation_service import reconcile_all_metrics_for_entity_cycle
            await reconcile_all_metrics_for_entity_cycle(
                db,
                portfolio_company_id=obj.portfolio_company_id,
                entity_id=obj.entity_id,
                review_cycle=obj.review_cycle,
            )
            await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete_financial_metric_reconciliation(db: AsyncSession, reconciliation_id: int) -> bool:
        obj = await PortfolioService.get_financial_metric_reconciliation(db, reconciliation_id)
        if not obj:
            return False
        await db.delete(obj)
        return True

    @staticmethod
    async def convert_afs_currency_for_entity_cycle(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        entity_id: int,
        review_cycle: str,
        target_currency: str,
    ):
        rc = (review_cycle or "").strip()[:128]
        if not rc:
            return None
        stmt = (
            select(FinancialMetricReconciliation)
            .where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.entity_id == entity_id,
                FinancialMetricReconciliation.review_cycle == rc,
            )
            .options(selectinload(FinancialMetricReconciliation.entity))
        )
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            return None

        sample = rows[0]
        source_currency = (sample.afs_currency or "").strip().upper()
        target = target_currency.strip().upper()
        from fastapi import HTTPException

        if not source_currency:
            raise HTTPException(status_code=422, detail="AFS currency is not set on this entity/cycle group")
        if source_currency == target:
            raise HTTPException(status_code=422, detail="Target currency matches current AFS currency")

        # Convert at the FX rate as of the entity's financial year end — NOT the
        # latest/current rate — so AFS amounts are translated using the rate that
        # was in effect at the reporting date (mirrors the file FX preview flow).
        entity = sample.entity
        fy_end = (
            normalize_fy_end(entity.fy_end) or fy_end_from_legacy_date(entity.fy_end)
            if entity is not None
            else None
        )
        if not fy_end:
            raise HTTPException(
                status_code=422,
                detail="Entity has no financial year end (fy_end) set — cannot determine FX rate date.",
            )
        try:
            fy_date = fy_end_last_day(fy_end)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Entity fy_end '{fy_end}' is invalid.")
        at_dt = datetime.combine(fy_date, time(12, 0, 0), tzinfo=timezone.utc)
        try:
            rate, fx_ts = await fetch_historical_rate(
                db, from_currency=source_currency, to_currency=target, at=at_dt
            )
        except FxConversionUnavailable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Historical FX rate unavailable for {source_currency}→{target} "
                    f"at {fy_date.isoformat()} (fy_end {fy_end})."
                ),
            )

        changes_all: dict[int, dict[str, Any]] = {}
        recorder = CompanyAuditRecorder(db)

        for row in rows:
            before = row.afs_amount
            changes: dict[str, Any] = {}
            new_after: Optional[float] = None
            if before is None:
                pass
            else:
                try:
                    bf = float(before)
                except (TypeError, ValueError):
                    bf = None
                if bf is not None:
                    new_after = round(bf * rate, 4)
                    row.afs_amount = new_after
                    changes[str(row.metric_key)] = {"before": bf, "after": new_after}
                    await recorder.log_financial_metric_change(
                        portfolio_company_id=row.portfolio_company_id,
                        metric_key=row.metric_key,
                        before=before,
                        after=new_after,
                        source="extracted",
                        entity_id=row.entity_id,
                        row_id=row.id,
                        edit_reason=(
                            f"Currency converted {source_currency} → {target} at FX rate snapshot {fx_ts}"
                        ),
                    )
            row.afs_currency = target[:8]
            extra = dict(row.extra_data or {})
            hist = list(extra.get("afs_currency_conversion_history") or [])
            hist_entry = PortfolioService._build_currency_conversion_meta(
                source_currency=source_currency,
                target_currency=target,
                rate=rate,
                fx_timestamp=fx_ts,
                changes=changes,
            )
            hist.append(hist_entry)
            extra["afs_currency_conversion_history"] = hist[-20:]
            row.extra_data = extra
            if changes:
                changes_all[row.id] = changes

        await db.flush()
        if rows:
            ent_label = await recorder.resolve_entity_label(entity_id)
            await recorder.log_company(
                portfolio_company_id=portfolio_company_id,
                action=(
                    f'As per AFS currency for "{ent_label}" (cycle {rc}) converted '
                    f"{source_currency} → {target} at rate {rate:.6f}"
                ),
                meta={
                    "event": "financial_metric_reconciliation.currency_converted",
                    "review_cycle": rc,
                    "entity_id": entity_id,
                    "rows_updated": len(rows),
                },
            )
        await db.refresh(sample)
        return rows

    # ---- File ----
    @staticmethod
    async def list_files(
        db: AsyncSession,
        *,
        portfolio_company_id: Optional[int],
        entity_id: Optional[int],
        status_: Optional[str],
        unattached_only: bool = False,
        limit: int,
        offset: int,
    ):
        stmt = select(File).options(
            selectinload(File.portfolio_company),
            selectinload(File.entity),
        )
        count_stmt = select(func.count(File.id)).select_from(File)
        stmt = stmt.where(exclude_org_chart_uploads_clause())
        count_stmt = count_stmt.where(exclude_org_chart_uploads_clause())
        if portfolio_company_id is not None:
            stmt = stmt.where(File.portfolio_company_id == portfolio_company_id)
            count_stmt = count_stmt.where(File.portfolio_company_id == portfolio_company_id)
        if entity_id is not None:
            stmt = stmt.where(File.entity_id == entity_id)
            count_stmt = count_stmt.where(File.entity_id == entity_id)
        if unattached_only:
            stmt = stmt.where(File.entity_id.is_(None))
            count_stmt = count_stmt.where(File.entity_id.is_(None))
        if status_ is not None:
            stmt = stmt.where(File.status == status_)
            count_stmt = count_stmt.where(File.status == status_)
        total = (await db.execute(count_stmt)).scalar_one()
        items = (
            (await db.execute(stmt.order_by(File.id).limit(limit).offset(offset))).scalars().all()
        )
        return items, total

    @staticmethod
    async def create_file(db: AsyncSession, payload: FileCreate):
        pc = await PortfolioService.get_portfolio_company(db, payload.portfolio_company_id)
        if not pc:
            return None
        obj = File(**payload.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        await CompanyAuditRecorder(db).log_file(
            obj,
            action=f'File "{obj.filename}" uploaded',
            meta={"event": "file.uploaded", "status": obj.status, "tags": list(obj.tags or [])},
        )
        return obj

    @staticmethod
    async def get_file(db: AsyncSession, file_id: int):
        stmt = select(File).options(
            selectinload(File.portfolio_company),
            selectinload(File.entity),
        ).where(File.id == file_id)
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def ensure_metric_reconciliation_rows_for_entity(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        entity_id: int,
    ) -> None:
        """Create stub tall rows (six metrics) for (company, entity, resolved review_cycle) when missing."""
        rc_norm = await resolve_financial_review_cycle(
            db,
            portfolio_company_id=portfolio_company_id,
            entity_id=entity_id,
        )
        if not rc_norm:
            return

        for metric_key in RECON_METRIC_KEYS:
            stmt = select(FinancialMetricReconciliation).where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.entity_id == entity_id,
                FinancialMetricReconciliation.review_cycle == rc_norm,
                FinancialMetricReconciliation.metric_key == metric_key,
            )
            if (await db.execute(stmt)).scalars().first() is not None:
                continue
            db.add(
                FinancialMetricReconciliation(
                    portfolio_company_id=portfolio_company_id,
                    entity_id=entity_id,
                    review_cycle=rc_norm,
                    metric_key=metric_key,
                    category="financial",
                    type=metric_key,
                    enable=False,
                    discrepency_text="",
                    extra_data={},
                )
            )
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError:
                pass

    @staticmethod
    async def patch_file(db: AsyncSession, file_id: int, payload: FilePatch):
        obj = await PortfolioService.get_file(db, file_id)
        if not obj:
            return None
        data = payload.model_dump(exclude_unset=True)
        old_pc_id = obj.portfolio_company_id
        old_pc = await db.get(PortfolioCompany, old_pc_id)
        old_was_unassigned = is_unassigned_portfolio_company(old_pc)
        old_tags = list(obj.tags or [])
        old_status = obj.status
        old_entity_id = obj.entity_id

        if "portfolio_company_id" in data:
            pcid = data["portfolio_company_id"]
            if pcid is not None:
                pc = await PortfolioService.get_portfolio_company(db, pcid)
                if not pc:
                    raise ValueError("Portfolio company not found")

        target_pc_id = data["portfolio_company_id"] if "portfolio_company_id" in data else obj.portfolio_company_id
        if "entity_id" in data:
            ent_id = data["entity_id"]
            if ent_id is not None:
                ent = (await db.execute(select(Entity).where(Entity.id == ent_id))).scalar_one_or_none()
                if not ent:
                    raise ValueError("Entity not found")
                if ent.portfolio_company_id != target_pc_id:
                    raise ValueError("Entity does not belong to the selected portfolio company")
        elif (
            obj.entity_id is not None
            and "portfolio_company_id" in data
            and "entity_id" not in data
        ):
            ent = (await db.execute(select(Entity).where(Entity.id == obj.entity_id))).scalar_one_or_none()
            if ent and ent.portfolio_company_id != target_pc_id:
                raise ValueError(
                    "Current entity does not belong to the new portfolio company; pick an entity that belongs to the new company"
                )

        attachment_touched = bool({"portfolio_company_id", "entity_id"} & data.keys())
        if attachment_touched:
            new_pc_id = data.get("portfolio_company_id", obj.portfolio_company_id)
            new_ent = data["entity_id"] if "entity_id" in data else obj.entity_id
            if new_pc_id is not None and new_ent is None:
                raise ValueError("entity_id is required when assigning a file to a portfolio company")

        # fy_end belongs to the entity, not the file — write it there if provided
        fy_end_to_set = data.pop("fy_end", None)

        for k, v in data.items():
            setattr(obj, k, v)

        if fy_end_to_set:
            target_entity_id = data.get("entity_id", obj.entity_id)
            if target_entity_id is not None:
                target_ent = (await db.execute(select(Entity).where(Entity.id == target_entity_id))).scalar_one_or_none()
                if target_ent:
                    target_ent.fy_end = fy_end_to_set

        # When portfolio_company_id changes, sync review_cycle_id from the new company
        # unless the caller explicitly provided review_cycle_id in the patch payload.
        if "portfolio_company_id" in data and "review_cycle_id" not in data:
            new_pc_id = data["portfolio_company_id"]
            if new_pc_id is not None:
                linked_pc = await db.get(PortfolioCompany, new_pc_id)
                obj.review_cycle_id = linked_pc.review_cycle_id if linked_pc else None
            else:
                obj.review_cycle_id = None

        await db.flush()

        recorder = CompanyAuditRecorder(db)
        new_pc = await db.get(PortfolioCompany, obj.portfolio_company_id) if obj.portfolio_company_id else None

        if attachment_touched:
            if (
                "portfolio_company_id" in data
                and data["portfolio_company_id"] != old_pc_id
                and new_pc is not None
            ):
                await recorder.log_file(
                    obj,
                    action=(
                        f'File "{obj.filename}" assigned to this company '
                        f"(from portfolio company id {old_pc_id})"
                    ),
                    meta={
                        "event": "file.reassigned",
                        "from_portfolio_company_id": old_pc_id,
                        "entity_id": obj.entity_id,
                    },
                    company=new_pc,
                )
            elif "entity_id" in data and data.get("entity_id") != old_entity_id:
                ent_label = await recorder.resolve_entity_label(obj.entity_id)
                await recorder.log_file(
                    obj,
                    action=f'File "{obj.filename}" entity attachment updated to {ent_label}',
                    meta={"event": "file.entity_updated", "entity_id": obj.entity_id},
                    company=new_pc,
                )

        if "tags" in data and list(obj.tags or []) != old_tags:
            await recorder.log_file(
                obj,
                action=f'File "{obj.filename}" tags updated',
                meta={"event": "file.tags_updated", "before": old_tags, "after": list(obj.tags or [])},
                company=new_pc,
            )
        if "status" in data and obj.status != old_status:
            await recorder.log_file(
                obj,
                action=f'File "{obj.filename}" status updated from {old_status or "—"} → {obj.status or "—"}',
                meta={"event": "file.status_updated", "before": old_status, "after": obj.status},
                company=new_pc,
            )

        if attachment_touched and obj.entity_id is not None:
            await PortfolioService.ensure_metric_reconciliation_rows_for_entity(
                db,
                portfolio_company_id=obj.portfolio_company_id,
                entity_id=obj.entity_id,
            )
            # Push the file's stored per-file breakdown into FinancialMetricReconciliation
            # unconditionally — this file was just explicitly attached, so its numbers
            # should immediately appear on the dashboard regardless of primary-file status.
            # Falls back to full re-sync when breakdown hasn't been computed yet.
            await push_file_breakdown_to_reconciliation(db, file_row=obj)
            await db.flush()

        if attachment_touched:
            from src.db.models import FileOCRMetadata
            from src.services.audit_document_extraction import _sync_auditor_to_pcm
            meta = (
                await db.execute(
                    select(FileOCRMetadata).where(FileOCRMetadata.file_id == obj.id)
                )
            ).scalar_one_or_none()
            if meta and meta.audit_qualitative:
                _eng = meta.audit_qualitative.get("auditor_engagement") or {}
                if isinstance(_eng, dict) and _eng.get("auditor_firm"):
                    await _sync_auditor_to_pcm(
                        db,
                        obj,
                        _eng.get("auditor_firm"),
                        _eng.get("auditor_tier_label"),
                    )

        return await PortfolioService.get_file(db, file_id)

    @staticmethod
    async def delete_file(db: AsyncSession, file_id: int) -> bool:
        obj = await PortfolioService.get_file(db, file_id)
        if not obj:
            return False
        fname = obj.filename
        await CompanyAuditRecorder(db).log_file(
            obj,
            action=f'File "{fname}" deleted',
            meta={"event": "file.deleted"},
        )
        await db.delete(obj)
        return True

    # ---- ManualReconciliationQuery (manual email queries) ----
    @staticmethod
    async def list_manual_reconciliation_queries(
        db: AsyncSession,
        *,
        portfolio_company_id: Optional[int],
        entity_id: Optional[int],
        limit: int,
        offset: int,
    ):
        stmt = select(ManualReconciliationQuery).options(selectinload(ManualReconciliationQuery.entity))
        count_stmt = select(func.count(ManualReconciliationQuery.id)).select_from(ManualReconciliationQuery)
        if portfolio_company_id is not None:
            stmt = stmt.where(ManualReconciliationQuery.portfolio_company_id == portfolio_company_id)
            count_stmt = count_stmt.where(ManualReconciliationQuery.portfolio_company_id == portfolio_company_id)
        if entity_id is not None:
            stmt = stmt.where(ManualReconciliationQuery.entity_id == entity_id)
            count_stmt = count_stmt.where(ManualReconciliationQuery.entity_id == entity_id)
        total = (await db.execute(count_stmt)).scalar_one()
        items = (
            (
                await db.execute(stmt.order_by(ManualReconciliationQuery.id.asc()).limit(limit).offset(offset))
            )
            .scalars()
            .all()
        )
        return items, total

    @staticmethod
    async def create_manual_reconciliation_query(
        db: AsyncSession, payload: ManualReconciliationQueryCreate
    ):
        pc = await PortfolioService.get_portfolio_company(db, payload.portfolio_company_id)
        if not pc:
            return None
        if payload.entity_id is not None:
            entity = await db.get(Entity, payload.entity_id)
            if entity is None or entity.portfolio_company_id != payload.portfolio_company_id:
                from fastapi import HTTPException
                raise HTTPException(status_code=422, detail="entity_id does not belong to this company")
        data = payload.model_dump()
        if not (data.get("status") or "").strip():
            data["status"] = "Open"
        obj = ManualReconciliationQuery(**data)
        db.add(obj)
        await db.flush()
        await CompanyAuditRecorder(db).log_company(
            portfolio_company_id=payload.portfolio_company_id,
            action=f'Manual query created: {(obj.discrepency_text or "")[:120]}',
            meta={"event": "manual_query.created", "id": obj.id},
        )
        return obj

    @staticmethod
    async def get_manual_reconciliation_query(db: AsyncSession, query_id: int):
        stmt = (
            select(ManualReconciliationQuery)
            .options(selectinload(ManualReconciliationQuery.entity))
            .where(ManualReconciliationQuery.id == query_id)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def patch_manual_reconciliation_query(
        db: AsyncSession, query_id: int, payload: ManualReconciliationQueryPatch
    ):
        obj = await PortfolioService.get_manual_reconciliation_query(db, query_id)
        if not obj:
            return None
        pc_id = obj.portfolio_company_id
        data = payload.model_dump(exclude_unset=True)
        before = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys())) if data else {}
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        if data:
            after = CompanyAuditRecorder.snapshot_obj(obj, list(data.keys()))
            changes = CompanyAuditRecorder.diff_snapshots(before, after)
            recorder = CompanyAuditRecorder(db)
            for field, (b, a) in changes.items():
                label = field.replace("_", " ").title()
                await recorder.log_company(
                    portfolio_company_id=pc_id,
                    action=f"Manual query {obj.id}: {label} updated from {format_audit_value(b)} → {format_audit_value(a)}",
                    meta={"event": "manual_query.updated", "id": obj.id, "field": field},
                )
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete_manual_reconciliation_query(db: AsyncSession, query_id: int) -> bool:
        obj = await PortfolioService.get_manual_reconciliation_query(db, query_id)
        if not obj:
            return False
        txt = (obj.discrepency_text or "")[:120]
        await CompanyAuditRecorder(db).log_company(
            portfolio_company_id=obj.portfolio_company_id,
            action=f"Manual query deleted: {txt}",
            meta={"event": "manual_query.deleted", "id": query_id},
        )
        await db.delete(obj)
        return True

    # ---- Org chart helpers (compat routes) ----
    @staticmethod
    async def list_entities_for_company(db: AsyncSession, portfolio_company_id: int) -> list[Entity]:
        return (
            (
                await db.execute(
                    select(Entity)
                    .where(Entity.portfolio_company_id == portfolio_company_id)
                    .order_by(Entity.id)
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def reparent_entity(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        child_entity_id: int,
        new_parent_entity_id: Optional[int],
    ) -> Optional[list[Entity]]:
        child = await db.get(Entity, child_entity_id)
        if not child or child.portfolio_company_id != portfolio_company_id:
            return None

        if new_parent_entity_id is not None:
            parent = await db.get(Entity, new_parent_entity_id)
            if not parent or parent.portfolio_company_id != portfolio_company_id:
                return None
            if parent.id == child.id:
                return None

        child.parent_entity_id = new_parent_entity_id
        await db.flush()
        recorder = CompanyAuditRecorder(db)
        parent_label = await recorder.resolve_entity_label(new_parent_entity_id)
        await recorder.log_company(
            portfolio_company_id=portfolio_company_id,
            action=f'Entity "{child.name}" reparented under {parent_label}',
            meta={
                "event": "entity.reparented",
                "child_entity_id": child_entity_id,
                "new_parent_entity_id": new_parent_entity_id,
            },
        )
        return await PortfolioService.list_entities_for_company(db, portfolio_company_id)

    # ---- Snowflake financials (minimal/compat) ----
    @staticmethod
    def _snowflake_to_api(row: FinancialDataSnowflake, *, fallback_company_id: Optional[int]) -> dict:
        payload = dict(row.payload or {})
        metrics = [
            "revenue",
            "ebitda",
            "pbt",
            "pat",
            "cash",
            "debt",
        ]
        # Prefer structured columns when present.
        portfolio_company_id = getattr(row, "portfolio_company_id", None) or payload.get("portfolio_company_id") or fallback_company_id
        entity_id = getattr(row, "entity_id", None) or payload.get("entity_id")
        period_start = payload.get("period_start")
        period_end = payload.get("period_end")
        review_cycle = getattr(row, "review_cycle", None) or payload.get("review_cycle")
        frequency = getattr(row, "frequency", None) or payload.get("frequency")
        currency = getattr(row, "currency", None) or payload.get("currency")
        out = {
            "id": row.id,
            "source_ref": row.source_ref,
            "portfolio_company_id": portfolio_company_id,
            "entity_id": entity_id,
            "period_start": period_start.isoformat() if hasattr(period_start, "isoformat") else period_start,
            "period_end": period_end.isoformat() if hasattr(period_end, "isoformat") else period_end,
            "review_cycle": review_cycle,
            "frequency": frequency,
            "currency": currency,
            "extra_data": payload.get("extra_data") or payload,
            # Per-metric manual-edit markers (keyed by metric, e.g. "ebitda"); None when nothing edited.
            "manual_edits": payload.get("manual_edits") if isinstance(payload.get("manual_edits"), dict) and payload.get("manual_edits") else None,
            "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
            "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
        }
        for k in metrics:
            out[k] = getattr(row, k, None) if getattr(row, k, None) is not None else payload.get(k)
        return out

    @staticmethod
    async def list_financial_data_snowflake(
        db: AsyncSession,
        *,
        portfolio_company_id: Optional[int],
        entity_id: Optional[int],
        limit: int,
        offset: int,
    ) -> Tuple[list[dict], int]:
        stmt = select(FinancialDataSnowflake)
        count_stmt = select(func.count(FinancialDataSnowflake.id))

        # Prefer structured filters when columns exist; payload fallback remains in _snowflake_to_api.
        if portfolio_company_id is not None and hasattr(FinancialDataSnowflake, "portfolio_company_id"):
            stmt = stmt.where(FinancialDataSnowflake.portfolio_company_id == portfolio_company_id)
            count_stmt = count_stmt.where(FinancialDataSnowflake.portfolio_company_id == portfolio_company_id)
        if entity_id is not None and hasattr(FinancialDataSnowflake, "entity_id"):
            stmt = stmt.where(FinancialDataSnowflake.entity_id == entity_id)
            count_stmt = count_stmt.where(FinancialDataSnowflake.entity_id == entity_id)

        total = (await db.execute(count_stmt)).scalar_one()
        rows = (
            (
                await db.execute(
                    stmt.order_by(
                        FinancialDataSnowflake.review_cycle.desc().nulls_last(),
                        FinancialDataSnowflake.id.desc(),
                    ).limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        items = [PortfolioService._snowflake_to_api(r, fallback_company_id=portfolio_company_id) for r in rows]
        return items, total

    @staticmethod
    async def create_financial_data_snowflake(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        review_cycle: Optional[str],
        currency: Optional[str],
        entity_id: Optional[int] = None,
        frequency: Optional[str] = None,
        metric_key: str,
        metric_value: float,
        edit_reason: str,
    ) -> dict:
        payload: dict[str, Any] = {
            "portfolio_company_id": portfolio_company_id,
            "manually_edited_metrics": [metric_key],
        }
        obj = FinancialDataSnowflake(
            portfolio_company_id=portfolio_company_id,
            entity_id=entity_id,
            review_cycle=review_cycle,
            frequency=frequency,
            currency=currency,
            payload=payload,
        )
        setattr(obj, metric_key, metric_value)
        db.add(obj)
        await db.flush()
        await db.refresh(obj)

        recorder = CompanyAuditRecorder(db)
        await recorder.log_financial_metric_change(
            portfolio_company_id=portfolio_company_id,
            metric_key=metric_key,
            before=None,
            after=metric_value,
            source="snowflake",
            entity_id=entity_id,
            row_id=obj.id,
            edit_reason=edit_reason.strip(),
        )

        return PortfolioService._snowflake_to_api(obj, fallback_company_id=portfolio_company_id)

    @staticmethod
    async def patch_financial_data_snowflake(
        db: AsyncSession,
        *,
        row_id: int,
        payload_patch: dict,
        audit_financial_metric_edits: bool = True,
        metric_edit_reason: Optional[str] = None,
    ) -> Optional[dict]:
        obj = await db.get(FinancialDataSnowflake, row_id)
        if not obj:
            return None

        reason_stripped = (metric_edit_reason or "").strip()
        merged = dict(obj.payload or {})

        metrics_in_patch = [k for k in _FINANCIAL_METRIC_KEYS if k in payload_patch]
        snap_before: dict[str, Any] = {}
        for k in metrics_in_patch:
            snap_before[k] = PortfolioService._snowflake_metric_value(obj, k)

        if audit_financial_metric_edits and metrics_in_patch:
            for k in metrics_in_patch:
                new_v = payload_patch.get(k)
                if metric_values_differ(snap_before.get(k), new_v) and not reason_stripped:
                    raise ValueError(
                        "edit_reason is required when updating financial metric values"
                    )

        for k, v in payload_patch.items():
            if hasattr(obj, k):
                try:
                    setattr(obj, k, v)
                except Exception:
                    pass
            merged[k] = v

        # Track manually-edited metrics so the data sync skips overwriting them.
        if metrics_in_patch:
            protected: set[str] = set(merged.get("manually_edited_metrics") or [])
            protected.update(metrics_in_patch)
            merged["manually_edited_metrics"] = sorted(protected)

        # Manual-edit markers per changed metric — drives the "edited manually" badge on MIS cells.
        # Non-fatal: a marker failure must never block the edit.
        if metrics_in_patch:
            try:
                actor = get_audit_actor_email()
                me = dict(merged.get("manual_edits") or {})
                for k in metrics_in_patch:
                    after_v = payload_patch.get(k)
                    if metric_values_differ(snap_before.get(k), after_v):
                        me[k] = build_marker(
                            action=ACTION_METRIC_EDIT,
                            reason=reason_stripped or None,
                            actor=actor,
                            previous_value=snap_before.get(k),
                            new_value=after_v,
                        )
                if me:
                    merged["manual_edits"] = me
            except Exception as e:  # pragma: no cover - marker must never block the edit
                logger.warning("snowflake manual-edit marker write failed (non-fatal): %s", str(e)[:200])

        obj.payload = merged

        await db.flush()
        await db.refresh(obj)

        if audit_financial_metric_edits and metrics_in_patch and reason_stripped:
            recorder = CompanyAuditRecorder(db)
            pc_id_for_audit = PortfolioService._snowflake_company_id(obj)
            if pc_id_for_audit is not None:
                eid = PortfolioService._snowflake_entity_id(obj)
                row_ref = int(getattr(obj, "id", row_id))
                for k in metrics_in_patch:
                    before_v = snap_before[k]
                    after_v = PortfolioService._snowflake_metric_value(obj, k)
                    if metric_values_differ(before_v, after_v):
                        await recorder.log_financial_metric_change(
                            portfolio_company_id=int(pc_id_for_audit),
                            metric_key=k,
                            before=before_v,
                            after=after_v,
                            source="snowflake",
                            entity_id=eid,
                            row_id=row_ref,
                            edit_reason=reason_stripped,
                        )

        if (bool(metrics_in_patch) or "currency" in payload_patch) and obj.portfolio_company_id and (obj.review_cycle or "").strip():
            from src.services.reconciliation_service import reconcile_all_metrics_for_company_cycle
            await reconcile_all_metrics_for_company_cycle(
                db,
                portfolio_company_id=int(obj.portfolio_company_id),
                review_cycle=obj.review_cycle,
            )
            await db.flush()

        return PortfolioService._snowflake_to_api(
            obj, fallback_company_id=getattr(obj, "portfolio_company_id", None) or merged.get("portfolio_company_id")
        )

    @staticmethod
    def _snowflake_company_id(row: FinancialDataSnowflake) -> Optional[int]:
        raw = getattr(row, "portfolio_company_id", None)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        payload = dict(row.payload or {})
        pc = payload.get("portfolio_company_id")
        if pc is None:
            return None
        try:
            return int(pc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def _resolve_snowflake_fx_date(
        db: AsyncSession, obj: FinancialDataSnowflake
    ) -> tuple[str, date]:
        """Resolve the reporting fy_end (``Mmm-YY``) + its month-end date for a
        company-level MIS row, so currency conversion uses the FX rate in effect
        at the reporting date rather than today's rate.

        Order of preference:
          1. The row's portfolio company's stored ``fy_end`` (authoritative — set
             by the PR-submission sync).
          2. Derived from the row's ``review_cycle`` — the cycle's FY-end month,
             i.e. the last month of its Jun→May window.
        """
        from fastapi import HTTPException

        fy_end: Optional[str] = None
        pc_id = PortfolioService._snowflake_company_id(obj)
        if pc_id is not None:
            pc = await db.get(PortfolioCompany, pc_id)
            if pc is not None:
                fy_end = normalize_fy_end(pc.fy_end) or fy_end_from_legacy_date(pc.fy_end)

        if not fy_end:
            rc = (getattr(obj, "review_cycle", None) or "").strip()
            cycle_months = months_for_review_cycle_id(rc) or months_for_review_cycle_name(rc)
            if cycle_months:
                fy_end = cycle_months[-1]

        if not fy_end:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not determine a financial year end for this MIS row "
                    "(no company fy_end and unrecognised review cycle) — cannot pick an FX rate date."
                ),
            )
        try:
            return fy_end, fy_end_last_day(fy_end)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Resolved fy_end '{fy_end}' is invalid.")

    @staticmethod
    async def list_company_financial_data_snowflake(
        db: AsyncSession,
        *,
        portfolio_company_id: int,
        limit: int,
        offset: int,
    ) -> Tuple[list[dict], int]:
        payload_company = FinancialDataSnowflake.payload["portfolio_company_id"].as_string()
        company_match = or_(
            FinancialDataSnowflake.portfolio_company_id == portfolio_company_id,
            cast(payload_company, Integer) == portfolio_company_id,
        )
        stmt = select(FinancialDataSnowflake).where(company_match)
        count_stmt = select(func.count(FinancialDataSnowflake.id)).where(company_match)

        total = (await db.execute(count_stmt)).scalar_one()
        rows = (
            (
                await db.execute(
                    stmt.order_by(
                        FinancialDataSnowflake.entity_id.nulls_last(),
                        FinancialDataSnowflake.review_cycle.desc().nulls_last(),
                        FinancialDataSnowflake.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        items = [
            PortfolioService._snowflake_to_api(r, fallback_company_id=portfolio_company_id) for r in rows
        ]
        entity_ids = {int(i["entity_id"]) for i in items if i.get("entity_id") is not None}
        entity_names: dict[int, str] = {}
        if entity_ids:
            ent_rows = (
                await db.execute(
                    select(Entity.id, Entity.name).where(
                        Entity.id.in_(entity_ids),
                        Entity.portfolio_company_id == portfolio_company_id,
                    )
                )
            ).all()
            entity_names = {int(rid): name for rid, name in ent_rows}
        enriched: list[dict] = []
        for item in items:
            eid = item.get("entity_id")
            out = dict(item)
            out["entity_name"] = entity_names.get(int(eid)) if eid is not None else None
            out["extra_data"] = out.get("extra_data") or {}
            enriched.append(out)
        return enriched, total

    @staticmethod
    async def set_snowflake_entity_attachment(
        db: AsyncSession,
        *,
        row_id: int,
        entity_id: Optional[int],
    ) -> Optional[dict]:
        from fastapi import HTTPException

        obj = await db.get(FinancialDataSnowflake, row_id)
        if not obj:
            return None

        company_id = PortfolioService._snowflake_company_id(obj)

        if entity_id is not None:
            entity = await db.get(Entity, entity_id)
            if not entity:
                raise HTTPException(status_code=422, detail="Entity not found")
            if company_id is not None and entity.portfolio_company_id != company_id:
                raise HTTPException(
                    status_code=422,
                    detail="Entity does not belong to the same portfolio company as this Snowflake row",
                )
            if company_id is None:
                company_id = entity.portfolio_company_id
        else:
            if company_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Cannot detach entity: Snowflake row has no portfolio company context",
                )

        patch: dict[str, Any] = {"entity_id": entity_id}
        if company_id is not None:
            patch["portfolio_company_id"] = company_id

        updated = await PortfolioService.patch_financial_data_snowflake(
            db, row_id=row_id, payload_patch=patch
        )
        if not updated:
            return None

        entity_name = None
        if entity_id is not None:
            entity = await db.get(Entity, entity_id)
            entity_name = entity.name if entity else None
        out = dict(updated)
        out["entity_name"] = entity_name
        out["extra_data"] = out.get("extra_data") or {}
        return out

    @staticmethod
    def _build_currency_conversion_meta(
        *,
        source_currency: str,
        target_currency: str,
        rate: float,
        fx_timestamp: str,
        changes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "from": source_currency,
            "to": target_currency,
            "rate": rate,
            "fx_timestamp": fx_timestamp,
            "converted_at": datetime.now(timezone.utc).isoformat(),
            "metrics_updated": sorted(changes.keys()),
            "changes": changes,
        }

    @staticmethod
    async def _apply_currency_conversion_to_metrics(
        *,
        rate: float,
        get_metric,
        set_metric,
    ) -> dict[str, dict[str, Any]]:
        changes: dict[str, dict[str, Any]] = {}
        for key in _FINANCIAL_METRIC_KEYS:
            before = get_metric(key)
            if before is None:
                continue
            try:
                before_f = float(before)
            except (TypeError, ValueError):
                continue
            after_f = round(before_f * rate, 4)
            set_metric(key, after_f)
            changes[key] = {"before": before_f, "after": after_f}
        return changes


    @staticmethod
    async def convert_financial_data_snowflake_currency(
        db: AsyncSession,
        *,
        row_id: int,
        target_currency: str,
    ) -> Optional[dict]:
        from fastapi import HTTPException

        obj = await db.get(FinancialDataSnowflake, row_id)
        if not obj:
            return None

        merged = dict(obj.payload or {})
        source_currency = (
            (getattr(obj, "currency", None) or merged.get("currency") or "") or ""
        ).strip().upper()
        target = target_currency.strip().upper()
        if not source_currency:
            raise HTTPException(status_code=422, detail="Row has no source currency set")
        if source_currency == target:
            raise HTTPException(status_code=422, detail="Target currency matches current currency")

        # Convert at the FX rate as of the reporting fy_end — NOT today's rate.
        fy_end, fy_date = await PortfolioService._resolve_snowflake_fx_date(db, obj)
        at_dt = datetime.combine(fy_date, time(12, 0, 0), tzinfo=timezone.utc)
        try:
            rate, fx_ts = await fetch_historical_rate(
                db, from_currency=source_currency, to_currency=target, at=at_dt
            )
        except FxConversionUnavailable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Historical FX rate unavailable for {source_currency}→{target} "
                    f"at {fy_date.isoformat()} (fy_end {fy_end})."
                ),
            )

        patch_metrics: dict[str, Any] = {}

        def _get(k: str):
            v = getattr(obj, k, None)
            if v is not None:
                return v
            return merged.get(k)

        def _set(k: str, v: Any):
            patch_metrics[k] = v
            if hasattr(obj, k):
                setattr(obj, k, v)

        changes = await PortfolioService._apply_currency_conversion_to_metrics(
            rate=rate,
            get_metric=_get,
            set_metric=_set,
        )

        extra = dict(merged.get("extra_data") or {})
        conv_meta = PortfolioService._build_currency_conversion_meta(
            source_currency=source_currency,
            target_currency=target,
            rate=rate,
            fx_timestamp=fx_ts,
            changes=changes,
        )
        extra["currency_conversion"] = conv_meta
        history = list(extra.get("currency_conversion_history") or [])
        history.append(conv_meta)
        extra["currency_conversion_history"] = history[-20:]

        patch: dict[str, Any] = {
            "currency": target,
            "extra_data": extra,
            **patch_metrics,
        }
        updated = await PortfolioService.patch_financial_data_snowflake(
            db,
            row_id=row_id,
            payload_patch=patch,
            audit_financial_metric_edits=False,
        )
        if not updated:
            return None

        pc_id = PortfolioService._snowflake_company_id(obj) or updated.get("portfolio_company_id")
        entity_id = updated.get("entity_id")
        if pc_id:
            entity_label = await CompanyAuditRecorder(db).resolve_entity_label(entity_id)
            await CompanyAuditRecorder(db).log_company(
                portfolio_company_id=int(pc_id),
                action=(
                    f'As per MIS currency for "{entity_label}" converted '
                    f"{source_currency} → {target} at rate {rate:.6f}; "
                    f"{len(changes)} metric(s) updated"
                ),
                meta={
                    "event": "financial_data_snowflake.currency_converted",
                    "source": "snowflake",
                    "row_id": row_id,
                    "entity_id": entity_id,
                    **conv_meta,
                },
            )
        return updated
