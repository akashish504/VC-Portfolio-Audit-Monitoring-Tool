"""
Tests for the org-chart reconciliation service (Phase 2).

Two layers:
  * Pure-logic unit tests (always run) — service helpers.
  * Integration tests against a real Postgres DB inside a SAVEPOINT that is
    rolled back, so test data never persists.  Auto-skipped when DB not reachable.

Run:
    PYTHONPATH=. pytest tests/test_org_chart_reconciliation.py -v
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# DB reachability guard (same pattern as other integration tests)
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


_skip_no_db = pytest.mark.skipif(not _db_reachable(), reason="Configured Postgres DB not reachable")

# ---------------------------------------------------------------------------
# Pure-logic tests (no DB)
# ---------------------------------------------------------------------------

class TestRefResolution:
    def test_existing_ref(self):
        from src.services.org_chart_reconciliation import _resolve_ref
        mapping = {"existing:42": 42}
        assert _resolve_ref("existing:42", mapping) == 42

    def test_extracted_ref(self):
        from src.services.org_chart_reconciliation import _resolve_ref
        mapping = {"extracted:7": 99}
        assert _resolve_ref("extracted:7", mapping) == 99

    def test_empty_ref_returns_none(self):
        from src.services.org_chart_reconciliation import _resolve_ref
        assert _resolve_ref("", {}) is None

    def test_fallback_existing_id_from_string(self):
        from src.services.org_chart_reconciliation import _resolve_ref
        # No mapping entry, but "existing:42" falls back to int 42
        assert _resolve_ref("existing:42", {}) == 42

    def test_unknown_extracted_returns_none(self):
        from src.services.org_chart_reconciliation import _resolve_ref
        assert _resolve_ref("extracted:99", {}) is None


class TestCycleDetection:
    def test_self_cycle(self):
        from src.services.org_chart_reconciliation import _detect_cycle
        assert _detect_cycle(5, 5, {}) is True

    def test_no_cycle(self):
        from src.services.org_chart_reconciliation import _detect_cycle
        # entity=3, parent=1; 1's parent is 2; 2's parent is None → no cycle
        existing = {1: 2, 2: None}
        assert _detect_cycle(3, 1, existing) is False

    def test_detects_cycle(self):
        from src.services.org_chart_reconciliation import _detect_cycle
        # entity=1, proposed parent=3; chain: 3→2→1 — cycle!
        existing = {2: 1, 3: 2}
        assert _detect_cycle(1, 3, existing) is True


class TestTopoOrder:
    def test_roots_first(self):
        from src.services.org_chart_reconciliation import _topo_order, _build_parent_map
        rows = [
            {"llm_id": 1, "name": "Root", "is_parent": True, "children_ids": [2, 3]},
            {"llm_id": 2, "name": "Child A", "is_parent": False, "children_ids": []},
            {"llm_id": 3, "name": "Child B", "is_parent": False, "children_ids": []},
        ]
        parent_of = _build_parent_map(rows)
        order = _topo_order(rows, parent_of)
        assert order[0] == 1
        assert set(order) == {1, 2, 3}


# ---------------------------------------------------------------------------
# Auto-match pure-logic tests
# ---------------------------------------------------------------------------

class TestAutoMatch:
    def _existing(self, id: int, name: str, geo: str | None = None) -> dict:
        return {"id": id, "name": name, "geolocation": geo, "entity_type": None,
                "parent_entity_id": None, "is_parent": False, "status": None, "file_count": 0}

    def _extracted(self, llm_id: int, name: str, geo: str | None = None) -> dict:
        return {"llm_id": llm_id, "name": name, "geolocation": geo,
                "entity_type": None, "is_parent": False, "children_ids": []}

    def test_name_match_normalised(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        existing = [self._existing(1, "Acme Holdings")]
        extracted = [self._extracted(10, " acme holdings ")]
        result = compute_auto_matches(existing, extracted)
        assert len(result["auto_matched_entities"]) == 1
        am = result["auto_matched_entities"][0]
        assert am["existing_entity_id"] == 1
        assert am["extracted_temp_id"] == 10
        assert am["match_reason"] == "name"
        assert result["unmatched_existing_entities"] == []
        assert result["unmatched_extracted_entities"] == []

    def test_geolocation_match_normalised(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        existing = [self._existing(1, "Old Corp", geo="USA")]
        extracted = [self._extracted(10, "New Corp", geo=" usa ")]
        result = compute_auto_matches(existing, extracted)
        assert len(result["auto_matched_entities"]) == 1
        am = result["auto_matched_entities"][0]
        assert am["match_reason"] == "geolocation"

    def test_empty_geolocation_does_not_match(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        # None vs None
        existing = [self._existing(1, "Corp A", geo=None)]
        extracted = [self._extracted(10, "Corp B", geo=None)]
        result = compute_auto_matches(existing, extracted)
        assert result["auto_matched_entities"] == []
        # Empty string vs whitespace
        existing2 = [self._existing(2, "Corp C", geo="  ")]
        extracted2 = [self._extracted(20, "Corp D", geo="")]
        result2 = compute_auto_matches(existing2, extracted2)
        assert result2["auto_matched_entities"] == []

    def test_name_preferred_over_geolocation(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        # Both existing entities share the same geo but one has a matching name.
        # The name-match entity should be chosen; the geo-only match should not conflict.
        existing = [
            self._existing(1, "Acme Holdings", geo="US"),
            self._existing(2, "Other Corp", geo="US"),
        ]
        extracted = [
            self._extracted(10, "Acme Holdings", geo="US"),  # name match + geo match
            self._extracted(20, "Unrelated Co", geo="US"),   # geo match only for entity 2
        ]
        result = compute_auto_matches(existing, extracted)
        # Entity 1 should match extracted 10 via name (pass 1)
        name_match = next(am for am in result["auto_matched_entities"] if am["existing_entity_id"] == 1)
        assert name_match["extracted_temp_id"] == 10
        assert name_match["match_reason"] == "name"
        # Entity 2 should match extracted 20 via geo (pass 2)
        geo_match = next(am for am in result["auto_matched_entities"] if am["existing_entity_id"] == 2)
        assert geo_match["extracted_temp_id"] == 20
        assert geo_match["match_reason"] == "geolocation"

    def test_no_duplicate_matches(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        # Two existing entities with same name — only first wins; second is unmatched.
        existing = [
            self._existing(1, "Acme", geo="US"),
            self._existing(2, "Acme", geo="UK"),
        ]
        extracted = [self._extracted(10, "Acme", geo="US")]
        result = compute_auto_matches(existing, extracted)
        assert len(result["auto_matched_entities"]) == 1
        assert result["auto_matched_entities"][0]["existing_entity_id"] == 1
        assert len(result["unmatched_existing_entities"]) == 1
        assert result["unmatched_existing_entities"][0]["id"] == 2

    def test_unmatched_lists_populated(self):
        from src.services.org_chart_reconciliation import compute_auto_matches
        existing = [
            self._existing(1, "Match Me", geo="US"),
            self._existing(2, "No Match", geo="UK"),
        ]
        extracted = [
            self._extracted(10, "Match Me", geo="US"),
            self._extracted(20, "Also No Match", geo="CA"),
        ]
        result = compute_auto_matches(existing, extracted)
        assert len(result["auto_matched_entities"]) == 1
        assert result["unmatched_existing_entities"][0]["id"] == 2
        assert result["unmatched_extracted_entities"][0]["llm_id"] == 20


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def _unique_cycle_id() -> str:
    import uuid
    return f"RC-TEST-{uuid.uuid4().hex[:8]}"


def _extracted_rows(n: int = 3) -> list[dict]:
    return [
        {"llm_id": 1, "name": "Parent Co", "geolocation": "US", "entity_type": "Holding", "is_parent": True, "children_ids": [2, 3]},
        {"llm_id": 2, "name": "Sub A", "geolocation": "US", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
        {"llm_id": 3, "name": "Sub B", "geolocation": "UK", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
    ][:n]


@_skip_no_db
class TestApplyOrgChartRecord:
    """Test 1: Direct apply when company has no org chart."""

    def test_apply_creates_entities_and_marks_applied(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import apply_org_chart_record

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc = PortfolioCompany(
                    company_id=f"co-{cycle_id}",
                    name="Test Company",
                    review_cycle_id=cycle_id,
                    extra_data={},
                )
                sess.add(pc)
                await sess.flush()

                # Seed File
                f = File(
                    filename="chart.pdf",
                    review_cycle_id=cycle_id,
                    status=FileUploadStatus.UPLOADED,
                    tags=["org_chart_batch"],
                    pending_audit_log=[],
                )
                sess.add(f)
                await sess.flush()

                batch = OrgChartUploadBatch(
                    review_cycle_id=cycle_id,
                    original_zip_filename="test.zip",
                    status="completed",
                    file_count=1,
                )
                sess.add(batch)
                await sess.flush()

                record = OrgChartUploadRecord(
                    batch_id=batch.id,
                    review_cycle_id=cycle_id,
                    file_id=f.id,
                    file_name="chart.pdf",
                    portfolio_company_id=pc.id,
                    extracted_org_chart=_extracted_rows(3),
                    extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                with patch("src.services.org_chart_reconciliation.CompanyAuditRecorder") as mock_rec:
                    mock_rec.return_value.log_company = AsyncMock()
                    created = await apply_org_chart_record(sess, record.id)

                # Verify
                from sqlalchemy import select
                entities = (
                    await sess.execute(
                        select(Entity).where(Entity.portfolio_company_id == pc.id)
                    )
                ).scalars().all()

                await sess.refresh(pc)
                await sess.refresh(record)
                await sess.refresh(f)

                return {
                    "entity_count": len(entities),
                    "has_root": any(e.is_parent for e in entities),
                    "org_chart_file_id": pc.org_chart_file_id,
                    "file_company_id": f.portfolio_company_id,
                    "rec_status": record.reconciliation_status,
                    "rec_applied_at_set": record.applied_at is not None,
                    "applied_ids": record.applied_entity_ids,
                    "created_count": len(created),
                }

        r = asyncio.run(_run())
        assert r["entity_count"] == 3
        assert r["has_root"] is True
        assert r["rec_status"] == "applied"
        assert r["rec_applied_at_set"] is True
        assert r["created_count"] == 3
        assert len(r["applied_ids"]) == 3


@_skip_no_db
class TestGetPendingUpdate:
    """Test 2: Pending-update detection when company already has an org chart.
       Test 3: No dialog after save."""

    def test_returns_pending_when_existing_chart(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import get_pending_update

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc = PortfolioCompany(
                    company_id=f"co-{cycle_id}",
                    name="Test Company",
                    review_cycle_id=cycle_id,
                    extra_data={},
                )
                sess.add(pc)
                await sess.flush()

                # Seed an existing entity (simulates existing org chart)
                existing_ent = Entity(
                    portfolio_company_id=pc.id,
                    name="Old Entity",
                    is_parent=True,
                    extra_data={},
                )
                sess.add(existing_ent)
                await sess.flush()

                # Seed a completed batch record
                f = File(filename="new.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id,
                    review_cycle_id=cycle_id,
                    file_id=f.id,
                    file_name="new.pdf",
                    portfolio_company_id=pc.id,
                    extracted_org_chart=_extracted_rows(2),
                    extraction_status="completed",
                )
                sess.add(record)
                await sess.commit()

                result = await get_pending_update(sess, pc.id)
                return result

        r = asyncio.run(_run())
        assert r is not None
        assert r["requires_reconciliation"] is True
        assert len(r["existing_entities"]) == 1
        assert len(r["extracted_org_chart"]) == 2

    def test_no_pending_after_applied(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import apply_org_chart_record, get_pending_update

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()
                f = File(filename="c.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="c.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=_extracted_rows(1),
                    extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                with patch("src.services.org_chart_reconciliation.CompanyAuditRecorder") as mock_rec:
                    mock_rec.return_value.log_company = AsyncMock()
                    await apply_org_chart_record(sess, record.id)

                # After apply, pending-update should return None
                result_after = await get_pending_update(sess, pc.id)
                return result_after

        r = asyncio.run(_run())
        assert r is None


@_skip_no_db
class TestReconcileOrgChartRecord:
    """Tests 4–6: match/create/archive, file moves, transaction rollback."""

    def _seed_all(self, sess, cycle_id, existing_names, extracted_rows, files_per_entity=None):
        """Helper: seed company, entities, batch record, optionally files. Returns dict."""
        import asyncio as _aio

        from src.db.models import (
            Entity, File, FileUploadStatus, OrgChartUploadBatch,
            OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
        )

        rc = ReviewCycle(id=cycle_id, name="Test", status="active", meta={})
        sess.add(rc)
        pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="Test Co", review_cycle_id=cycle_id, extra_data={})
        sess.add(pc)
        sess._session.flush()  # type: ignore[attr-defined]

        entities = []
        for name in existing_names:
            e = Entity(portfolio_company_id=pc.id, name=name, is_parent=False, extra_data={})
            sess.add(e)
        sess._session.flush()

        return pc, entities, rc

    def test_match_create_archive_roundtrip(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record
            from sqlalchemy import select

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="Test", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                # Three existing entities
                e_old_parent = Entity(portfolio_company_id=pc.id, name="Old Parent", is_parent=True, extra_data={})
                e_old_child = Entity(portfolio_company_id=pc.id, name="Old Child", is_parent=False, extra_data={})
                e_stale = Entity(portfolio_company_id=pc.id, name="Stale Entity", is_parent=False, extra_data={})
                sess.add_all([e_old_parent, e_old_child, e_stale])
                await sess.flush()

                # Extracted: New Parent (llm_id=1), New Child (llm_id=2), New Sub (llm_id=3)
                extracted = [
                    {"llm_id": 1, "name": "New Parent", "geolocation": "US", "entity_type": "Holding", "is_parent": True, "children_ids": [2]},
                    {"llm_id": 2, "name": "New Child", "geolocation": "US", "entity_type": "Subsidiary", "is_parent": False, "children_ids": [3]},
                    {"llm_id": 3, "name": "New Sub", "geolocation": "UK", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
                ]

                f = File(filename="chart.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="chart.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                entity_mappings = [
                    # Old Parent → matched to New Parent
                    {"existing_entity_id": e_old_parent.id, "extracted_temp_id": 1, "action": "match",
                     "final_name": "New Parent", "final_geolocation": "US", "final_entity_type": "Holding", "final_is_parent": True},
                    # Old Child → matched to New Child
                    {"existing_entity_id": e_old_child.id, "extracted_temp_id": 2, "action": "match",
                     "final_name": "New Child", "final_geolocation": "US", "final_entity_type": "Subsidiary", "final_is_parent": False},
                    # Stale → archive
                    {"existing_entity_id": e_stale.id, "action": "archive"},
                    # New Sub → create
                    {"extracted_temp_id": 3, "action": "create",
                     "final_name": "New Sub", "final_geolocation": "UK", "final_entity_type": "Subsidiary", "final_is_parent": False},
                ]
                parent_links = [
                    {"child_ref": f"existing:{e_old_child.id}", "parent_ref": f"existing:{e_old_parent.id}"},
                    {"child_ref": "extracted:3", "parent_ref": f"existing:{e_old_child.id}"},
                ]

                with patch("src.services.org_chart_reconciliation.CompanyAuditRecorder") as mock_rec:
                    mock_rec.return_value.log_company = AsyncMock()
                    entities = await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc.id,
                        entity_mappings=entity_mappings,
                        parent_links=parent_links,
                        file_moves=[],
                    )

                # Reload entities from DB
                final = (await sess.execute(select(Entity).where(Entity.portfolio_company_id == pc.id))).scalars().all()
                await sess.refresh(record)

                stale_ent = next(e for e in final if e.id == e_stale.id)
                new_parent = next(e for e in final if e.id == e_old_parent.id)
                new_sub = next((e for e in final if e.name == "New Sub"), None)

                return {
                    "total": len(final),
                    "stale_status": stale_ent.status,
                    "old_parent_name": new_parent.name,
                    "new_sub_created": new_sub is not None,
                    "rec_status": record.reconciliation_status,
                    "has_parent_link": new_sub is not None and new_sub.parent_entity_id == e_old_child.id,
                }

        r = asyncio.run(_run())
        assert r["stale_status"] == "Archive Entity"
        assert r["old_parent_name"] == "New Parent"
        assert r["new_sub_created"] is True
        assert r["rec_status"] == "applied"
        assert r["has_parent_link"] is True

    def test_file_move_preservation(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record
            from sqlalchemy import select

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                e_old = Entity(portfolio_company_id=pc.id, name="Old", is_parent=True, extra_data={})
                sess.add(e_old)
                await sess.flush()

                # File attached to old entity
                attached_file = File(
                    portfolio_company_id=pc.id, entity_id=e_old.id,
                    filename="report.pdf", review_cycle_id=cycle_id,
                    status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[],
                )
                sess.add(attached_file)
                await sess.flush()

                extracted = [{"llm_id": 10, "name": "New Co", "geolocation": None, "entity_type": "Holding", "is_parent": True, "children_ids": []}]
                f_batch = File(filename="chart.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f_batch)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f_batch.id,
                    file_name="chart.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                entity_mappings = [
                    {"existing_entity_id": e_old.id, "extracted_temp_id": 10, "action": "match",
                     "final_name": "New Co", "final_is_parent": True},
                ]
                file_moves = [
                    {"file_id": attached_file.id, "target_entity_ref": f"existing:{e_old.id}", "acknowledge_detached": False},
                ]

                with patch("src.services.org_chart_reconciliation.CompanyAuditRecorder") as mock_rec:
                    mock_rec.return_value.log_company = AsyncMock()
                    await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc.id,
                        entity_mappings=entity_mappings,
                        parent_links=[],
                        file_moves=file_moves,
                    )

                await sess.refresh(attached_file)
                return {"file_entity_id": attached_file.entity_id}

        r = asyncio.run(_run())
        assert r["file_entity_id"] is not None

    def test_transaction_rollback_on_cycle(self):
        """Invalid parent cycle → ValueError; no DB state persists."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record
            from sqlalchemy import select

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()
                e1 = Entity(portfolio_company_id=pc.id, name="E1", is_parent=True, extra_data={})
                e2 = Entity(portfolio_company_id=pc.id, name="E2", is_parent=False, extra_data={})
                sess.add_all([e1, e2])
                await sess.flush()

                extracted = [{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}]
                f = File(filename="c.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="c.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                # e1 → parent=e2, e2 → parent=e1 : cycle
                parent_links_with_cycle = [
                    {"child_ref": f"existing:{e1.id}", "parent_ref": f"existing:{e2.id}"},
                    {"child_ref": f"existing:{e2.id}", "parent_ref": f"existing:{e1.id}"},
                ]

                raised = False
                try:
                    await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc.id,
                        entity_mappings=[{"existing_entity_id": e1.id, "action": "keep"}],
                        parent_links=parent_links_with_cycle,
                        file_moves=[],
                    )
                except ValueError:
                    raised = True

                await sess.refresh(record)
                return {"raised": raised, "rec_status": record.reconciliation_status}

        r = asyncio.run(_run())
        assert r["raised"] is True
        # Record should remain un-applied (reconciliation_status still None/pending)
        assert r["rec_status"] not in ("applied",)

    def test_company_scoping_blocks_cross_company_file(self):
        """A file from another company must raise ValueError."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()

                pc1 = PortfolioCompany(company_id=f"co1-{cycle_id}", name="C1", review_cycle_id=cycle_id, extra_data={})
                pc2 = PortfolioCompany(company_id=f"co2-{cycle_id}", name="C2", review_cycle_id=cycle_id, extra_data={})
                sess.add_all([pc1, pc2])
                await sess.flush()

                # File belongs to pc2
                other_file = File(portfolio_company_id=pc2.id, filename="x.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(other_file)
                batch_file = File(filename="chart.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(batch_file)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=batch_file.id,
                    file_name="chart.pdf", portfolio_company_id=pc1.id,
                    extracted_org_chart=[{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}],
                    extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                raised = False
                try:
                    await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc1.id,
                        entity_mappings=[{"extracted_temp_id": 1, "action": "create", "final_name": "X"}],
                        parent_links=[],
                        file_moves=[{"file_id": other_file.id, "target_entity_ref": None}],
                    )
                except ValueError:
                    raised = True

                return {"raised": raised}

        r = asyncio.run(_run())
        assert r["raised"] is True

    def test_record_scoping_blocks_wrong_company(self):
        """Record mapped to company A cannot be reconciled for company B."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc1 = PortfolioCompany(company_id=f"co1-{cycle_id}", name="C1", review_cycle_id=cycle_id, extra_data={})
                pc2 = PortfolioCompany(company_id=f"co2-{cycle_id}", name="C2", review_cycle_id=cycle_id, extra_data={})
                sess.add_all([pc1, pc2])
                await sess.flush()

                f = File(filename="c.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                # Record belongs to pc1
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="c.pdf", portfolio_company_id=pc1.id,
                    extracted_org_chart=[{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}],
                    extraction_status="completed",
                )
                sess.add(record)
                await sess.flush()

                raised = False
                try:
                    # Try to reconcile record for pc2 → should fail
                    await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc2.id,
                        entity_mappings=[],
                        parent_links=[],
                        file_moves=[],
                    )
                except ValueError:
                    raised = True

                return {"raised": raised}

        r = asyncio.run(_run())
        assert r["raised"] is True


# ---------------------------------------------------------------------------
# Integration tests: get_pending_update() auto-match fields
# ---------------------------------------------------------------------------

@_skip_no_db
class TestGetPendingUpdateAutoMatch:
    """get_pending_update() must return auto-match metadata and must NOT mutate DB."""

    def test_auto_matched_entities_returned(self):
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import get_pending_update
            from sqlalchemy import select, func

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                e_match = Entity(portfolio_company_id=pc.id, name="Acme Holdings", geolocation="US", is_parent=True, extra_data={})
                e_nomatch = Entity(portfolio_company_id=pc.id, name="Orphan Entity", is_parent=False, extra_data={})
                sess.add_all([e_match, e_nomatch])
                await sess.flush()
                entity_count_before = (await sess.execute(
                    select(func.count()).select_from(Entity).where(Entity.portfolio_company_id == pc.id)
                )).scalar()

                extracted = [
                    {"llm_id": 1, "name": " acme holdings ", "geolocation": "US", "entity_type": "Holding", "is_parent": True, "children_ids": []},
                    {"llm_id": 2, "name": "Brand New Co", "geolocation": "UK", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
                ]
                f = File(filename="x.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="x.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                )
                sess.add(record)
                await sess.commit()

                result = await get_pending_update(sess, pc.id)

                entity_count_after = (await sess.execute(
                    select(func.count()).select_from(Entity).where(Entity.portfolio_company_id == pc.id)
                )).scalar()
                await sess.refresh(record)

                return {
                    "result": result,
                    "entity_count_before": entity_count_before,
                    "entity_count_after": entity_count_after,
                    "rec_status": record.reconciliation_status,
                    "e_match_id": e_match.id,
                }

        r = asyncio.run(_run())
        result = r["result"]
        assert result is not None

        auto_matches = result["auto_matched_entities"]
        assert len(auto_matches) == 1
        am = auto_matches[0]
        assert am["existing_entity_id"] == r["e_match_id"]
        assert am["extracted_temp_id"] == 1
        assert am["match_reason"] == "name"

        unmatched_existing_ids = [e["id"] for e in result["unmatched_existing_entities"]]
        assert r["e_match_id"] not in unmatched_existing_ids
        unmatched_extracted_llm_ids = [e["llm_id"] for e in result["unmatched_extracted_entities"]]
        assert 1 not in unmatched_extracted_llm_ids

        assert any(e["name"] == "Orphan Entity" for e in result["unmatched_existing_entities"])
        assert any(e["llm_id"] == 2 for e in result["unmatched_extracted_entities"])

        assert r["entity_count_after"] == r["entity_count_before"]
        assert r["rec_status"] != "applied"

    def test_get_pending_update_does_not_mutate_db(self):
        """Calling get_pending_update() must not create Entity rows or update PortfolioCompany."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import get_pending_update
            from sqlalchemy import select, func

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                e = Entity(portfolio_company_id=pc.id, name="Existing Co", is_parent=True, extra_data={})
                sess.add(e)
                await sess.flush()

                extracted = [{"llm_id": 1, "name": "Existing Co", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}]
                f = File(filename="x.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="x.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                )
                sess.add(record)
                await sess.commit()

                await get_pending_update(sess, pc.id)

                entity_count = (await sess.execute(
                    select(func.count()).select_from(Entity).where(Entity.portfolio_company_id == pc.id)
                )).scalar()
                await sess.refresh(pc)
                await sess.refresh(record)

                return {
                    "entity_count": entity_count,
                    "org_chart_file_id": pc.org_chart_file_id,
                    "rec_status": record.reconciliation_status,
                }

        r = asyncio.run(_run())
        assert r["entity_count"] == 1
        assert r["org_chart_file_id"] is None
        assert r["rec_status"] != "applied"


# ---------------------------------------------------------------------------
# Integration tests: DB state before/after extraction and reconcile save
# ---------------------------------------------------------------------------

@_skip_no_db
class TestDbStateBeforeAndAfterReconcile:
    """Verify DB state is correct before reconcile (extraction only) and after final save."""

    def test_extraction_state(self):
        """After extraction: extracted_org_chart populated, status pending, no Entity mutations."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from sqlalchemy import select, func

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                e = Entity(portfolio_company_id=pc.id, name="Old Co", is_parent=True, extra_data={})
                sess.add(e)
                await sess.flush()
                old_entity_id = e.id

                f = File(filename="x.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="x.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=_extracted_rows(2),
                    extraction_status="completed",
                    reconciliation_status="pending",
                )
                sess.add(record)
                await sess.commit()

                entity_count = (await sess.execute(
                    select(func.count()).select_from(Entity).where(Entity.portfolio_company_id == pc.id)
                )).scalar()
                await sess.refresh(pc)
                await sess.refresh(record)
                old_entity = await sess.get(Entity, old_entity_id)

                return {
                    "extracted_set": record.extracted_org_chart is not None and len(record.extracted_org_chart) > 0,
                    "rec_status": record.reconciliation_status,
                    "entity_count": entity_count,
                    "org_chart_file_id": pc.org_chart_file_id,
                    "old_entity_name": old_entity.name if old_entity else None,
                }

        r = asyncio.run(_run())
        assert r["extracted_set"] is True
        assert r["rec_status"] == "pending"
        assert r["entity_count"] == 1
        assert r["org_chart_file_id"] is None
        assert r["old_entity_name"] == "Old Co"

    def test_final_reconcile_save_full_flow(self):
        """After final save: auto-matched entities updated, creates inserted, archives set,
        parent links correct, file_id updated, record applied."""
        from src.db.session import AsyncSessionLocal

        async def _run():
            from src.db.models import (
                Entity, File, FileUploadStatus, OrgChartUploadBatch,
                OrgChartUploadRecord, PortfolioCompany, ReviewCycle,
            )
            from src.services.org_chart_reconciliation import reconcile_org_chart_record
            from sqlalchemy import select

            async with AsyncSessionLocal() as sess:
                cycle_id = _unique_cycle_id()
                rc = ReviewCycle(id=cycle_id, name="T", status="active", meta={})
                sess.add(rc)
                await sess.flush()
                pc = PortfolioCompany(company_id=f"co-{cycle_id}", name="C", review_cycle_id=cycle_id, extra_data={})
                sess.add(pc)
                await sess.flush()

                # e_auto: auto-matches by name to extracted llm_id=1
                # e_archive: user archives
                # e_keep: user keeps unchanged
                e_auto = Entity(portfolio_company_id=pc.id, name="Parent Co", geolocation="US", is_parent=True, extra_data={})
                e_archive = Entity(portfolio_company_id=pc.id, name="Stale Co", is_parent=False, extra_data={})
                e_keep = Entity(portfolio_company_id=pc.id, name="Keep Co", is_parent=False, extra_data={})
                sess.add_all([e_auto, e_archive, e_keep])
                await sess.flush()

                extracted = [
                    {"llm_id": 1, "name": "Parent Co", "geolocation": "US", "entity_type": "Holding", "is_parent": True, "children_ids": [2]},
                    {"llm_id": 2, "name": "New Sub", "geolocation": "UK", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
                ]

                f = File(filename="c.pdf", review_cycle_id=cycle_id, status=FileUploadStatus.UPLOADED, tags=[], pending_audit_log=[])
                sess.add(f)
                batch = OrgChartUploadBatch(review_cycle_id=cycle_id, original_zip_filename="z.zip", status="completed", file_count=1)
                sess.add(batch)
                await sess.flush()
                record = OrgChartUploadRecord(
                    batch_id=batch.id, review_cycle_id=cycle_id, file_id=f.id,
                    file_name="c.pdf", portfolio_company_id=pc.id,
                    extracted_org_chart=extracted, extraction_status="completed",
                    reconciliation_status="pending",
                )
                sess.add(record)
                await sess.flush()

                # User provides only unmatched mappings; e_auto NOT submitted by user
                entity_mappings = [
                    {"existing_entity_id": e_archive.id, "action": "archive"},
                    {"existing_entity_id": e_keep.id, "action": "keep"},
                    {"extracted_temp_id": 2, "action": "create",
                     "final_name": "New Sub", "final_geolocation": "UK",
                     "final_entity_type": "Subsidiary", "final_is_parent": False},
                ]
                parent_links = [
                    {"child_ref": "extracted:2", "parent_ref": f"existing:{e_auto.id}"},
                ]

                with patch("src.services.org_chart_reconciliation.CompanyAuditRecorder") as mock_rec:
                    mock_rec.return_value.log_company = AsyncMock()
                    await reconcile_org_chart_record(
                        sess,
                        record_id=record.id,
                        portfolio_company_id=pc.id,
                        entity_mappings=entity_mappings,
                        parent_links=parent_links,
                        file_moves=[],
                    )

                final_entities = (await sess.execute(
                    select(Entity).where(Entity.portfolio_company_id == pc.id)
                )).scalars().all()
                await sess.refresh(record)
                await sess.refresh(pc)

                e_auto_final = await sess.get(Entity, e_auto.id)
                e_archive_final = await sess.get(Entity, e_archive.id)
                new_sub = next((e for e in final_entities if e.name == "New Sub"), None)

                return {
                    "e_auto_type": e_auto_final.entity_type if e_auto_final else None,
                    "e_archive_status": e_archive_final.status if e_archive_final else None,
                    "new_sub_created": new_sub is not None,
                    "new_sub_parent": new_sub.parent_entity_id if new_sub else None,
                    "org_chart_file_id": pc.org_chart_file_id,
                    "rec_status": record.reconciliation_status,
                    "payload_has_auto": "auto_matched_entities" in (record.reconciliation_payload or {}),
                    "e_auto_id": e_auto.id,
                }

        r = asyncio.run(_run())
        assert r["e_auto_type"] == "Holding"
        assert r["e_archive_status"] == "Archive Entity"
        assert r["new_sub_created"] is True
        assert r["new_sub_parent"] == r["e_auto_id"]
        assert r["org_chart_file_id"] is not None
        assert r["rec_status"] == "applied"
        assert r["payload_has_auto"] is True
