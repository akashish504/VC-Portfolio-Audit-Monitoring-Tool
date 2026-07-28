"""
Dashboard data export/import service.

Download: builds an XLSX workbook representing the current dashboard table for a
          given review cycle, one row per entity within each company.
Upload:   parses an XLSX produced by the download, validates it, and updates
          existing PortfolioCompany records.  Never creates new companies,
          entities, or review cycles.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Entity, PortfolioCompany, PortfolioCompanyMetadata, ReviewCycle

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# Hidden ID columns (first, not editable by user)
_ID_COLS: list[tuple[str, str]] = [
    ("portfolio_company_id", "portfolio_company_id"),
    ("entity_id", "entity_id"),
    ("review_cycle_id", "review_cycle_id"),
]

# Visible identity/context columns (not editable)
_IDENTITY_COLS: list[tuple[str, str]] = [
    ("company_id", "company_id"),
    ("review_cycle", "review_cycle"),
    ("deal_company_name", "deal/company name"),
    ("entity_name", "entity name"),
    ("status", "status"),
    ("entity_type", "entity type"),
    ("geolocation", "geolocation"),
]

# Editable dashboard columns: (field_on_portfolio_company, header_label, type)
# Mirrors EDITABLE_FIELDS in ReviewCycleAdjustmentsPage.tsx
_EDITABLE_COLS: list[tuple[str, str, str]] = [
    ("contact_name",               "Contact",              "text"),
    ("contact_email_id",           "Email",                "text"),
    ("fund",                       "Fund",                 "text"),
    ("investment_lead",            "Investment Lead",      "text"),
    ("company_stage",              "Stage (G/V/S)",        "text"),
    ("geography",                  "Geo",                  "text"),
    ("ownership_pct",              "Own %",                "number"),
    ("cost",                       "Cost",                 "number"),
    ("fmv",                        "FMV",                  "number"),
    ("position_is_unique",         "Unique",               "text"),
    ("consolidated_ownership_pct", "Consol. Own %",        "number"),
    ("consolidated_cost",          "Consol. Cost",         "number"),
    ("consolidated_fmv",           "Consol. FMV",          "number"),
    ("company_category_1",         "Category 1",           "text"),
    ("company_category_2",         "Category 2",           "text"),
    ("scoped_in_for_audit",        "Scoped In Audit",      "text"),
    ("exclusion_reason",           "Exclusion Reason",     "text"),
    ("fy_end_date",                "FY End",               "date"),
    ("due_date",                   "Due Date",             "date"),
    ("audit_status",               "Audit Status",         "text"),
    ("auditor",                    "Auditor",              "text"),
    ("tentative_completion_date",  "Tent. Completion",     "date"),
    ("company_response",           "Company Response",     "text"),
    ("peak_xv_actionable",         "Peak XV Actionable",   "text"),
    ("reason_to_scope_out",        "Reason to Scope Out",  "text"),
]

# Column header → (field_name, col_type)
_EDITABLE_HEADER_MAP: dict[str, tuple[str, str]] = {
    label: (field, col_type) for field, label, col_type in _EDITABLE_COLS
}

# Immutable identity header labels — reject if changed in upload
_IMMUTABLE_HEADERS: set[str] = {
    "company_id", "review_cycle", "deal/company name",
    "entity name", "entity_id", "portfolio_company_id", "review_cycle_id",
}

_NA_VALUES = {"na", "n/a", "none", "null", "-", "—", ""}


def _cell(v: Any) -> str:
    """Normalise a cell value to string, mapping blanks/None to empty string."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in _NA_VALUES else s


def _xlsx_col_headers() -> list[str]:
    headers = [h for _, h in _ID_COLS]
    headers += [h for _, h in _IDENTITY_COLS]
    headers += [label for _, label, _ in _EDITABLE_COLS]
    return headers


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def build_dashboard_xlsx(
    db: AsyncSession,
    review_cycle_id: str,
    scoped_in: Optional[bool] = None,
) -> tuple[bytes, str]:
    """
    Return (xlsx_bytes, filename) for companies + entities in the given cycle.

    When ``scoped_in`` is True  → only companies whose deal_id (= company_id)
    appears in PortfolioCompanyMetadata with scoping_for_audit=True for this cycle.
    When ``scoped_in`` is False → companies NOT scoped in (or with no metadata).
    When ``scoped_in`` is None  → all companies (original behaviour).
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc

    # Fetch the review cycle label
    rc_row = (
        await db.execute(select(ReviewCycle).where(ReviewCycle.id == review_cycle_id))
    ).scalar_one_or_none()
    rc_label = (rc_row.name or review_cycle_id) if rc_row else review_cycle_id

    # Determine the set of scoped-in deal_ids for this cycle (from metadata).
    if scoped_in is not None:
        metadata_rows = (
            await db.execute(
                select(PortfolioCompanyMetadata.deal_id)
                .where(PortfolioCompanyMetadata.review_cycle_id == review_cycle_id)
                .where(PortfolioCompanyMetadata.scoping_for_audit.is_(True))
            )
        ).scalars().all()
        scoped_in_deal_ids: set[str] = set(metadata_rows)

    # Fetch companies for this cycle, optionally filtered by scoping.
    companies_stmt = (
        select(PortfolioCompany)
        .where(PortfolioCompany.review_cycle_id == review_cycle_id)
        .order_by(PortfolioCompany.name)
    )
    all_companies = (await db.execute(companies_stmt)).scalars().all()

    if scoped_in is None:
        companies = all_companies
    elif scoped_in:
        companies = [pc for pc in all_companies if (pc.company_id or "") in scoped_in_deal_ids]
    else:
        companies = [pc for pc in all_companies if (pc.company_id or "") not in scoped_in_deal_ids]

    company_ids = [pc.id for pc in companies]

    # Fetch entities for those companies
    entities_by_company: dict[int, list[Entity]] = {pc.id: [] for pc in companies}
    if company_ids:
        entity_rows = (
            await db.execute(
                select(Entity)
                .where(Entity.portfolio_company_id.in_(company_ids))
                .order_by(Entity.portfolio_company_id, Entity.name)
            )
        ).scalars().all()
        for ent in entity_rows:
            entities_by_company[ent.portfolio_company_id].append(ent)

    # Build workbook
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dashboard Data"

    hdr_font = Font(bold=True, color="FFFFFF")
    id_fill = PatternFill("solid", fgColor="4B5563")      # grey for hidden IDs
    identity_fill = PatternFill("solid", fgColor="1D4ED8") # blue for identity
    editable_fill = PatternFill("solid", fgColor="15803D") # green for editable
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    headers = _xlsx_col_headers()
    n_id = len(_ID_COLS)
    n_identity = len(_IDENTITY_COLS)

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hdr_font
        cell.alignment = hdr_align
        if ci <= n_id:
            cell.fill = id_fill
        elif ci <= n_id + n_identity:
            cell.fill = identity_fill
        else:
            cell.fill = editable_fill

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    for pc in companies:
        entities = entities_by_company.get(pc.id, [])
        # If no entities, write one row with blank entity fields
        rows_to_write = entities if entities else [None]

        for ent in rows_to_write:
            row: list[Any] = [
                # ID cols
                pc.id,
                ent.id if ent else "",
                review_cycle_id,
                # Identity cols
                pc.company_id or "",
                rc_label,
                pc.name or "",
                ent.name if ent else "",
                ent.status if ent else "",
                ent.entity_type if ent else "",
                ent.geolocation if ent else "",
            ]
            # Editable cols
            for field, _label, _type in _EDITABLE_COLS:
                v = getattr(pc, field, None)
                row.append(v if v is not None else "")

            ws.append(row)

    # Auto-width (approximate)
    for ci, h in enumerate(headers, 1):
        col_letter = get_column_letter(ci)
        ws.column_dimensions[col_letter].width = max(14, min(40, len(h) + 4))

    # Instructions sheet
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["Column", "Type", "Notes"])
    ws2.append(["portfolio_company_id", "READ-ONLY", "Internal DB id — do not change"])
    ws2.append(["entity_id", "READ-ONLY", "Internal entity id — do not change"])
    ws2.append(["review_cycle_id", "READ-ONLY", "Review cycle id — do not change"])
    ws2.append(["company_id", "READ-ONLY", "Business company identifier — do not change"])
    ws2.append(["review_cycle", "READ-ONLY", "Human-readable cycle label — do not change"])
    ws2.append(["deal/company name", "READ-ONLY", "Company name — do not change"])
    ws2.append(["entity name", "READ-ONLY", "Entity name — do not change"])
    ws2.append(["status", "READ-ONLY", "Entity audit status — use the Audit Tracker UI to change it"])
    ws2.append(["entity type", "READ-ONLY", "Holding / Subsidiary — do not change"])
    ws2.append(["geolocation", "READ-ONLY", "Entity geolocation — do not change"])
    ws2.append(["", "", ""])
    ws2.append(["--- Editable columns below ---", "", ""])
    for field, label, col_type in _EDITABLE_COLS:
        ws2.append([label, col_type.upper(), f"Editable — maps to portfolio_companies.{field}"])

    ws2.append(["", "", ""])
    ws2.append(["NOTES", "", ""])
    ws2.append(["1. Only update rows for the review cycle shown in column C (review_cycle_id)."])
    ws2.append(["2. Do not add or remove rows."])
    ws2.append(["3. Blank / NA values will clear the field."])
    ws2.append(["4. The upload rejects unknown portfolio_company_id or entity_id values."])
    ws2.append(["5. The upload uses portfolio_company_id for matching — do not change it."])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_rc = review_cycle_id.replace("/", "-").replace(" ", "_")
    scoping_suffix = "_scoped_in" if scoped_in is True else ("_scoped_out" if scoped_in is False else "")
    filename = f"dashboard_data_{safe_rc}{scoping_suffix}_{date.today()}.xlsx"
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
        self.rows_skipped = 0
        self.errors: list[UploadRowError] = []

    def to_dict(self) -> dict:
        return {
            "rows_processed": self.rows_processed,
            "rows_updated": self.rows_updated,
            "rows_skipped": self.rows_skipped,
            "error_count": len(self.errors),
            "errors": [e.to_dict() for e in self.errors],
        }


async def process_dashboard_upload(
    db: AsyncSession,
    file_bytes: bytes,
    expected_review_cycle_id: Optional[str] = None,
) -> UploadResult:
    """
    Parse an XLSX produced by build_dashboard_xlsx, validate, and update
    PortfolioCompany records.  Never creates new records.
    All-or-nothing: rolls back on any validation error.
    """
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc

    result = UploadResult()

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    if "Dashboard Data" not in wb.sheetnames:
        raise ValueError("Uploaded file must contain a 'Dashboard Data' sheet")

    ws = wb["Dashboard Data"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        raise ValueError("Sheet 'Dashboard Data' is empty")

    # Parse header row
    raw_headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    header_index: dict[str, int] = {h: i for i, h in enumerate(raw_headers)}

    required = ["portfolio_company_id", "review_cycle_id"] + [label for _, label, _ in _EDITABLE_COLS]
    missing = [h for h in required if h not in header_index]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    # Validate review cycle consistency across the file
    rc_col = header_index["review_cycle_id"]
    file_cycle_ids: set[str] = set()
    for dr in rows[1:]:
        v = str(dr[rc_col]).strip() if dr[rc_col] is not None else ""
        if v:
            file_cycle_ids.add(v)

    if len(file_cycle_ids) > 1:
        raise ValueError(
            f"File contains multiple review cycles: {', '.join(sorted(file_cycle_ids))}. "
            "Upload must be single-cycle."
        )

    file_cycle_id = next(iter(file_cycle_ids), None)
    if not file_cycle_id:
        raise ValueError("No review_cycle_id values found in the file")

    if expected_review_cycle_id and file_cycle_id != expected_review_cycle_id:
        raise ValueError(
            f"File review_cycle_id '{file_cycle_id}' does not match "
            f"expected '{expected_review_cycle_id}'"
        )

    # Verify cycle exists in DB
    rc_exists = (
        await db.execute(select(ReviewCycle).where(ReviewCycle.id == file_cycle_id))
    ).scalar_one_or_none()
    if rc_exists is None:
        raise ValueError(f"Review cycle '{file_cycle_id}' not found in database")

    # Load all company IDs for this cycle into a lookup map
    db_companies = (
        await db.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.review_cycle_id == file_cycle_id
            )
        )
    ).scalars().all()
    company_map: dict[int, PortfolioCompany] = {pc.id: pc for pc in db_companies}

    # Collect updates — validate all rows first (all-or-nothing)
    # Maps portfolio_company_id → dict of field → new_value
    pending_updates: dict[int, dict[str, Any]] = {}

    pc_id_col = header_index["portfolio_company_id"]

    for row_num, dr in enumerate(rows[1:], start=2):
        result.rows_processed += 1

        # Parse portfolio_company_id
        raw_pc_id = dr[pc_id_col]
        if raw_pc_id is None or str(raw_pc_id).strip() == "":
            result.errors.append(UploadRowError(row_num, "Missing portfolio_company_id"))
            continue

        try:
            pc_id = int(float(str(raw_pc_id)))
        except (ValueError, TypeError):
            result.errors.append(UploadRowError(row_num, f"Invalid portfolio_company_id: {raw_pc_id!r}"))
            continue

        if pc_id not in company_map:
            result.errors.append(UploadRowError(
                row_num,
                f"portfolio_company_id {pc_id} not found in review cycle '{file_cycle_id}'"
            ))
            continue

        # Validate immutable identity columns were not changed
        pc = company_map[pc_id]
        _validation_error = _validate_immutable_cols(dr, header_index, pc, file_cycle_id, row_num)
        if _validation_error:
            result.errors.append(_validation_error)
            continue

        # Parse editable fields
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

        if pc_id in pending_updates:
            pending_updates[pc_id].update(field_updates)
        else:
            pending_updates[pc_id] = field_updates

    # Abort entirely if any validation errors
    if result.errors:
        return result

    # Apply all updates in this transaction
    for pc_id, updates in pending_updates.items():
        pc = company_map[pc_id]
        changed = False
        for field, new_val in updates.items():
            current = getattr(pc, field, None)
            # Normalise both to empty string for comparison
            current_s = "" if current is None else str(current).strip()
            new_s = "" if new_val is None else str(new_val).strip()
            if current_s != new_s:
                setattr(pc, field, new_val if new_s else None)
                changed = True
        if changed:
            result.rows_updated += 1
        else:
            result.rows_skipped += 1

    await db.commit()
    return result


def _validate_immutable_cols(
    dr: tuple,
    header_index: dict[str, int],
    pc: PortfolioCompany,
    file_cycle_id: str,
    row_num: int,
) -> Optional[UploadRowError]:
    """Return an error if any immutable identity column was changed."""
    checks = [
        ("company_id", pc.company_id or ""),
        ("deal/company name", pc.name or ""),
    ]
    for col_header, expected in checks:
        if col_header not in header_index:
            continue
        cell_val = _cell(dr[header_index[col_header]])
        if cell_val and cell_val != expected:
            return UploadRowError(
                row_num,
                f"Immutable column '{col_header}' was changed: "
                f"expected '{expected}', got '{cell_val}'"
            )
    return None


def _parse_cell(
    raw: Any,
    field: str,
    label: str,
    col_type: str,
    row_num: int,
) -> tuple[Optional[str], Optional[UploadRowError]]:
    """
    Parse a cell value for an editable field.
    Returns (parsed_value_or_None, error_or_None).
    Empty/NA → None (clears the field).
    """
    if raw is None:
        return None, None

    s = str(raw).strip()
    if s.lower() in _NA_VALUES:
        return None, None

    if col_type == "number":
        # Accept numeric strings, strip commas/percent
        cleaned = s.replace(",", "").replace("%", "").strip()
        try:
            float(cleaned)  # validate it's numeric
            return cleaned, None
        except ValueError:
            return None, UploadRowError(
                row_num, f"Column '{label}': '{s}' is not a valid number"
            )

    if col_type == "date":
        # Accept YYYY-MM-DD or Excel date serial or leave as string
        return s, None

    # text — return as-is
    return s, None
