"""
Tests for the dashboard data export/import service.

Two layers:
  * Pure-logic unit tests (no DB required) — verify column layout, parsing, validation.
  * Integration tests against the configured Postgres DB inside a SAVEPOINT that is
    rolled back, so test data never persists.  Auto-skipped when DB is not reachable.

Run:
    PYTHONPATH=. pytest tests/test_dashboard_data_export.py -v
"""
from __future__ import annotations

import asyncio
import io
import os
import uuid

import pytest

from src.services.dashboard_data_export import (
    _EDITABLE_COLS,
    _ID_COLS,
    _IDENTITY_COLS,
    _NA_VALUES,
    _parse_cell,
    _xlsx_col_headers,
    UploadResult,
    UploadRowError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_xlsx(headers: list[str], data_rows: list[list]) -> bytes:
    """Build a minimal XLSX bytes object with a 'Dashboard Data' sheet."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dashboard Data"
    ws.append(headers)
    for row in data_rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _full_headers() -> list[str]:
    return _xlsx_col_headers()


def _minimal_data_row(
    pc_id: int = 1,
    entity_id: int = 10,
    review_cycle_id: str = "CY25-FY26",
) -> list:
    headers = _full_headers()
    row = []
    for h in headers:
        if h == "portfolio_company_id":
            row.append(pc_id)
        elif h == "entity_id":
            row.append(entity_id)
        elif h == "review_cycle_id":
            row.append(review_cycle_id)
        elif h == "company_id":
            row.append("C001")
        elif h == "review_cycle":
            row.append("FY 2025-26")
        elif h == "deal/company name":
            row.append("Test Co")
        elif h == "entity name":
            row.append("Test Entity")
        elif h == "status":
            row.append("In review")
        elif h == "entity type":
            row.append("Holding")
        elif h == "geolocation":
            row.append("India")
        else:
            row.append("")
    return row


# ---------------------------------------------------------------------------
# 1. Column layout tests (pure logic, no DB)
# ---------------------------------------------------------------------------

class TestColumnLayout:
    def test_id_cols_come_first(self):
        headers = _full_headers()
        id_headers = [h for _, h in _ID_COLS]
        assert headers[: len(id_headers)] == id_headers

    def test_identity_cols_follow_id_cols(self):
        headers = _full_headers()
        n_id = len(_ID_COLS)
        identity_headers = [h for _, h in _IDENTITY_COLS]
        assert headers[n_id: n_id + len(identity_headers)] == identity_headers

    def test_editable_cols_at_end(self):
        headers = _full_headers()
        editable_labels = [label for _, label, _ in _EDITABLE_COLS]
        assert headers[-len(editable_labels):] == editable_labels

    def test_required_base_columns_present(self):
        headers = _full_headers()
        required = [
            "portfolio_company_id", "entity_id", "review_cycle_id",
            "company_id", "review_cycle", "deal/company name",
            "entity name", "status", "entity type", "geolocation",
        ]
        for col in required:
            assert col in headers, f"Missing required column: {col}"

    def test_editable_fields_count(self):
        # All EDITABLE_FIELDS from the frontend should be present
        expected_fields = {
            "contact_name", "contact_email_id", "fund", "investment_lead",
            "company_stage", "geography", "ownership_pct", "cost", "fmv",
            "position_is_unique", "consolidated_ownership_pct",
            "consolidated_cost", "consolidated_fmv", "company_category_1",
            "company_category_2", "scoped_in_for_audit", "exclusion_reason",
            "fy_end_date", "due_date", "audit_status", "auditor",
            "tentative_completion_date", "company_response", "peak_xv_actionable",
            "reason_to_scope_out",
        }
        actual_fields = {field for field, _, _ in _EDITABLE_COLS}
        assert actual_fields == expected_fields

    def test_reason_to_scope_out_in_headers(self):
        headers = _full_headers()
        assert "Reason to Scope Out" in headers

    def test_reason_to_scope_out_is_editable_col(self):
        fields = [field for field, _, _ in _EDITABLE_COLS]
        assert "reason_to_scope_out" in fields


# ---------------------------------------------------------------------------
# 2. Cell parsing tests (pure logic, no DB)
# ---------------------------------------------------------------------------

class TestParseCell:
    def test_none_returns_none(self):
        val, err = _parse_cell(None, "cost", "Cost", "number", 2)
        assert val is None and err is None

    def test_na_string_returns_none(self):
        for na in ["NA", "N/A", "None", "null", "-", "—", "", "na"]:
            val, err = _parse_cell(na, "cost", "Cost", "number", 2)
            assert val is None, f"Expected None for {na!r}"
            assert err is None

    def test_valid_number_accepted(self):
        val, err = _parse_cell("1250000", "cost", "Cost", "number", 2)
        assert err is None
        assert val == "1250000"

    def test_number_with_commas_accepted(self):
        val, err = _parse_cell("1,250,000", "cost", "Cost", "number", 2)
        assert err is None
        assert val == "1250000"

    def test_invalid_number_returns_error(self):
        val, err = _parse_cell("abc", "cost", "Cost", "number", 2)
        assert val is None
        assert err is not None
        assert err.row_num == 2
        assert "Cost" in err.reason

    def test_text_field_returned_as_is(self):
        val, err = _parse_cell("  Acme Corp  ", "fund", "Fund", "text", 3)
        assert err is None
        assert val == "Acme Corp"

    def test_date_field_accepted_as_string(self):
        val, err = _parse_cell("2026-03-31", "fy_end_date", "FY End", "date", 4)
        assert err is None
        assert val == "2026-03-31"

    def test_empty_text_returns_none(self):
        val, err = _parse_cell("", "fund", "Fund", "text", 2)
        assert val is None and err is None


# ---------------------------------------------------------------------------
# 3. UploadRowError / UploadResult serialization (pure logic)
# ---------------------------------------------------------------------------

class TestUploadResult:
    def test_to_dict_structure(self):
        r = UploadResult()
        r.rows_processed = 5
        r.rows_updated = 3
        r.rows_skipped = 1
        r.errors.append(UploadRowError(2, "bad value"))
        d = r.to_dict()
        assert d["rows_processed"] == 5
        assert d["rows_updated"] == 3
        assert d["rows_skipped"] == 1
        assert d["error_count"] == 1
        assert d["errors"][0] == {"row": 2, "reason": "bad value"}


# ---------------------------------------------------------------------------
# 4. XLSX parsing validation (pure logic, no DB — we test process_dashboard_upload
#    with a mock DB that we inject)
# ---------------------------------------------------------------------------

class TestUploadValidation:
    """Tests that exercise process_dashboard_upload with a stub async DB session."""

    def _make_mock_db(self, companies=None, cycle_exists=True):
        """Return a minimal async mock for SQLAlchemy AsyncSession."""
        from unittest.mock import AsyncMock, MagicMock

        db = AsyncMock()

        class _ScalarResult:
            def __init__(self, value):
                self._value = value
            def scalar_one_or_none(self):
                return self._value
            def scalars(self):
                return self
            def all(self):
                return self._value if isinstance(self._value, list) else []

        # Build company objects
        company_objs = companies or []

        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            # First call: ReviewCycle lookup
            if call_count == 1:
                from src.db.models import ReviewCycle as RC
                if cycle_exists:
                    rc = MagicMock(spec=RC)
                    rc.id = "CY25-FY26"
                    rc.name = "FY 2025-26"
                    return _ScalarResult(rc)
                return _ScalarResult(None)
            # Second call: PortfolioCompany list for cycle
            return _ScalarResult(company_objs)

        db.execute.side_effect = _execute
        db.commit = AsyncMock()
        return db

    def _make_company(self, pc_id: int, company_id: str = "C001", name: str = "Test Co"):
        from unittest.mock import MagicMock
        from src.db.models import PortfolioCompany
        pc = MagicMock(spec=PortfolioCompany)
        pc.id = pc_id
        pc.company_id = company_id
        pc.name = name
        pc.review_cycle_id = "CY25-FY26"
        for field, _, _ in _EDITABLE_COLS:
            setattr(pc, field, None)
        return pc

    def test_missing_dashboard_data_sheet_raises(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.title = "Wrong Sheet"
        buf = io.BytesIO()
        wb.save(buf)
        from src.services.dashboard_data_export import process_dashboard_upload
        db = self._make_mock_db()
        with pytest.raises(ValueError, match="Dashboard Data"):
            asyncio.run(process_dashboard_upload(db, buf.getvalue()))

    def test_cycle_not_in_db_raises(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        db = self._make_mock_db(cycle_exists=False)
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(process_dashboard_upload(db, xlsx))

    def test_unknown_portfolio_company_id_recorded_as_error(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=999, review_cycle_id="CY25-FY26")
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        # No companies in DB for this cycle
        db = self._make_mock_db(companies=[])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count > 0
        assert any("999" in e["reason"] for e in result.to_dict()["errors"])

    def test_mismatched_expected_cycle_raises(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        db = self._make_mock_db()
        with pytest.raises(ValueError, match="does not match"):
            asyncio.run(process_dashboard_upload(db, xlsx, expected_review_cycle_id="CY26-FY27"))

    def test_multiple_cycles_in_file_raises(self):
        headers = _full_headers()
        row1 = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        row2 = _minimal_data_row(pc_id=2, review_cycle_id="CY26-FY27")
        xlsx = _make_xlsx(headers, [row1, row2])
        from src.services.dashboard_data_export import process_dashboard_upload
        db = self._make_mock_db()
        with pytest.raises(ValueError, match="multiple review cycles"):
            asyncio.run(process_dashboard_upload(db, xlsx))

    def test_invalid_number_produces_error_not_update(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        cost_idx = headers.index("Cost")
        row[cost_idx] = "not-a-number"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count > 0
        db.commit.assert_not_called()

    def test_immutable_company_name_changed_produces_error(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        name_idx = headers.index("deal/company name")
        row[name_idx] = "Tampered Name"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1, name="Test Co")  # original name
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count > 0
        assert any("Tampered" in e["reason"] or "deal/company name" in e["reason"]
                   for e in result.to_dict()["errors"])

    def test_valid_row_updates_company(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        fund_idx = headers.index("Fund")
        row[fund_idx] = "Growth Fund III"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count == 0
        assert result.rows_updated == 1
        assert pc.fund == "Growth Fund III"
        db.commit.assert_called_once()

    def test_no_change_row_is_skipped(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        # All editable fields are blank → same as None in DB
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count == 0
        assert result.rows_skipped == 1
        assert result.rows_updated == 0

    def test_na_value_clears_field(self):
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        fund_idx = headers.index("Fund")
        row[fund_idx] = "NA"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        pc.fund = "Old Fund"  # pre-existing value
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count == 0
        assert result.rows_updated == 1
        assert pc.fund is None

    def test_does_not_create_new_company(self):
        """An unknown pc_id must produce an error, not a new PortfolioCompany."""
        headers = _full_headers()
        row = _minimal_data_row(pc_id=9999, review_cycle_id="CY25-FY26")
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        db = self._make_mock_db(companies=[])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        # Must have an error and must NOT have committed
        assert result.error_count > 0
        db.commit.assert_not_called()

    def test_reason_to_scope_out_upload_sets_field(self):
        """Upload with 'Reason to Scope Out' value persists to reason_to_scope_out."""
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        col_idx = headers.index("Reason to Scope Out")
        row[col_idx] = "Not a priority this cycle"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count == 0
        assert result.rows_updated == 1
        assert pc.reason_to_scope_out == "Not a priority this cycle"

    def test_reason_to_scope_out_blank_clears_field(self):
        """Blank 'Reason to Scope Out' clears an existing value (saves None)."""
        headers = _full_headers()
        row = _minimal_data_row(pc_id=1, review_cycle_id="CY25-FY26")
        col_idx = headers.index("Reason to Scope Out")
        row[col_idx] = "NA"
        xlsx = _make_xlsx(headers, [row])
        from src.services.dashboard_data_export import process_dashboard_upload
        pc = self._make_company(1)
        pc.reason_to_scope_out = "Old reason"
        db = self._make_mock_db(companies=[pc])
        result = asyncio.run(process_dashboard_upload(db, xlsx))
        assert result.error_count == 0
        assert result.rows_updated == 1
        assert pc.reason_to_scope_out is None


# ---------------------------------------------------------------------------
# 5. Download builder (pure logic — build XLSX and inspect it)
# ---------------------------------------------------------------------------

class TestBuildDashboardXlsx:
    def test_single_cycle_only(self):
        """Only companies for the requested cycle appear in the output."""
        from unittest.mock import AsyncMock, MagicMock
        from src.db.models import PortfolioCompany, Entity, ReviewCycle

        db = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            class _R:
                def __init__(self, v): self._v = v
                def scalar_one_or_none(self): return self._v
                def scalars(self): return self
                def all(self): return self._v if isinstance(self._v, list) else []

            if call_count == 1:
                rc = MagicMock(spec=ReviewCycle)
                rc.id = "CY25-FY26"
                rc.name = "FY 2025-26"
                return _R(rc)
            if call_count == 2:
                pc = MagicMock(spec=PortfolioCompany)
                pc.id = 1
                pc.company_id = "C001"
                pc.name = "Acme Ltd"
                pc.review_cycle_id = "CY25-FY26"
                pc.review_stage = "In Review"
                for field, _, _ in _EDITABLE_COLS:
                    setattr(pc, field, None)
                return _R([pc])
            # Entities
            ent = MagicMock(spec=Entity)
            ent.id = 10
            ent.portfolio_company_id = 1
            ent.name = "Acme India"
            ent.entity_type = "Holding"
            ent.geolocation = "India"
            ent.status = "In review"
            return _R([ent])

        db.execute.side_effect = _execute

        from src.services.dashboard_data_export import build_dashboard_xlsx

        async def _run():
            return await build_dashboard_xlsx(db, "CY25-FY26")

        xlsx_bytes, filename = asyncio.run(_run())

        assert filename.startswith("dashboard_data_CY25-FY26")
        assert filename.endswith(".xlsx")

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
        assert "Dashboard Data" in wb.sheetnames
        assert "Instructions" in wb.sheetnames

        ws = wb["Dashboard Data"]
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()

        # Header row + 1 data row
        assert len(all_rows) == 2
        headers = [str(h) if h is not None else "" for h in all_rows[0]]
        assert "portfolio_company_id" in headers
        assert "entity_id" in headers
        assert "review_cycle_id" in headers
        assert "deal/company name" in headers
        assert "entity name" in headers

        data_row = all_rows[1]
        rc_id_idx = headers.index("review_cycle_id")
        assert str(data_row[rc_id_idx]) == "CY25-FY26"

    def test_no_entities_produces_one_row_per_company(self):
        from unittest.mock import AsyncMock, MagicMock
        from src.db.models import PortfolioCompany, ReviewCycle

        db = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            class _R:
                def __init__(self, v): self._v = v
                def scalar_one_or_none(self): return self._v
                def scalars(self): return self
                def all(self): return self._v if isinstance(self._v, list) else []

            if call_count == 1:
                rc = MagicMock(spec=ReviewCycle)
                rc.id = "CY25-FY26"
                rc.name = "FY 2025-26"
                return _R(rc)
            if call_count == 2:
                pcs = []
                for i in range(3):
                    pc = MagicMock(spec=PortfolioCompany)
                    pc.id = i + 1
                    pc.company_id = f"C00{i+1}"
                    pc.name = f"Company {i+1}"
                    pc.review_cycle_id = "CY25-FY26"
                    pc.review_stage = "Created"
                    for field, _, _ in _EDITABLE_COLS:
                        setattr(pc, field, None)
                    pcs.append(pc)
                return _R(pcs)
            # No entities
            return _R([])

        db.execute.side_effect = _execute

        from src.services.dashboard_data_export import build_dashboard_xlsx

        async def _run():
            return await build_dashboard_xlsx(db, "CY25-FY26")

        xlsx_bytes, _ = asyncio.run(_run())

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
        ws = wb["Dashboard Data"]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        # 1 header + 3 company rows (one each, no entities)
        assert len(rows) == 4


# ---------------------------------------------------------------------------
# 6. DB integration tests (rolled back via SAVEPOINT)
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    if os.environ.get("SKIP_DB_TESTS"):
        return False
    try:
        from sqlalchemy import text
        from src.db.session import sync_engine
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_skip_no_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="Configured Postgres DB not reachable; skipping integration tests",
)


@pytest.fixture
def sync_db_session():
    """Sync session bound to a SAVEPOINT; rolled back after the test."""
    from sqlalchemy.orm import Session
    from sqlalchemy import event
    from src.db.session import sync_engine

    connection = sync_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _seed_cycle(session, cycle_id: str = "CY25-FY26"):
    from src.db.models import ReviewCycle
    rc = ReviewCycle(id=cycle_id, name=f"Test {cycle_id}", status="active", meta={})
    session.add(rc)
    session.flush()
    return rc


def _seed_company(session, cycle_id: str, suffix: str = "A") -> "PortfolioCompany":
    from src.db.models import PortfolioCompany
    pc = PortfolioCompany(
        company_id=f"TEST-{suffix}-{uuid.uuid4().hex[:6]}",
        name=f"Integration Test Co {suffix}",
        review_cycle_id=cycle_id,
        review_stage="Created",
        fund="Test Fund",
        cost="500000",
    )
    session.add(pc)
    session.flush()
    return pc


def _seed_entity(session, company_id: int, suffix: str = "E1") -> "Entity":
    from src.db.models import Entity
    ent = Entity(
        portfolio_company_id=company_id,
        name=f"Entity {suffix}",
        entity_type="Holding",
        geolocation="India",
    )
    session.add(ent)
    session.flush()
    return ent


@_skip_no_db
class TestDashboardDataIntegration:
    """End-to-end integration: seed DB → download → mutate → upload → verify."""

    def _wrap_sync_session_as_async(self, sync_session):
        """Wraps a sync SQLAlchemy session to look like an async session for service calls."""
        from unittest.mock import AsyncMock, MagicMock

        db = MagicMock()
        db.commit = AsyncMock(side_effect=lambda: sync_session.flush())

        async def _execute(stmt, *a, **kw):
            result = sync_session.execute(stmt)
            class _Wrap:
                def __init__(self, r): self._r = r
                def scalar_one_or_none(self): return self._r.scalar_one_or_none()
                def scalars(self): return self
                def all(self): return self._r.scalars().all()
            return _Wrap(result)

        db.execute.side_effect = _execute
        return db

    def test_download_only_contains_requested_cycle(self, sync_db_session):
        cycle_a = _seed_cycle(sync_db_session, "CY25-FY26")
        cycle_b = _seed_cycle(sync_db_session, "CY26-FY27")
        pc_a = _seed_company(sync_db_session, cycle_a.id, "A")
        pc_b = _seed_company(sync_db_session, cycle_b.id, "B")
        _seed_entity(sync_db_session, pc_a.id, "E1")
        _seed_entity(sync_db_session, pc_b.id, "E2")

        db = self._wrap_sync_session_as_async(sync_db_session)
        from src.services.dashboard_data_export import build_dashboard_xlsx

        async def _run():
            return await build_dashboard_xlsx(db, cycle_a.id)

        xlsx_bytes, filename = asyncio.run(_run())

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
        ws = wb["Dashboard Data"]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()

        headers = [str(h) if h is not None else "" for h in rows[0]]
        rc_id_col = headers.index("review_cycle_id")
        data_rows = rows[1:]
        cycle_ids_in_file = {str(r[rc_id_col]) for r in data_rows}
        assert cycle_ids_in_file == {cycle_a.id}, (
            f"Expected only {cycle_a.id!r}, got {cycle_ids_in_file}"
        )
        assert pc_b.name not in [str(r[headers.index("deal/company name")]) for r in data_rows]

    def test_upload_updates_existing_records(self, sync_db_session):
        cycle = _seed_cycle(sync_db_session, "CY25-FY26")
        pc = _seed_company(sync_db_session, cycle.id, "UPD")
        _seed_entity(sync_db_session, pc.id, "E1")

        db = self._wrap_sync_session_as_async(sync_db_session)

        from src.services.dashboard_data_export import build_dashboard_xlsx, process_dashboard_upload

        async def _run():
            xlsx_bytes, _ = await build_dashboard_xlsx(db, cycle.id)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
            ws = wb["Dashboard Data"]
            headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
            fund_col = headers.index("Fund") + 1
            for row in ws.iter_rows(min_row=2):
                row[fund_col - 1].value = "Updated Fund"
            buf = io.BytesIO()
            wb.save(buf)
            return await process_dashboard_upload(db, buf.getvalue(), expected_review_cycle_id=cycle.id)

        result = asyncio.run(_run())
        assert result.error_count == 0
        assert result.rows_updated >= 1

        sync_db_session.refresh(pc)
        assert pc.fund == "Updated Fund"

    def test_upload_does_not_create_new_company(self, sync_db_session):
        from src.db.models import PortfolioCompany
        cycle = _seed_cycle(sync_db_session, "CY25-FY26")
        count_before = sync_db_session.query(PortfolioCompany).count()

        # Build XLSX with a fake pc_id that doesn't exist
        headers = _full_headers()
        row = _minimal_data_row(pc_id=999999, review_cycle_id=cycle.id)
        name_idx = headers.index("deal/company name")
        row[name_idx] = "Ghost Co"
        xlsx = _make_xlsx(headers, [row])

        db = self._wrap_sync_session_as_async(sync_db_session)
        from src.services.dashboard_data_export import process_dashboard_upload
        result = asyncio.run(process_dashboard_upload(db, xlsx, expected_review_cycle_id=cycle.id))

        assert result.error_count > 0
        count_after = sync_db_session.query(PortfolioCompany).count()
        assert count_after == count_before

    def test_upload_does_not_affect_other_cycles(self, sync_db_session):
        cycle_a = _seed_cycle(sync_db_session, "CY25-FY26")
        cycle_b = _seed_cycle(sync_db_session, "CY26-FY27")
        pc_a = _seed_company(sync_db_session, cycle_a.id, "A")
        pc_b = _seed_company(sync_db_session, cycle_b.id, "B")
        original_fund_b = pc_b.fund

        db = self._wrap_sync_session_as_async(sync_db_session)
        from src.services.dashboard_data_export import build_dashboard_xlsx, process_dashboard_upload

        async def _run():
            xlsx_bytes, _ = await build_dashboard_xlsx(db, cycle_a.id)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
            ws = wb["Dashboard Data"]
            headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
            fund_col_idx = headers.index("Fund")
            for row in ws.iter_rows(min_row=2):
                row[fund_col_idx].value = "Cycle A Fund"
            buf = io.BytesIO()
            wb.save(buf)
            return await process_dashboard_upload(db, buf.getvalue(), expected_review_cycle_id=cycle_a.id)

        result = asyncio.run(_run())
        assert result.error_count == 0

        sync_db_session.refresh(pc_b)
        assert pc_b.fund == original_fund_b, "Other cycle's company should not be modified"

    def test_upload_rejects_unknown_entity_not_crash(self, sync_db_session):
        """Unknown entity_id in upload should not crash — entity_id is not used for matching,
        but upload should still process and update the company row."""
        cycle = _seed_cycle(sync_db_session, "CY25-FY26")
        pc = _seed_company(sync_db_session, cycle.id, "ENT")

        db = self._wrap_sync_session_as_async(sync_db_session)
        from src.services.dashboard_data_export import build_dashboard_xlsx, process_dashboard_upload

        async def _run():
            xlsx_bytes, _ = await build_dashboard_xlsx(db, cycle.id)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
            ws = wb["Dashboard Data"]
            headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
            eid_col_idx = headers.index("entity_id")
            for row in ws.iter_rows(min_row=2):
                row[eid_col_idx].value = 99999  # unknown entity id (not used for matching)
            buf = io.BytesIO()
            wb.save(buf)
            return await process_dashboard_upload(db, buf.getvalue(), expected_review_cycle_id=cycle.id)

        # Should succeed — entity_id is informational, matching is by portfolio_company_id
        result = asyncio.run(_run())
        assert result.error_count == 0
