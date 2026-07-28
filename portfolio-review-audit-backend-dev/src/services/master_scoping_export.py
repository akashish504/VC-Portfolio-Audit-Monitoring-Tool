"""
Master Scoping data export / import service.

Download: builds an XLSX workbook from portfolio_company_metadata, one row per record.
Upload:   parses the XLSX and upserts rows keyed by (fund, deal_id, strategy, review_cycle_id).
          New rows (for a different cycle) are inserted; existing rows in the same cycle are updated.
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Entity, PortfolioCompany, PortfolioCompanyMetadata
from src.schema.portfolio import CompanyReviewStage, DealLevelStage1, DealLevelStage2
from src.services.company_audit_recorder import SYSTEM_ACTOR, CompanyAuditRecorder
from src.services.company_audit_context import get_audit_actor_email

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# Immutable key columns (define uniqueness — do not allow user to change)
# A record is identified by (fund, deal_id, strategy, review_cycle_id); there is no
# separate id/row column in the workbook.
_KEY_COLS: list[tuple[str, str]] = [
    ("fund", "Fund"),
    ("deal_id", "Deal ID"),
    ("strategy", "Strategy"),
]

# All editable columns: (field_name, header_label, col_type)
# These drive the XLSX upload parser AND the PATCH endpoint whitelist.
_EDITABLE_COLS: list[tuple[str, str, str]] = [
    ("deal_id_for_analysis",            "Deal ID for Analysis",                 "text"),
    ("deal_id_for_analysis_and_strategy", "Deal ID for Analysis & Strategy",    "text"),
    ("deal_name",                       "Deal Name",                            "text"),
    ("il_main",                         "IL (Main)",                            "text"),
    ("sector_l1",                       "Sector L1",                            "text"),
    ("sector_l2",                       "Sector L2",                            "text"),
    ("geo_l1",                          "Geo L1",                               "text"),
    ("geo_l2",                          "Geo L2",                               "text"),
    ("cost",                            "Cost",                                 "number"),
    ("distributed",                     "Distributed",                          "number"),
    ("proceeds",                        "Proceeds",                             "number"),
    ("fmv",                             "FMV",                                  "number"),
    ("ownership",                       "Ownership",                            "number"),
    ("scoping_for_audit",               "Scoping for Audit",                    "boolean"),
    ("reason_for_exclusion",            "Reason for Exclusion",                 "text"),
    ("deal_level_stage_1",              "Deal Level Stage 1",                   "enum_stage1"),
    ("deal_level_stage_2",              "Deal Level Stage 2",                   "enum_stage2"),
    ("tentative_audit_completion_date", "Tentative Audit Completion Date",      "text"),
    ("fy_end",                          "FY End",                               "text"),
    ("auditor",                         "Auditor",                              "text"),
    ("category_of_auditor",             "Category of Auditor",                  "text"),
    ("py_audit_status",                 "PY Audit Status",                      "text"),
    ("review_cycle_id",                 "Review Cycle ID",                      "text"),
    ("comments",                        "Comments",                             "text"),
]

# Server-computed columns — shown in the XLSX download for reference but never
# accepted via upload or PATCH (they are derived automatically on every upsert).
_COMPUTED_COLS: list[tuple[str, str]] = [
    ("category",                    "Category"),
    ("unique_by_company_id",        "Unique by Company ID"),
    ("unique_by_company_id_strategy", "Unique by Company ID and Strategy"),
    ("consolidated_cost",           "Consolidated Cost"),
    ("consolidated_fmv",            "Consolidated FMV"),
]

_NA_VALUES = {"na", "n/a", "none", "null", "-", "—", ""}


async def recompute_aggregates(
    db: AsyncSession,
    review_cycle_id: str,
) -> None:
    """
    Recompute the four derived columns for every row in the given review cycle:

    unique_by_company_id
        1 if this row is the first (lowest id) for its (deal_id, review_cycle_id)
        group, else 0.

    unique_by_company_id_strategy
        1 if this row is the first (lowest id) for its
        (deal_id, strategy, review_cycle_id) group, else 0.

    consolidated_cost
        SUM of cost across all rows with the same (deal_id, review_cycle_id).
        Every row in the group gets this same total.

    consolidated_fmv
        SUM of fmv across all rows with the same (deal_id, review_cycle_id).
        Every row in the group gets this same total.
    """
    # populate_existing=True forces SQLAlchemy to overwrite any already-loaded
    # identity-map entries with the freshly-flushed DB values, so the patched
    # row's new cost/fmv is visible when we compute group sums.
    rows: list[PortfolioCompanyMetadata] = (
        await db.execute(
            select(PortfolioCompanyMetadata)
            .where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
            .order_by(PortfolioCompanyMetadata.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()

    # --- unique_by_company_id: first id per (deal_id, review_cycle_id) ---
    seen_company: set[str] = set()

    # --- unique_by_company_id_strategy: first id per (deal_id, strategy, review_cycle_id) ---
    seen_company_strategy: set[tuple[str, str]] = set()

    # --- consolidated_cost / consolidated_fmv: sum per (deal_id, review_cycle_id) ---
    cost_totals: dict[str, Decimal] = defaultdict(Decimal)
    fmv_totals: dict[str, Decimal] = defaultdict(Decimal)

    for row in rows:
        if row.cost is not None:
            cost_totals[row.deal_id] += Decimal(str(row.cost))
        if row.fmv is not None:
            fmv_totals[row.deal_id] += Decimal(str(row.fmv))

    for row in rows:
        # unique_by_company_id
        if row.deal_id not in seen_company:
            row.unique_by_company_id = 1
            seen_company.add(row.deal_id)
        else:
            row.unique_by_company_id = 0

        # unique_by_company_id_strategy
        cs_key = (row.deal_id, row.strategy)
        if cs_key not in seen_company_strategy:
            row.unique_by_company_id_strategy = 1
            seen_company_strategy.add(cs_key)
        else:
            row.unique_by_company_id_strategy = 0

        # consolidated_cost / consolidated_fmv (same total for every row in group)
        row.consolidated_cost = float(cost_totals[row.deal_id]) if cost_totals[row.deal_id] else None
        row.consolidated_fmv = float(fmv_totals[row.deal_id]) if fmv_totals[row.deal_id] else None


def _cell(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in _NA_VALUES else s


def _xlsx_col_headers() -> list[str]:
    headers = [h for _, h in _KEY_COLS]
    headers += [label for _, label, _ in _EDITABLE_COLS]
    headers += [label for _, label in _COMPUTED_COLS]
    return headers


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def build_master_scoping_xlsx(
    db: AsyncSession,
    review_cycle_id: Optional[str] = None,
) -> tuple[bytes, str]:
    """Return (xlsx_bytes, filename) for portfolio_company_metadata rows.

    If review_cycle_id is provided, only rows for that cycle are included.
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc

    stmt = select(PortfolioCompanyMetadata).order_by(
        PortfolioCompanyMetadata.fund,
        PortfolioCompanyMetadata.deal_id,
        PortfolioCompanyMetadata.strategy,
    )
    if review_cycle_id:
        stmt = stmt.where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)

    rows_db = (await db.execute(stmt)).scalars().all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master Scoping"

    hdr_font = Font(bold=True, color="FFFFFF")
    key_fill      = PatternFill("solid", fgColor="1D4ED8")  # blue  — key cols
    editable_fill = PatternFill("solid", fgColor="15803D")  # green — editable
    computed_fill = PatternFill("solid", fgColor="6B7280")  # grey  — computed / read-only
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    headers = _xlsx_col_headers()
    n_key      = len(_KEY_COLS)
    n_editable = len(_EDITABLE_COLS)

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hdr_font
        cell.alignment = hdr_align
        if ci <= n_key:
            cell.fill = key_fill
        elif ci <= n_key + n_editable:
            cell.fill = editable_fill
        else:
            cell.fill = computed_fill

    ws.row_dimensions[1].height = 30
    # Freeze the header row and the three key columns (Fund / Deal ID / Strategy)
    # so they stay visible while scrolling the editable columns.
    ws.freeze_panes = get_column_letter(n_key + 1) + "2"

    for rec in rows_db:
        row: list[Any] = [rec.fund, rec.deal_id, rec.strategy]
        for field, _label, _type in _EDITABLE_COLS:
            v = getattr(rec, field, None)
            if _type == "boolean":
                row.append("Yes" if v else ("No" if v is False else ""))
            else:
                row.append(v if v is not None else "")
        for field, _label in _COMPUTED_COLS:
            row.append(getattr(rec, field, None) or "")
        ws.append(row)

    for ci, h in enumerate(headers, 1):
        col_letter = get_column_letter(ci)
        ws.column_dimensions[col_letter].width = max(14, min(40, len(h) + 4))

    # Instructions sheet
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["Column", "Type", "Notes"])
    ws2.append(["Fund", "KEY", "Part of unique key — do not change"])
    ws2.append(["Deal ID", "KEY", "Part of unique key — do not change"])
    ws2.append(["Strategy", "KEY", "Part of unique key — do not change"])
    ws2.append(["", "", ""])
    ws2.append(["--- Editable columns (green) ---", "", ""])
    _ENUM_NOTES: dict[str, str] = {
        "enum_stage1": "Allowed: " + " | ".join(m.value for m in DealLevelStage1),
        "enum_stage2": "Allowed: " + " | ".join(m.value for m in DealLevelStage2),
    }
    for field, label, col_type in _EDITABLE_COLS:
        note = _ENUM_NOTES.get(col_type, f"Editable — maps to portfolio_company_metadata.{field}")
        ws2.append([label, col_type.upper() if col_type not in _ENUM_NOTES else "ENUM", note])
    ws2.append(["", "", ""])
    ws2.append(["--- Computed columns (grey) — read-only, do not edit ---", "", ""])
    for field, label in _COMPUTED_COLS:
        ws2.append([label, "COMPUTED", f"Auto-calculated server-side — changes are ignored on upload"])
    ws2.append(["", "", ""])
    ws2.append(["NOTES", "", ""])
    ws2.append(["1. New rows (Fund+DealID+Strategy not present for the chosen Review Cycle) will be inserted."])
    ws2.append(["2. Rows that already exist for the chosen Review Cycle will be updated."])
    ws2.append(["3. Blank / NA values will clear the field."])
    ws2.append(["4. Do not change the Fund, Deal ID, or Strategy columns — these are part of the unique key."])
    ws2.append(["5. Review Cycle ID is set from the review cycle you choose at upload time and overrides this column."])
    ws2.append(["6. Uploading the same rows under a DIFFERENT review cycle always creates new records for that cycle."])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    if review_cycle_id:
        safe_cycle = review_cycle_id.replace("/", "-").replace(" ", "_")
        filename = f"master_scoping_{safe_cycle}_{date.today()}.xlsx"
    else:
        filename = f"master_scoping_{date.today()}.xlsx"
    return buf.read(), filename


# ---------------------------------------------------------------------------
# Upload / import
# ---------------------------------------------------------------------------

class UploadRowError:
    def __init__(self, row_num: int, reason: str):
        self.row_num = row_num
        self.reason = reason

    def to_dict(self) -> dict:
        return {"row": self.row_num, "reason": self.reason}


class UploadResult:
    def __init__(self):
        self.rows_processed = 0
        self.rows_updated = 0
        self.rows_inserted = 0
        self.rows_skipped = 0
        self.errors: list[UploadRowError] = []

    def to_dict(self) -> dict:
        return {
            "rows_processed": self.rows_processed,
            "rows_updated": self.rows_updated,
            "rows_inserted": self.rows_inserted,
            "rows_skipped": self.rows_skipped,
            "error_count": len(self.errors),
            "errors": [e.to_dict() for e in self.errors],
        }


async def process_master_scoping_upload(
    db: AsyncSession,
    file_bytes: bytes,
    review_cycle_id: str,
) -> UploadResult:
    """
    Parse an XLSX produced by build_master_scoping_xlsx, validate, and upsert
    PortfolioCompanyMetadata records.  Inserts new rows; updates existing ones.
    Aborts entirely if any row has a validation error.

    Uniqueness is keyed on (fund, deal_id, strategy, review_cycle_id) — the same
    deal uploaded under a different review cycle produces a new row rather than
    overwriting the old one.  The review cycle chosen at upload time is
    authoritative and overrides any "Review Cycle ID" value in the workbook.
    """
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc

    result = UploadResult()

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    if "Master Scoping" not in wb.sheetnames:
        raise ValueError("Uploaded file must contain a 'Master Scoping' sheet")

    ws = wb["Master Scoping"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        raise ValueError("Sheet 'Master Scoping' is empty")

    raw_headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    header_index: dict[str, int] = {h: i for i, h in enumerate(raw_headers)}

    required_headers = ["Fund", "Deal ID", "Strategy"] + [label for _, label, _ in _EDITABLE_COLS]
    missing = [h for h in required_headers if h not in header_index]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    # Load only records that belong to the target review cycle, keyed by
    # (fund, deal_id, strategy, review_cycle_id).  This ensures that the same
    # deal uploaded under a *different* cycle is treated as a new insert rather
    # than an update to the existing row.
    existing_records = (
        await db.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.review_cycle_id == review_cycle_id
            )
        )
    ).scalars().all()
    existing_map: dict[tuple[str, str, str, str], PortfolioCompanyMetadata] = {
        (r.fund, r.deal_id, r.strategy, r.review_cycle_id): r for r in existing_records
    }
    # Snapshot scoping_for_audit BEFORE the upsert loop mutates these live ORM objects,
    # so we can detect a genuine False→True transition for existing deals (not just inserts).
    prior_scoped_flags: dict[tuple[str, str, str], bool] = {
        key: bool(rec.scoping_for_audit) for key, rec in existing_map.items()
    }

    # Parse and validate all rows first
    pending: list[tuple[tuple[str, str, str], dict[str, Any]]] = []

    for row_num, dr in enumerate(rows[1:], start=2):
        result.rows_processed += 1

        fund_val = _cell(dr[header_index["Fund"]])
        deal_id_val = _cell(dr[header_index["Deal ID"]])
        strategy_val = _cell(dr[header_index["Strategy"]])

        if not fund_val or not deal_id_val or not strategy_val:
            result.errors.append(UploadRowError(row_num, "Fund, Deal ID, and Strategy are required"))
            continue

        field_updates: dict[str, Any] = {}
        field_error: Optional[UploadRowError] = None

        for field, label, col_type in _EDITABLE_COLS:
            if label not in header_index:
                continue
            raw = dr[header_index[label]]
            parsed, err = _parse_cell(raw, field, label, col_type, row_num)
            if err:
                field_error = err
                break
            field_updates[field] = parsed

        if field_error:
            result.errors.append(field_error)
            continue

        # The review cycle chosen at upload time is authoritative — it overrides
        # whatever the "Review Cycle ID" column in the workbook contained.
        field_updates["review_cycle_id"] = review_cycle_id

        pending.append(((fund_val, deal_id_val, strategy_val), field_updates))

    if result.errors:
        return result

    actor = get_audit_actor_email() or SYSTEM_ACTOR
    recorder = CompanyAuditRecorder(db)

    # Deduplicate pending rows by key — last row in the XLSX wins for each
    # (fund, deal_id, strategy) combination.  Without this, duplicate rows in
    # the uploaded file trigger multiple INSERTs for the same unique key and
    # violate the uq_pcm_fund_deal_strategy_cycle constraint.
    deduped_pending: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key_triple, updates in pending:
        deduped_pending[key_triple] = updates

    # Collect audit rows separately so the db.execute() calls that look up
    # PortfolioCompany IDs happen AFTER the explicit db.flush() below, not
    # during SQLAlchemy autoflush triggered mid-loop.  Autoflush fires when
    # any SELECT is issued on a session that has unflushed inserts, and if
    # those inserts include duplicates the unique constraint fires prematurely.
    pending_audit_rows: list[tuple[str, str, dict]] = []  # (deal_id, cycle_id, field_changes)

    # Apply upserts
    for (fund, deal_id, strategy), updates in deduped_pending.items():
        key = (fund, deal_id, strategy, review_cycle_id)
        rec = existing_map.get(key)

        if rec is None:
            # Insert — also register in existing_map so any later duplicate
            # rows in the same upload hit the update path instead.
            new_rec = PortfolioCompanyMetadata(
                fund=fund,
                deal_id=deal_id,
                strategy=strategy,
                deal_name=updates.get("deal_name") or "",
                **{k: v for k, v in updates.items() if k != "deal_name"},
            )
            db.add(new_rec)
            existing_map[key] = new_rec
            result.rows_inserted += 1
        else:
            # Update — collect field-level changes for audit
            field_changes: dict[str, tuple[Any, Any]] = {}
            changed = False
            for field, new_val in updates.items():
                current = getattr(rec, field, None)
                current_s = "" if current is None else str(current).strip()
                new_s = "" if new_val is None else str(new_val).strip()
                if current_s != new_s:
                    field_changes[field] = (current, new_val if new_s else None)
                    setattr(rec, field, new_val if new_s else None)
                    changed = True
            if changed:
                result.rows_updated += 1
                pending_audit_rows.append((deal_id, review_cycle_id, field_changes))
            else:
                result.rows_skipped += 1

    # Flush all inserts/updates to the DB before issuing any SELECTs.
    # This prevents autoflush from firing mid-loop when we look up
    # PortfolioCompany IDs for audit logging, which would violate the
    # unique constraint on rows that haven't been properly flushed yet.
    await db.flush()

    # Now it is safe to SELECT PortfolioCompany IDs for audit logging.
    for deal_id, cycle_id, field_changes in pending_audit_rows:
        pc_stmt = select(PortfolioCompany.id).where(
            PortfolioCompany.company_id == deal_id,
            PortfolioCompany.review_cycle_id == cycle_id,
        )
        pc_id = (await db.execute(pc_stmt)).scalars().first()
        if pc_id and field_changes:
            await recorder.log_company_field_changes(
                portfolio_company_id=pc_id,
                changes=field_changes,
                entity_type="portfolio_company_metadata",
                actor_email=actor,
            )

    # For any metadata row where scoping_for_audit just became True, advance the
    # matching company's entities' status from "Not applicable" → "Financials to be received".
    # Audit state lives on entities now. The match key is deal_id == company_id AND
    # review_cycle_id (when set on the metadata row).
    newly_scoped_deals: list[tuple[str, Optional[str]]] = []
    for (fund, deal_id, strategy), updates in deduped_pending.items():
        if updates.get("scoping_for_audit") is True:
            key = (fund, deal_id, strategy, review_cycle_id)
            # Newly scoped = inserted now, OR an existing deal whose scoping_for_audit
            # was False before this upload (read the PRE-update snapshot, not the live
            # object the upsert loop just set to True).
            was_scoped = prior_scoped_flags.get(key, False)
            if not was_scoped:
                newly_scoped_deals.append((deal_id, review_cycle_id))

    if newly_scoped_deals:
        for deal_id, cycle_id in newly_scoped_deals:
            pc_stmt = select(PortfolioCompany.id).where(PortfolioCompany.company_id == deal_id)
            if cycle_id:
                pc_stmt = pc_stmt.where(PortfolioCompany.review_cycle_id == cycle_id)
            pc_ids = (await db.execute(pc_stmt)).scalars().all()
            if not pc_ids:
                continue
            ent_stmt = select(Entity).where(
                Entity.portfolio_company_id.in_(pc_ids),
                or_(
                    Entity.status == CompanyReviewStage.NOT_APPLICABLE.value,
                    Entity.status.is_(None),
                ),
            )
            for ent in (await db.execute(ent_stmt)).scalars().all():
                before_status = ent.status
                ent.status = CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value
                for pc_id in pc_ids:
                    if ent.portfolio_company_id == pc_id:
                        await recorder.log_company_field_changes(
                            portfolio_company_id=pc_id,
                            changes={"status": (before_status, CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value)},
                            entity_type="entity",
                            entity_id=ent.id,
                            actor_email=actor,
                        )
                        break

    # Flush inserts/updates so new rows have IDs before aggregate computation.
    await db.flush()
    await recompute_aggregates(db, review_cycle_id)

    await db.commit()
    return result


# ---------------------------------------------------------------------------
# Bulk inline update
# ---------------------------------------------------------------------------

# Fields the bulk-edit UI may set. Restricted to the three dropdown columns the
# Master Scoping table renders as always-on selects.
_BULK_EDITABLE_FIELDS: dict[str, tuple[str, str]] = {
    field: (label, col_type)
    for field, label, col_type in _EDITABLE_COLS
    if field in ("scoping_for_audit", "deal_level_stage_1", "deal_level_stage_2")
}

# Deal Level Stage 2 only applies once Stage 1 reports completion; mirrors the
# STAGE_2_ENABLED_STAGE_1 set used in the frontend.
_STAGE_2_ENABLED_STAGE_1: set[str] = {
    DealLevelStage1.COMPLETED_WITHIN_DUE_DATE.value,
    DealLevelStage1.COMPLETED_POST_DUE_DATE.value,
}


class BulkUpdateResult:
    def __init__(self) -> None:
        self.updated = 0
        self.skipped = 0
        self.not_found = 0

    def to_dict(self) -> dict:
        return {
            "updated": self.updated,
            "skipped": self.skipped,
            "not_found": self.not_found,
        }


async def process_master_scoping_bulk_update(
    db: AsyncSession,
    record_ids: list[int],
    field: str,
    raw_value: Any,
) -> BulkUpdateResult:
    """
    Set one dropdown ``field`` to ``raw_value`` across every record in
    ``record_ids``, in a single transaction.

    Only ``scoping_for_audit`` / ``deal_level_stage_1`` / ``deal_level_stage_2``
    are accepted. When setting Stage 2, rows whose Stage 1 is not a completed
    status are skipped (counted in ``skipped``) rather than written, matching the
    per-row lock in the UI. Aggregate recompute and the scoping_for_audit
    entity-status advance reuse the same logic as the single-record PATCH.
    """
    meta = _BULK_EDITABLE_FIELDS.get(field)
    if meta is None:
        raise ValueError(f"Field '{field}' is not editable in bulk")
    label, col_type = meta

    if not record_ids:
        raise ValueError("No records selected")

    parsed, err = _parse_cell(raw_value, field, label, col_type, 0)
    if err is not None:
        raise ValueError(err.reason)

    result = BulkUpdateResult()

    records = (
        await db.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.id.in_(record_ids)
            )
        )
    ).scalars().all()
    found_ids = {r.id for r in records}
    result.not_found = len([rid for rid in set(record_ids) if rid not in found_ids])

    actor = get_audit_actor_email() or SYSTEM_ACTOR
    recorder = CompanyAuditRecorder(db)

    # None of the three bulk-editable fields feed the derived columns, so no
    # aggregate recompute is needed (unlike the cost/fmv PATCH path).
    # (deal_id, cycle_id) for rows whose scoping_for_audit flipped False → True.
    newly_scoped_deals: list[tuple[str, Optional[str]]] = []
    # (deal_id, cycle_id, field_changes) staged for audit after the flush.
    pending_audit_rows: list[tuple[str, Optional[str], dict]] = []

    for rec in records:
        # Stage 2 is locked unless Stage 1 reports completion — skip those rows.
        if field == "deal_level_stage_2" and (rec.deal_level_stage_1 or "") not in _STAGE_2_ENABLED_STAGE_1:
            result.skipped += 1
            continue

        current = getattr(rec, field, None)
        current_s = "" if current is None else str(current).strip()
        new_s = "" if parsed is None else str(parsed).strip()
        if current_s == new_s:
            result.skipped += 1
            continue

        setattr(rec, field, parsed)
        result.updated += 1
        pending_audit_rows.append(
            (rec.deal_id, rec.review_cycle_id, {field: (current, parsed)})
        )
        if field == "scoping_for_audit" and parsed is True and not current:
            newly_scoped_deals.append((rec.deal_id, rec.review_cycle_id))

    if result.updated == 0:
        return result

    await db.flush()

    for deal_id, cycle_id, field_changes in pending_audit_rows:
        pc_stmt = select(PortfolioCompany.id).where(
            PortfolioCompany.company_id == deal_id,
            PortfolioCompany.review_cycle_id == cycle_id,
        )
        pc_id = (await db.execute(pc_stmt)).scalars().first()
        if pc_id:
            await recorder.log_company_field_changes(
                portfolio_company_id=pc_id,
                changes=field_changes,
                entity_type="portfolio_company_metadata",
                actor_email=actor,
            )

    # scoping_for_audit False → True: advance entities Not applicable → Financials to be received.
    for deal_id, cycle_id in newly_scoped_deals:
        pc_stmt = select(PortfolioCompany.id).where(PortfolioCompany.company_id == deal_id)
        if cycle_id:
            pc_stmt = pc_stmt.where(PortfolioCompany.review_cycle_id == cycle_id)
        pc_ids = (await db.execute(pc_stmt)).scalars().all()
        if not pc_ids:
            continue
        ent_stmt = select(Entity).where(
            Entity.portfolio_company_id.in_(pc_ids),
            or_(
                Entity.status == CompanyReviewStage.NOT_APPLICABLE.value,
                Entity.status.is_(None),
            ),
        )
        for ent in (await db.execute(ent_stmt)).scalars().all():
            before_status = ent.status
            ent.status = CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value
            for pc_id in pc_ids:
                if ent.portfolio_company_id == pc_id:
                    await recorder.log_company_field_changes(
                        portfolio_company_id=pc_id,
                        changes={"status": (before_status, CompanyReviewStage.FINANCIALS_TO_BE_RECEIVED.value)},
                        entity_type="entity",
                        entity_id=ent.id,
                        actor_email=actor,
                    )
                    break

    await db.commit()
    return result


def _parse_cell(
    raw: Any,
    field: str,
    label: str,
    col_type: str,
    row_num: int,
) -> tuple[Optional[Any], Optional[UploadRowError]]:
    if raw is None:
        return None, None

    s = str(raw).strip()
    if s.lower() in _NA_VALUES:
        return None, None

    if col_type == "number":
        cleaned = s.replace(",", "").replace("%", "").strip()
        try:
            float(cleaned)
            return cleaned, None
        except ValueError:
            return None, UploadRowError(row_num, f"Column '{label}': '{s}' is not a valid number")

    if col_type == "boolean":
        return s.lower() in ("yes", "true", "1"), None

    if col_type in ("enum_stage1", "enum_stage2"):
        enum_cls = DealLevelStage1 if col_type == "enum_stage1" else DealLevelStage2
        valid = {m.value for m in enum_cls}
        if s not in valid:
            allowed = ", ".join(f'"{v}"' for v in valid)
            return None, UploadRowError(row_num, f"Column '{label}': '{s}' is not a valid value. Allowed: {allowed}")
        return s, None

    return s, None
