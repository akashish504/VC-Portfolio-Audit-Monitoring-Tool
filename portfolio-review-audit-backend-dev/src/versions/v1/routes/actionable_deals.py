"""
Actionable Deals endpoints.

These endpoints expose PortfolioCompanyMetadata rows that still carry the
placeholder values fund="UNASSIGNED" / strategy="UNASSIGNED" written by the
sync script when a new company is discovered.  Until a user fills in real
fund/strategy data (via the XLSX upload flow), the company surfaces here so
someone can take action.

GET  /api/v1/actionable-deals/count    → { count: N }  (distinct deal_ids)
GET  /api/v1/actionable-deals          → list of unresolved deals
GET  /api/v1/actionable-deals/download → XLSX template
POST /api/v1/actionable-deals/upload   → resolve deals (override dummy, upsert real rows)
DELETE /api/v1/actionable-deals/{deal_id} → delete ALL placeholder rows for a deal_id
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, File as FastAPIFile, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PortfolioCompanyMetadata
from src.db.session import get_db
from src.services.master_scoping_export import (
    _EDITABLE_COLS,
    _KEY_COLS,
    _cell,
    _parse_cell,
    UploadResult,
    UploadRowError,
    recompute_aggregates,
)

router = APIRouter()

_PLACEHOLDER_FUND = "UNASSIGNED"
_PLACEHOLDER_STRATEGY = "UNASSIGNED"


def _is_placeholder(rec: PortfolioCompanyMetadata) -> bool:
    return rec.fund == _PLACEHOLDER_FUND and rec.strategy == _PLACEHOLDER_STRATEGY


def _serialize(rec: PortfolioCompanyMetadata) -> dict:
    return {
        "id": rec.id,
        "deal_id": rec.deal_id,
        "deal_name": rec.deal_name,
        "fund": rec.fund,
        "strategy": rec.strategy,
        "review_cycle_id": rec.review_cycle_id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


def _placeholder_stmt():
    return select(PortfolioCompanyMetadata).where(
        PortfolioCompanyMetadata.fund == _PLACEHOLDER_FUND,
        PortfolioCompanyMetadata.strategy == _PLACEHOLDER_STRATEGY,
    )


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------

@router.get("/actionable-deals/count", tags=["Actionable Deals"])
async def get_actionable_deals_count(db: AsyncSession = Depends(get_db)) -> dict:
    """Return the number of distinct deal_ids with unresolved placeholder rows."""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(PortfolioCompanyMetadata.deal_id.distinct())).where(
            PortfolioCompanyMetadata.fund == _PLACEHOLDER_FUND,
            PortfolioCompanyMetadata.strategy == _PLACEHOLDER_STRATEGY,
        )
    )
    count = result.scalar_one() or 0
    return {"count": count}


# ---------------------------------------------------------------------------
# List  (one row per deal_id — use the lowest-id placeholder as representative)
# ---------------------------------------------------------------------------

@router.get("/actionable-deals", tags=["Actionable Deals"])
async def list_actionable_deals(db: AsyncSession = Depends(get_db)) -> dict:
    """Return all placeholder rows (one per deal_id+cycle), ordered by deal_name."""
    rows = (
        await db.execute(
            _placeholder_stmt().order_by(
                PortfolioCompanyMetadata.deal_name,
                PortfolioCompanyMetadata.deal_id,
                PortfolioCompanyMetadata.review_cycle_id,
            )
        )
    ).scalars().all()

    # Deduplicate by deal_id — keep the first (lowest id) representative row per deal.
    seen: set[str] = set()
    items = []
    for r in rows:
        if r.deal_id not in seen:
            seen.add(r.deal_id)
            items.append(_serialize(r))

    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Download XLSX template
# ---------------------------------------------------------------------------

@router.get("/actionable-deals/download", tags=["Actionable Deals"])
async def download_actionable_deals(db: AsyncSession = Depends(get_db)) -> Response:
    """Download an XLSX with all unresolved deals — user fills in fund/strategy and uploads."""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openpyxl is not installed") from exc

    rows_db = (
        await db.execute(
            _placeholder_stmt().order_by(
                PortfolioCompanyMetadata.deal_name,
                PortfolioCompanyMetadata.deal_id,
                PortfolioCompanyMetadata.review_cycle_id,
            )
        )
    ).scalars().all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Actionable Deals"

    hdr_font = Font(bold=True, color="FFFFFF")
    key_fill = PatternFill("solid", fgColor="1D4ED8")       # blue  — key cols
    editable_fill = PatternFill("solid", fgColor="15803D")  # green — editable
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Key columns + a subset of editable columns (fund and strategy are the critical ones)
    key_headers = ["Deal ID", "Deal Name", "Review Cycle ID"]
    # Editable headers the user needs to fill in (fund + strategy first, then rest)
    editable_headers = ["Fund", "Strategy"] + [
        label for field, label, _ in _EDITABLE_COLS
        if field not in ("deal_name", "review_cycle_id")
    ]

    all_headers = key_headers + editable_headers
    n_key = len(key_headers)

    for ci, h in enumerate(all_headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hdr_font
        cell.alignment = hdr_align
        cell.fill = key_fill if ci <= n_key else editable_fill

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = get_column_letter(n_key + 1) + "2"

    editable_field_map = {label: field for field, label, _ in _EDITABLE_COLS}
    editable_field_map["Fund"] = "fund"
    editable_field_map["Strategy"] = "strategy"

    for rec in rows_db:
        row: list[Any] = [rec.deal_id, rec.deal_name, rec.review_cycle_id or ""]
        for h in editable_headers:
            field = editable_field_map.get(h)
            if field:
                v = getattr(rec, field, None)
                if isinstance(v, bool):
                    row.append("Yes" if v else "No")
                else:
                    # Show empty string for placeholder values so user knows to fill in
                    if v in (_PLACEHOLDER_FUND, _PLACEHOLDER_STRATEGY):
                        row.append("")
                    else:
                        row.append(v if v is not None else "")
            else:
                row.append("")
        ws.append(row)

    for ci, h in enumerate(all_headers, 1):
        ws.column_dimensions[get_column_letter(ci)].width = max(14, min(40, len(h) + 4))

    # Instructions sheet
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["Column", "Notes"])
    ws2.append(["Deal ID", "KEY — do not change"])
    ws2.append(["Deal Name", "KEY — do not change"])
    ws2.append(["Review Cycle ID", "KEY — do not change"])
    ws2.append(["Fund", "REQUIRED — fill in the real fund name"])
    ws2.append(["Strategy", "REQUIRED — fill in the real strategy"])
    ws2.append(["", ""])
    ws2.append(["NOTES", ""])
    ws2.append(["1. Fill in Fund and Strategy (at minimum) for each row."])
    ws2.append(["2. Upload this file back via the 'Upload' button."])
    ws2.append(["3. The system will replace placeholder rows with your real data."])
    ws2.append(["4. If a deal spans multiple review cycles you will see one row per cycle — fill each."])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"actionable_deals_{date.today()}.xlsx"
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Upload — resolve placeholder rows
# ---------------------------------------------------------------------------

@router.post("/actionable-deals/upload", tags=["Actionable Deals"])
async def upload_actionable_deals(
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Parse the filled XLSX.  For each row:
    1. Delete all placeholder rows for that deal_id (fund=UNASSIGNED, strategy=UNASSIGNED).
    2. Upsert the real row via the (fund, deal_id, strategy, review_cycle_id) unique key.
    If a deal appears multiple times in the sheet (e.g. two funds), the first hit deletes
    the placeholder; subsequent rows for the same deal just upsert cleanly.
    """
    content_type = (file.content_type or "").lower()
    valid_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/octet-stream",
    }
    filename_lower = (file.filename or "").lower()
    if content_type not in valid_types and not filename_lower.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Uploaded file must be an Excel workbook (.xlsx)")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    try:
        import openpyxl
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openpyxl is not installed") from exc

    result = UploadResult()

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    sheet_name = "Actionable Deals" if "Actionable Deals" in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]
    sheet_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not sheet_rows:
        raise HTTPException(status_code=422, detail="Uploaded sheet is empty")

    raw_headers = [str(h).strip() if h is not None else "" for h in sheet_rows[0]]
    header_index: dict[str, int] = {h: i for i, h in enumerate(raw_headers)}

    required = ["Deal ID", "Deal Name", "Review Cycle ID", "Fund", "Strategy"]
    missing = [h for h in required if h not in header_index]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required columns: {', '.join(missing)}")

    # Build a map from header label → (field_name, col_type) for all editable cols
    editable_meta: dict[str, tuple[str, str]] = {
        label: (field, col_type) for field, label, col_type in _EDITABLE_COLS
        if field not in ("deal_name", "review_cycle_id")
    }
    editable_meta["Fund"] = ("fund", "text")
    editable_meta["Strategy"] = ("strategy", "text")

    # Parse rows
    pending: list[dict[str, Any]] = []
    for row_num, dr in enumerate(sheet_rows[1:], start=2):
        result.rows_processed += 1

        deal_id = _cell(dr[header_index["Deal ID"]])
        deal_name = _cell(dr[header_index["Deal Name"]])
        review_cycle_id = _cell(dr[header_index["Review Cycle ID"]])
        fund = _cell(dr[header_index["Fund"]])
        strategy = _cell(dr[header_index["Strategy"]])

        if not deal_id:
            result.errors.append(UploadRowError(row_num, "Deal ID is required"))
            continue
        if not fund:
            result.errors.append(UploadRowError(row_num, "Fund is required"))
            continue
        if not strategy:
            result.errors.append(UploadRowError(row_num, "Strategy is required"))
            continue
        if fund == _PLACEHOLDER_FUND or strategy == _PLACEHOLDER_STRATEGY:
            result.errors.append(UploadRowError(row_num, "Fund and Strategy must not be 'UNASSIGNED' — fill in real values"))
            continue

        field_updates: dict[str, Any] = {
            "fund": fund,
            "strategy": strategy,
            "deal_name": deal_name or deal_id,
            "review_cycle_id": review_cycle_id or None,
        }

        row_error: Optional[UploadRowError] = None
        for label, (field, col_type) in editable_meta.items():
            if label in ("Fund", "Strategy") or label not in header_index:
                continue
            raw = dr[header_index[label]]
            parsed, err = _parse_cell(raw, field, label, col_type, row_num)
            if err:
                row_error = err
                break
            field_updates[field] = parsed

        if row_error:
            result.errors.append(row_error)
            continue

        pending.append({"deal_id": deal_id, "review_cycle_id": review_cycle_id or None, **field_updates})

    if result.errors:
        return result.to_dict()

    # Collect deal_ids whose placeholders we still need to delete
    deals_to_clean: set[str] = {p["deal_id"] for p in pending}

    # Delete all placeholder rows for affected deal_ids in one shot
    if deals_to_clean:
        await db.execute(
            delete(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.deal_id.in_(list(deals_to_clean)),
                PortfolioCompanyMetadata.fund == _PLACEHOLDER_FUND,
                PortfolioCompanyMetadata.strategy == _PLACEHOLDER_STRATEGY,
            )
        )
        await db.flush()

    # Load existing real rows for upsert
    existing_records = (
        await db.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.deal_id.in_(list(deals_to_clean))
            )
        )
    ).scalars().all()
    existing_map: dict[tuple[str, str, str, Optional[str]], PortfolioCompanyMetadata] = {
        (r.fund, r.deal_id, r.strategy, r.review_cycle_id): r for r in existing_records
    }

    cycles_affected: set[str] = set()

    for p in pending:
        deal_id = p["deal_id"]
        fund = p["fund"]
        strategy = p["strategy"]
        review_cycle_id = p.get("review_cycle_id")
        key = (fund, deal_id, strategy, review_cycle_id)
        rec = existing_map.get(key)

        if rec is None:
            new_rec = PortfolioCompanyMetadata(
                fund=fund,
                deal_id=deal_id,
                strategy=strategy,
                deal_name=p.get("deal_name") or deal_id,
                **{k: v for k, v in p.items() if k not in ("fund", "deal_id", "strategy", "deal_name")},
            )
            db.add(new_rec)
            existing_map[key] = new_rec
            result.rows_inserted += 1
        else:
            changed = False
            for field, val in p.items():
                if field in ("fund", "deal_id", "strategy"):
                    continue
                current = getattr(rec, field, None)
                if str(current or "") != str(val or ""):
                    setattr(rec, field, val)
                    changed = True
            if changed:
                result.rows_updated += 1
            else:
                result.rows_skipped += 1

        if review_cycle_id:
            cycles_affected.add(review_cycle_id)

    await db.flush()

    for cycle_id in cycles_affected:
        await recompute_aggregates(db, cycle_id)

    await db.commit()
    return result.to_dict()


# ---------------------------------------------------------------------------
# Delete — remove all placeholder rows for a deal_id
# ---------------------------------------------------------------------------

@router.delete("/actionable-deals/{deal_id}", tags=["Actionable Deals"], status_code=204)
async def delete_actionable_deal(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete all UNASSIGNED placeholder rows for the given deal_id."""
    result = await db.execute(
        delete(PortfolioCompanyMetadata).where(
            PortfolioCompanyMetadata.deal_id == deal_id,
            PortfolioCompanyMetadata.fund == _PLACEHOLDER_FUND,
            PortfolioCompanyMetadata.strategy == _PLACEHOLDER_STRATEGY,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No actionable deal placeholder found for deal_id '{deal_id}'")
    await db.commit()
