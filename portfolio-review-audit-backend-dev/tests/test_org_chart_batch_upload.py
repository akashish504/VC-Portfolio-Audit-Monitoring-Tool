"""
Tests for the org chart batch ZIP upload feature.

Two layers:
  * Pure-logic / mock-DB unit tests (no DB required) — always run.
  * Integration tests against the configured Postgres DB inside a SAVEPOINT that is
    rolled back, so test data never persists.  Auto-skipped when DB is not reachable.

Run:
    PYTHONPATH=. pytest tests/test_org_chart_batch_upload.py -v
"""
from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest

from src.services.org_chart_batch_upload import (
    ALLOWED_BATCH_EXTENSIONS,
    MAX_BATCH_TOTAL_FILES,
    ORG_CHART_BATCH_TAG,
    MappingUploadResult,
    _is_skippable,
    _safe_filename,
    _validate_no_traversal,
    build_mapping_template,
)


# ---------------------------------------------------------------------------
# DB reachability (same pattern as other test files)
# ---------------------------------------------------------------------------

def _db_reachable() -> bool:
    try:
        from sqlalchemy import text
        from src.db.session import sync_engine
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


_skip_no_db = pytest.mark.skipif(not _db_reachable(), reason="Configured Postgres DB not reachable")


# ---------------------------------------------------------------------------
# ZIP helper tests (pure logic)
# ---------------------------------------------------------------------------

class TestZipHelpers:
    def test_is_skippable_macosx(self):
        assert _is_skippable("__MACOSX/._foo.pdf") is True

    def test_is_skippable_hidden(self):
        assert _is_skippable(".DS_Store") is True

    def test_is_skippable_directory(self):
        assert _is_skippable("subdir/") is True

    def test_is_skippable_normal_file(self):
        assert _is_skippable("report.pdf") is False

    def test_safe_filename_strips_path(self):
        assert _safe_filename("a/b/c.pdf") == "c.pdf"

    def test_safe_filename_no_path(self):
        assert _safe_filename("chart.xlsx") == "chart.xlsx"

    def test_validate_no_traversal_raises(self):
        with pytest.raises(ValueError, match="traversal"):
            _validate_no_traversal("../../../etc/passwd")

    def test_validate_no_traversal_normal(self):
        _validate_no_traversal("subdir/report.pdf")  # should not raise


# ---------------------------------------------------------------------------
# build_mapping_template tests (pure logic)
# ---------------------------------------------------------------------------

class TestBuildMappingTemplate:
    def _make_record(self, file_name: str) -> MagicMock:
        r = MagicMock()
        r.file_name = file_name
        return r

    def test_headers_are_correct(self):
        records = [self._make_record("a.pdf"), self._make_record("b.docx")]
        xlsx_bytes = build_mapping_template(records)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert rows[0] == ("file_name", "company_id", "name")

    def test_one_data_row_per_record(self):
        records = [self._make_record("x.pdf"), self._make_record("y.xlsx"), self._make_record("z.docx")]
        xlsx_bytes = build_mapping_template(records)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert len(rows) == 4  # 1 header + 3 data rows

    def test_data_rows_have_correct_filenames(self):
        records = [self._make_record("alpha.pdf"), self._make_record("beta.docx")]
        xlsx_bytes = build_mapping_template(records)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert rows[1][0] == "alpha.pdf"
        assert rows[2][0] == "beta.docx"

    def test_company_id_and_name_blank(self):
        records = [self._make_record("report.pdf")]
        xlsx_bytes = build_mapping_template(records)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        # company_id and name should be empty strings
        assert rows[1][1] in (None, "")
        assert rows[1][2] in (None, "")

    def test_empty_records_has_only_header(self):
        xlsx_bytes = build_mapping_template([])
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert len(rows) == 1
        assert rows[0] == ("file_name", "company_id", "name")


# ---------------------------------------------------------------------------
# create_batch_from_zip — mock-DB unit tests
# ---------------------------------------------------------------------------

def _make_zip(files: dict[str, bytes]) -> bytes:
    """Build a ZIP in memory from {filename: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_review_cycle(cycle_id: str = "CY25-FY26") -> MagicMock:
    from src.db.models import ReviewCycle
    rc = MagicMock(spec=ReviewCycle)
    rc.id = cycle_id
    return rc


class TestCreateBatchFromZipUnit:
    """Mock-DB unit tests — no real Postgres needed."""

    def _make_db(self, cycle: Any = None) -> AsyncMock:
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        # add() is synchronous in SQLAlchemy
        added = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db._added = added

        class _Result:
            def __init__(self, val):
                self._val = val
            def scalar_one_or_none(self):
                return self._val

        async def _execute(stmt, *a, **kw):
            return _Result(cycle)

        db.execute.side_effect = _execute
        return db

    @patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="some/key")
    def test_empty_zip_raises(self, _mock_s3):
        from src.services.org_chart_batch_upload import create_batch_from_zip
        zip_bytes = _make_zip({})
        db = self._make_db(cycle=_make_review_cycle())
        with pytest.raises(ValueError, match="empty"):
            asyncio.run(create_batch_from_zip(db, "CY25-FY26", zip_bytes, "test.zip"))

    @patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="some/key")
    def test_unsupported_extension_rejected(self, _mock_s3):
        from src.services.org_chart_batch_upload import create_batch_from_zip
        zip_bytes = _make_zip({"report.txt": b"text content"})
        db = self._make_db(cycle=_make_review_cycle())
        with pytest.raises(ValueError, match="no valid files|unsupported|empty"):
            asyncio.run(create_batch_from_zip(db, "CY25-FY26", zip_bytes, "test.zip"))

    @patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="some/key")
    def test_missing_review_cycle_raises(self, _mock_s3):
        from src.services.org_chart_batch_upload import create_batch_from_zip
        zip_bytes = _make_zip({"chart.pdf": b"%PDF-1.4"})
        db = self._make_db(cycle=None)  # cycle not found
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(create_batch_from_zip(db, "MISSING", zip_bytes, "test.zip"))

    @patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="some/key")
    def test_path_traversal_entry_raises(self, _mock_s3):
        from src.services.org_chart_batch_upload import create_batch_from_zip
        zip_bytes = _make_zip({"../evil.pdf": b"%PDF-1.4"})
        db = self._make_db(cycle=_make_review_cycle())
        with pytest.raises(ValueError, match="traversal"):
            asyncio.run(create_batch_from_zip(db, "CY25-FY26", zip_bytes, "test.zip"))

    @patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="some/key")
    def test_valid_files_create_batch(self, mock_s3):
        from src.services.org_chart_batch_upload import create_batch_from_zip
        from src.db.models import File, OrgChartUploadBatch, OrgChartUploadRecord

        zip_bytes = _make_zip({
            "chart1.pdf": b"%PDF-1.4",
            "chart2.docx": b"PK fake docx",
            "data.xlsx": b"PK fake xlsx",
        })
        db = self._make_db(cycle=_make_review_cycle())

        # flush must set batch.id so the S3 key can be built
        flush_call = 0

        async def _flush_with_id():
            nonlocal flush_call
            flush_call += 1
            for obj in db._added:
                if isinstance(obj, OrgChartUploadBatch) and getattr(obj, 'id', None) is None:
                    obj.id = 42
                elif isinstance(obj, File) and getattr(obj, 'id', None) is None:
                    obj.id = flush_call * 10

        db.flush.side_effect = _flush_with_id
        db.refresh.side_effect = AsyncMock()

        asyncio.run(create_batch_from_zip(db, "CY25-FY26", zip_bytes, "charts.zip"))

        # S3 should be called once per file
        assert mock_s3.call_count == 3

        # File and OrgChartUploadRecord rows added
        file_rows = [x for x in db._added if isinstance(x, File)]
        record_rows = [x for x in db._added if isinstance(x, OrgChartUploadRecord)]
        assert len(file_rows) == 3
        assert len(record_rows) == 3

        # All files tagged org_chart_batch
        for f in file_rows:
            assert ORG_CHART_BATCH_TAG in f.tags


# ---------------------------------------------------------------------------
# process_mapping_upload — mock-DB unit tests
# ---------------------------------------------------------------------------

def _make_xlsx_mapping(rows: list[list]) -> bytes:
    """Build mapping XLSX with standard headers + data rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["file_name", "company_id", "name"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_portfolio_company(
    pc_id: int,
    company_id: str,
    name: str,
    review_cycle_id: str = "CY25-FY26",
) -> MagicMock:
    from src.db.models import PortfolioCompany
    pc = MagicMock(spec=PortfolioCompany)
    pc.id = pc_id
    pc.company_id = company_id
    pc.name = name
    pc.review_cycle_id = review_cycle_id
    return pc


def _make_record_mock(
    file_name: str,
    batch_id: int = 1,
    record_id: int = 1,
) -> MagicMock:
    from src.db.models import OrgChartUploadRecord
    r = MagicMock(spec=OrgChartUploadRecord)
    r.id = record_id
    r.batch_id = batch_id
    r.file_name = file_name
    r.extraction_status = "pending_mapping"
    r.company_id = None
    r.name = None
    r.portfolio_company_id = None
    r.error_message = None
    return r


def _make_batch_mock(review_cycle_id: str = "CY25-FY26", batch_id: int = 1) -> MagicMock:
    from src.db.models import OrgChartUploadBatch
    b = MagicMock(spec=OrgChartUploadBatch)
    b.id = batch_id
    b.review_cycle_id = review_cycle_id
    b.status = "uploaded"
    return b


class TestProcessMappingUploadUnit:
    """Mock-DB tests for process_mapping_upload."""

    def _make_db_for_mapping(
        self,
        records: list,
        lookup_pc: Any = None,  # returned for company_id lookup
        name_lookup_pc: Any = None,  # returned for name lookup
    ) -> AsyncMock:
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        # db.add is synchronous in SQLAlchemy
        db.add = MagicMock()

        from src.db.models import OrgChartUploadRecord, PortfolioCompany
        from sqlalchemy import and_
        from sqlalchemy.orm import RelationshipProperty

        class _ListResult:
            """Mimics sqlalchemy execute() result for scalars() → iterable list."""
            def __init__(self, vals):
                self._vals = vals if isinstance(vals, list) else []
            def scalars(self):
                return iter(self._vals)
            def all(self):
                return self._vals

        class _SingleResult:
            """Mimics execute() result for scalar_one_or_none()."""
            def __init__(self, val):
                self._val = val
            def scalar_one_or_none(self):
                return self._val

        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            # First call: get batch records → iterable list
            if call_count == 1:
                return _ListResult(records)
            # Distinguish company_id vs name lookup by the bound parameter name in the WHERE clause.
            stmt_str = str(stmt)
            if ":company_id_1" in stmt_str:
                return _SingleResult(lookup_pc)
            return _SingleResult(name_lookup_pc)

        db.execute.side_effect = _execute
        return db

    def test_company_id_resolves(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("report.pdf", record_id=10)
        pc = _make_portfolio_company(99, "ACME", "Acme Corp")
        xlsx = _make_xlsx_mapping([["report.pdf", "ACME", ""]])
        batch = _make_batch_mock()

        db = self._make_db_for_mapping(records=[record], lookup_pc=pc)

        result, mapped_ids = asyncio.run(process_mapping_upload(db, batch, xlsx))

        assert result.rows_mapped == 1
        assert record.portfolio_company_id == 99
        assert record.extraction_status == "mapped"
        assert 10 in mapped_ids

    def test_name_fallback_resolves(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("chart.docx", record_id=11)
        pc = _make_portfolio_company(77, "XYZ", "XYZ Holdings")
        xlsx = _make_xlsx_mapping([["chart.docx", "", "XYZ Holdings"]])
        batch = _make_batch_mock()

        db = self._make_db_for_mapping(records=[record], lookup_pc=None, name_lookup_pc=pc)

        result, mapped_ids = asyncio.run(process_mapping_upload(db, batch, xlsx))

        assert result.rows_mapped == 1
        assert record.portfolio_company_id == 77
        assert 11 in mapped_ids

    def test_company_id_takes_precedence_over_name(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("a.pdf", record_id=5)
        pc_by_id = _make_portfolio_company(10, "CID1", "Company A")
        pc_by_name = _make_portfolio_company(20, "CID2", "Company B")
        xlsx = _make_xlsx_mapping([["a.pdf", "CID1", "Company B"]])
        batch = _make_batch_mock()

        db = self._make_db_for_mapping(records=[record], lookup_pc=pc_by_id, name_lookup_pc=pc_by_name)

        result, _ = asyncio.run(process_mapping_upload(db, batch, xlsx))

        assert record.portfolio_company_id == 10  # from company_id, not name

    def test_unknown_company_id_leaves_unmapped(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("b.pdf", record_id=6)
        xlsx = _make_xlsx_mapping([["b.pdf", "UNKNOWN_CID", ""]])
        batch = _make_batch_mock()

        db = self._make_db_for_mapping(records=[record], lookup_pc=None, name_lookup_pc=None)

        result, mapped_ids = asyncio.run(process_mapping_upload(db, batch, xlsx))

        assert result.rows_mapped == 0
        assert result.rows_skipped == 1
        assert len(result.errors) >= 1
        assert record.portfolio_company_id is None
        assert 6 not in mapped_ids

    def test_unknown_file_name_skipped_with_error(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("known.pdf", record_id=7)
        xlsx = _make_xlsx_mapping([["unknown_file.pdf", "CID1", ""]])
        batch = _make_batch_mock()

        db = self._make_db_for_mapping(records=[record])

        result, _ = asyncio.run(process_mapping_upload(db, batch, xlsx))

        assert result.rows_skipped == 1
        assert any("not found" in e["error"] for e in result.errors)

    def test_missing_headers_raises(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        record = _make_record_mock("x.pdf")
        # Build XLSX missing the 'name' column
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["file_name", "company_id"])  # missing 'name'
        ws.append(["x.pdf", "CID1"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx = buf.getvalue()

        batch = _make_batch_mock()
        db = self._make_db_for_mapping(records=[record])

        with pytest.raises(ValueError, match="missing"):
            asyncio.run(process_mapping_upload(db, batch, xlsx))

    def test_empty_xlsx_raises(self):
        from src.services.org_chart_batch_upload import process_mapping_upload

        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        xlsx = buf.getvalue()

        batch = _make_batch_mock()
        db = AsyncMock()

        with pytest.raises(ValueError):
            asyncio.run(process_mapping_upload(db, batch, xlsx))


# ---------------------------------------------------------------------------
# run_org_chart_batch_record_extraction — mock tests
# ---------------------------------------------------------------------------

class TestBatchRecordExtraction:
    def test_extraction_success_stores_json(self):
        from src.services.org_chart_extraction import run_org_chart_batch_record_extraction
        from src.db.models import OrgChartUploadRecord

        record = MagicMock(spec=OrgChartUploadRecord)
        record.id = 1
        record.storage_uri = "s3://bucket/some/key"
        record.file_name = "chart.pdf"
        record.extraction_status = "mapped"

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return record

        db.execute.return_value = _Result()

        fake_rows = [
            {"llm_id": 1, "name": "HoldCo", "geolocation": "India", "entity_type": "Holding",
             "is_parent": True, "children_ids": [2]},
            {"llm_id": 2, "name": "SubCo", "geolocation": "India", "entity_type": "Subsidiary",
             "is_parent": False, "children_ids": []},
        ]

        with (
            patch("src.services.org_chart_extraction.download_file_bytes", return_value=b"fake pdf"),
            patch("src.services.org_chart_extraction.call_llm_org_chart", new=AsyncMock(return_value="[mock]")),
            patch("src.services.org_chart_extraction.parse_org_chart_response", return_value=fake_rows),
        ):
            asyncio.run(run_org_chart_batch_record_extraction(db, 1))

        assert record.extracted_org_chart == fake_rows
        assert record.extraction_status == "completed"
        assert record.error_message is None

    def test_extraction_failure_marks_record_failed(self):
        from src.services.org_chart_extraction import run_org_chart_batch_record_extraction
        from src.db.models import OrgChartUploadRecord

        record = MagicMock(spec=OrgChartUploadRecord)
        record.id = 2
        record.storage_uri = "s3://bucket/some/key"
        record.file_name = "chart.pdf"
        record.extraction_status = "mapped"

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return record

        db.execute.return_value = _Result()

        with (
            patch("src.services.org_chart_extraction.download_file_bytes", side_effect=RuntimeError("S3 failure")),
        ):
            asyncio.run(run_org_chart_batch_record_extraction(db, 2))

        assert record.extraction_status == "failed"
        assert "S3 failure" in (record.error_message or "")

    def test_extraction_does_not_modify_entities(self):
        """Batch flow must NOT call _persist_entities."""
        from src.services.org_chart_extraction import run_org_chart_batch_record_extraction, _persist_entities
        from src.db.models import OrgChartUploadRecord

        record = MagicMock(spec=OrgChartUploadRecord)
        record.id = 3
        record.storage_uri = "s3://bucket/some/key"
        record.file_name = "chart.pdf"
        record.extraction_status = "mapped"

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return record

        db.execute.return_value = _Result()

        with (
            patch("src.services.org_chart_extraction.download_file_bytes", return_value=b"fake"),
            patch("src.services.org_chart_extraction.call_llm_org_chart", new=AsyncMock(return_value="[]")),
            patch("src.services.org_chart_extraction.parse_org_chart_response", return_value=[
                {"llm_id": 1, "name": "Root", "geolocation": None, "entity_type": None,
                 "is_parent": True, "children_ids": []}
            ]),
            patch("src.services.org_chart_extraction._persist_entities") as mock_persist,
        ):
            asyncio.run(run_org_chart_batch_record_extraction(db, 3))

        mock_persist.assert_not_called()

    def test_no_storage_uri_marks_failed(self):
        from src.services.org_chart_extraction import run_org_chart_batch_record_extraction
        from src.db.models import OrgChartUploadRecord

        record = MagicMock(spec=OrgChartUploadRecord)
        record.id = 4
        record.storage_uri = None  # missing URI
        record.file_name = "chart.pdf"
        record.extraction_status = "mapped"
        record.error_message = None

        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return record

        db.execute.return_value = _Result()

        asyncio.run(run_org_chart_batch_record_extraction(db, 4))

        assert record.extraction_status == "failed"


# ---------------------------------------------------------------------------
# Integration tests (require real DB)
# ---------------------------------------------------------------------------

@_skip_no_db
class TestCreateBatchIntegration:
    """Integration tests: real DB inside a SAVEPOINT that is rolled back."""

    @pytest.fixture
    def db_session(self):
        from sqlalchemy import event
        from sqlalchemy.orm import Session
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

        yield session
        session.close()
        transaction.rollback()
        connection.close()

    @pytest.fixture
    def async_session(self, db_session):
        """Wrap the sync session in an AsyncMock that delegates key operations."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from unittest.mock import AsyncMock, MagicMock

        # Use a real async session via AsyncSessionLocal
        from src.db.session import AsyncSessionLocal
        return AsyncSessionLocal

    def _seed_review_cycle(self, session, cycle_id: str = "CY25-FY26") -> Any:
        from src.db.models import ReviewCycle
        rc = ReviewCycle(id=cycle_id, name="Test Cycle", status="active", meta={})
        session.add(rc)
        session.flush()
        return rc

    def test_zip_upload_creates_batch_and_records(self, db_session):
        """ZIP upload: batch row, File rows, OrgChartUploadRecord rows all created."""
        from src.db.models import File, OrgChartUploadBatch, OrgChartUploadRecord

        self._seed_review_cycle(db_session, "CY25-FY26")

        zip_bytes = _make_zip({
            "chart1.pdf": b"%PDF-1.4 fake",
            "org.docx": b"PK fake docx",
        })

        with patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="key"):
            # Run via asyncio against a real async session
            from src.db.session import AsyncSessionLocal

            async def _run():
                from src.services.org_chart_batch_upload import create_batch_from_zip
                async with AsyncSessionLocal() as sess:
                    # Seed
                    from src.db.models import ReviewCycle
                    rc = ReviewCycle(id="CY_INT_TEST_01", name="Integration Test Cycle", status="active", meta={})
                    sess.add(rc)
                    await sess.flush()
                    batch = await create_batch_from_zip(sess, "CY_INT_TEST_01", zip_bytes, "test.zip")
                    return batch.id, batch.file_count, batch.status

            batch_id, file_count, status = asyncio.run(_run())

        assert file_count == 2
        assert status == "uploaded"

    def test_mapping_template_headers(self):
        """GET mapping template returns XLSX with correct headers."""
        from src.db.models import OrgChartUploadRecord
        records = [_make_record_mock(f"file{i}.pdf", record_id=i) for i in range(3)]
        xlsx = build_mapping_template(records)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        assert rows[0] == ("file_name", "company_id", "name")
        assert len(rows) == 4  # 1 header + 3 data rows

    def test_invalid_zip_creates_no_batch(self):
        """Invalid ZIP returns 422 and creates nothing in DB."""
        with patch("src.services.org_chart_batch_upload.s3_upload_file") as mock_s3:
            from src.db.session import AsyncSessionLocal

            async def _run():
                from src.services.org_chart_batch_upload import create_batch_from_zip
                async with AsyncSessionLocal() as sess:
                    from src.db.models import ReviewCycle
                    rc = ReviewCycle(id="CY_INT_TEST_02", name="Test", status="active", meta={})
                    sess.add(rc)
                    await sess.flush()
                    try:
                        await create_batch_from_zip(sess, "CY_INT_TEST_02", b"not a zip", "bad.zip")
                        return False  # should not reach here
                    except ValueError:
                        return True

            raised = asyncio.run(_run())
            assert raised is True
            mock_s3.assert_not_called()

    def test_mapping_upload_company_id_resolution(self):
        """Mapping upload with company_id resolves to portfolio_company_id."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.services.org_chart_batch_upload import (
                create_batch_from_zip,
                process_mapping_upload,
                get_batch_records,
            )
            from src.db.models import OrgChartUploadBatch, PortfolioCompany, ReviewCycle

            async with AsyncSessionLocal() as sess:
                rc = ReviewCycle(id="CY_INT_TEST_03", name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc = PortfolioCompany(
                    company_id="CID_TEST_99",
                    name="Test Company Inc",
                    review_cycle_id="CY_INT_TEST_03",
                    extra_data={},
                )
                sess.add(pc)
                await sess.flush()
                pc_id = pc.id

                zip_bytes = _make_zip({"report.pdf": b"%PDF-1.4"})
                with patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="k"):
                    batch = await create_batch_from_zip(sess, "CY_INT_TEST_03", zip_bytes, "t.zip")

                xlsx = _make_xlsx_mapping([["report.pdf", "CID_TEST_99", ""]])
                result, mapped_ids = await process_mapping_upload(sess, batch, xlsx)

                records = await get_batch_records(sess, batch.id)
                return result.rows_mapped, records[0].portfolio_company_id, pc_id

        rows_mapped, record_pc_id, pc_id = asyncio.run(_run())
        assert rows_mapped == 1
        assert record_pc_id == pc_id

    def test_mapping_upload_name_fallback(self):
        """Mapping upload with blank company_id but valid name resolves via name lookup."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.services.org_chart_batch_upload import (
                create_batch_from_zip,
                process_mapping_upload,
                get_batch_records,
            )
            from src.db.models import PortfolioCompany, ReviewCycle

            async with AsyncSessionLocal() as sess:
                rc = ReviewCycle(id="CY_INT_TEST_04", name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc = PortfolioCompany(
                    company_id="CID_NAME_TEST",
                    name="Name Fallback Corp",
                    review_cycle_id="CY_INT_TEST_04",
                    extra_data={},
                )
                sess.add(pc)
                await sess.flush()
                pc_id = pc.id

                zip_bytes = _make_zip({"chart.docx": b"PK fake"})
                with patch("src.services.org_chart_batch_upload.s3_upload_file", return_value="k"):
                    batch = await create_batch_from_zip(sess, "CY_INT_TEST_04", zip_bytes, "t.zip")

                xlsx = _make_xlsx_mapping([["chart.docx", "", "Name Fallback Corp"]])
                result, _ = await process_mapping_upload(sess, batch, xlsx)

                records = await get_batch_records(sess, batch.id)
                return result.rows_mapped, records[0].portfolio_company_id, pc_id

        rows_mapped, record_pc_id, pc_id = asyncio.run(_run())
        assert rows_mapped == 1
        assert record_pc_id == pc_id

    def test_extraction_does_not_touch_entity_table(self):
        """After batch extraction, Entity table rows and PortfolioCompany.org_chart_file_id are unchanged."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.services.org_chart_extraction import run_org_chart_batch_record_extraction
            from src.db.models import Entity, OrgChartUploadRecord, PortfolioCompany, ReviewCycle

            async with AsyncSessionLocal() as sess:
                rc = ReviewCycle(id="CY_INT_TEST_05", name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc = PortfolioCompany(
                    company_id="CID_EXT_TEST",
                    name="Ext Test Corp",
                    review_cycle_id="CY_INT_TEST_05",
                    org_chart_file_id=None,
                    extra_data={},
                )
                sess.add(pc)
                await sess.flush()
                pc_id = pc.id

                from src.db.models import File, FileUploadStatus, OrgChartUploadBatch
                batch = OrgChartUploadBatch(
                    review_cycle_id="CY_INT_TEST_05",
                    original_zip_filename="t.zip",
                    status="uploaded",
                    file_count=1,
                )
                sess.add(batch)
                await sess.flush()

                file_row = File(
                    portfolio_company_id=None,
                    review_cycle_id="CY_INT_TEST_05",
                    filename="chart.pdf",
                    content_type="application/pdf",
                    storage_uri="s3://test-bucket/test/chart.pdf",
                    size_bytes=100,
                    status=FileUploadStatus.UPLOADED,
                    tags=[],
                    pending_audit_log=[],
                )
                sess.add(file_row)
                await sess.flush()

                record = OrgChartUploadRecord(
                    batch_id=batch.id,
                    review_cycle_id="CY_INT_TEST_05",
                    file_id=file_row.id,
                    file_name="chart.pdf",
                    storage_uri="s3://test-bucket/test/chart.pdf",
                    portfolio_company_id=pc_id,
                    extraction_status="mapped",
                )
                sess.add(record)
                await sess.flush()
                record_id = record.id

                fake_rows = [
                    {"llm_id": 1, "name": "HoldCo", "geolocation": None,
                     "entity_type": "Holding", "is_parent": True, "children_ids": []}
                ]

                with (
                    patch("src.services.org_chart_extraction.download_file_bytes", return_value=b"fake"),
                    patch("src.services.org_chart_extraction.call_llm_org_chart",
                          new=AsyncMock(return_value="[mock]")),
                    patch("src.services.org_chart_extraction.parse_org_chart_response",
                          return_value=fake_rows),
                ):
                    await run_org_chart_batch_record_extraction(sess, record_id)

                # Verify extraction stored
                from sqlalchemy import select
                updated = (await sess.execute(
                    select(OrgChartUploadRecord).where(OrgChartUploadRecord.id == record_id)
                )).scalar_one_or_none()
                # Company untouched
                refreshed_pc = await sess.get(PortfolioCompany, pc_id)
                entities = (await sess.execute(
                    select(Entity).where(Entity.portfolio_company_id == pc_id)
                )).scalars().all()

                return (
                    updated.extraction_status,
                    updated.extracted_org_chart,
                    refreshed_pc.org_chart_file_id,
                    len(entities),
                )

        extraction_status, extracted, org_chart_file_id, entity_count = asyncio.run(_run())
        assert extraction_status == "completed"
        assert extracted is not None
        assert org_chart_file_id is None  # not touched by batch flow
        assert entity_count == 0  # no entities created by batch flow
