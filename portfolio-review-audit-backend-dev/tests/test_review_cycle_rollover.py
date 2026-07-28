"""
Tests for review_cycle_rollover.

Two layers:
  * Pure-logic tests for IST date → (source, target) cycle id derivation.
  * Integration tests against the configured Postgres DB inside a SAVEPOINT
    that is rolled back at the end, so test data never persists.  These are
    auto-skipped when no DB is reachable (e.g. CI without secrets).

Run:
    PYTHONPATH=. pytest tests/test_review_cycle_rollover.py -v
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest

from src.services.review_cycle_rollover import (
    ist_today,
    resolve_rollover_cycle_ids,
)


# ---------------------------------------------------------------------------
# Pure-logic: IST + cycle id derivation
# ---------------------------------------------------------------------------


class TestResolveRolloverCycleIds:
    def test_on_rollover_day_2026(self):
        # 1 Jun 2026 IST → CY26-FY27 is the new target; CY25-FY26 is the source.
        assert resolve_rollover_cycle_ids(date(2026, 6, 1)) == ("CY25-FY26", "CY26-FY27")

    def test_on_rollover_day_2030(self):
        assert resolve_rollover_cycle_ids(date(2030, 6, 1)) == ("CY29-FY30", "CY30-FY31")

    def test_after_rollover_same_cycle(self):
        # Aug 2026 — still in CY26-FY27 window; source remains CY25-FY26.
        assert resolve_rollover_cycle_ids(date(2026, 8, 15)) == ("CY25-FY26", "CY26-FY27")

    def test_before_rollover_previous_cycle_target(self):
        # 31 May 2026 — current cycle is still CY25-FY26; new one not yet active.
        assert resolve_rollover_cycle_ids(date(2026, 5, 31)) == ("CY24-FY25", "CY25-FY26")

    def test_january_uses_previous_year(self):
        # Jan 2027 — inside CY26-FY27.
        assert resolve_rollover_cycle_ids(date(2027, 1, 10)) == ("CY25-FY26", "CY26-FY27")

    def test_century_wrap(self):
        # 1 Jun 2099 → CY99-FY00; source CY98-FY99.
        assert resolve_rollover_cycle_ids(date(2099, 6, 1)) == ("CY98-FY99", "CY99-FY00")


class TestIstToday:
    def test_utc_to_ist_offset_crosses_midnight(self):
        # 31 May 2026 20:00 UTC → 01 Jun 2026 01:30 IST → rollover day.
        utc = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
        assert ist_today(utc) == date(2026, 6, 1)

    def test_utc_before_ist_rollover(self):
        # 31 May 2026 17:00 UTC → 31 May 2026 22:30 IST.
        utc = datetime(2026, 5, 31, 17, 0, tzinfo=timezone.utc)
        assert ist_today(utc) == date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Integration tests against the configured DB (rolled back via SAVEPOINT).
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
    not _db_reachable(), reason="Configured Postgres DB not reachable; skipping integration tests"
)


@pytest.fixture
def db_session():
    """Session bound to a SAVEPOINT that is rolled back, so test data never persists."""
    from sqlalchemy.orm import Session

    from src.db.session import sync_engine

    connection = sync_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False)

    # Use a nested SAVEPOINT — any commit() inside the service merely releases it.
    nested = connection.begin_nested()

    from sqlalchemy import event

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def isolated_cycle_ids():
    """Unique CYxx-FYyy ids that don't collide with real data (uses 90s wrap)."""
    # Use a high CY pair unlikely to exist; keep within parseable CYxx-FYyy.
    src = "CY89-FY90"
    tgt = "CY90-FY91"
    return src, tgt


@_skip_no_db
class TestRolloverIntegration:
    def _seed_cycle(self, session, cycle_id):
        from src.db.models import ReviewCycle
        from src.services.review_cycle_provisioning import (
            cycle_window_dates,
            format_cycle_display_name,
            parse_cycle_id,
        )

        if session.get(ReviewCycle, cycle_id) is not None:
            return
        cy, fy = parse_cycle_id(cycle_id)
        starts, ends = cycle_window_dates(cy, fy)
        session.add(
            ReviewCycle(
                id=cycle_id,
                name=format_cycle_display_name(cy, fy),
                status="active",
                starts_at=starts,
                ends_at=ends,
                meta={},
            )
        )
        session.flush()

    def _seed_company(self, session, *, cycle_id, company_id, name="Acme"):
        from src.db.models import PortfolioCompany

        pc = PortfolioCompany(
            company_id=company_id,
            review_cycle_id=cycle_id,
            name=name,
            contact_name="Alice",
            contact_email_id="alice@acme.test",
            fund="Fund-X",
            investment_lead="Lead-Y",
            review_stage="Completed",  # workflow state that should NOT carry over
            audit_status="Done",
            extra_data={},
        )
        session.add(pc)
        session.flush()
        return pc

    def _seed_metadata(self, session, *, cycle_id, deal_id, fund="Fund-X", strategy="Growth"):
        from src.db.models import PortfolioCompanyMetadata

        pcm = PortfolioCompanyMetadata(
            fund=fund,
            deal_id=deal_id,
            strategy=strategy,
            deal_name="Acme Deal",
            review_cycle_id=cycle_id,
            # financials / classification — should carry verbatim
            cost=100,
            fmv=250,
            sector_l1="Tech",
            comments="carry me over",
            # audit workflow — should reset on rollover
            scoping_for_audit=True,
            reason_for_exclusion="n/a last year",
            deal_level_stage_1="In Progress",
            deal_level_stage_2="Review",
            auditor="KPMG",
            category_of_auditor="Big 4",
            tentative_audit_completion_date="2026-03-31",
            category="Audited",
            py_audit_status="Completed",
        )
        session.add(pcm)
        session.flush()
        return pcm

    def test_creates_target_cycle_when_missing(self, db_session, isolated_cycle_ids):
        from src.db.models import ReviewCycle
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        self._seed_company(db_session, cycle_id=src_id, company_id=company_id)

        result = rollover_review_cycle(
            db_session, source_cycle_id=src_id, target_cycle_id=tgt_id
        )
        assert result["status"] == "ok"
        assert db_session.get(ReviewCycle, tgt_id) is not None
        assert result["companies_created"] == 1

    def test_copies_only_metadata_fields_and_sets_created_stage(
        self, db_session, isolated_cycle_ids
    ):
        from sqlalchemy import select

        from src.db.models import PortfolioCompany
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        self._seed_company(db_session, cycle_id=src_id, company_id=company_id)

        rollover_review_cycle(db_session, source_cycle_id=src_id, target_cycle_id=tgt_id)

        tgt = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id,
                PortfolioCompany.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        assert tgt.name == "Acme"
        assert tgt.contact_name == "Alice"
        assert tgt.contact_email_id == "alice@acme.test"
        assert tgt.fund == "Fund-X"
        assert tgt.investment_lead == "Lead-Y"
        # Audit state lives on entities now; the company carries no review stage.
        assert tgt.review_stage is None
        # Workflow state from source MUST NOT be copied.
        assert tgt.audit_status is None

    def test_idempotent_no_duplicates(self, db_session, isolated_cycle_ids):
        from sqlalchemy import func, select

        from src.db.models import PortfolioCompany
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        self._seed_company(db_session, cycle_id=src_id, company_id=company_id)

        rollover_review_cycle(db_session, source_cycle_id=src_id, target_cycle_id=tgt_id)
        # Mutate source to verify second run updates instead of duplicating.
        src_pc = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id,
                PortfolioCompany.review_cycle_id == src_id,
            )
        ).scalar_one()
        src_pc.fund = "Fund-X-UPDATED"
        db_session.flush()

        result2 = rollover_review_cycle(
            db_session, source_cycle_id=src_id, target_cycle_id=tgt_id
        )
        assert result2["companies_created"] == 0
        assert result2["companies_updated"] == 1

        count = db_session.execute(
            select(func.count())
            .select_from(PortfolioCompany)
            .where(
                PortfolioCompany.company_id == company_id,
                PortfolioCompany.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        assert count == 1

        tgt = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id,
                PortfolioCompany.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        assert tgt.fund == "Fund-X-UPDATED"

    def test_copies_company_metadata_with_resets(self, db_session, isolated_cycle_ids):
        from sqlalchemy import func, select

        from src.db.models import PortfolioCompanyMetadata
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        self._seed_company(db_session, cycle_id=src_id, company_id=company_id)
        deal_id = f"deal-{uuid.uuid4().hex[:8]}"
        self._seed_metadata(db_session, cycle_id=src_id, deal_id=deal_id)

        result = rollover_review_cycle(
            db_session, source_cycle_id=src_id, target_cycle_id=tgt_id
        )
        assert result["metadata_created"] == 1
        assert result["metadata_updated"] == 0

        tgt = db_session.execute(
            select(PortfolioCompanyMetadata).where(
                PortfolioCompanyMetadata.deal_id == deal_id,
                PortfolioCompanyMetadata.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        # Verbatim copy
        assert tgt.fund == "Fund-X"
        assert tgt.strategy == "Growth"
        assert tgt.cost == 100
        assert tgt.fmv == 250
        assert tgt.sector_l1 == "Tech"
        assert tgt.comments == "carry me over"
        # Reset to defaults
        assert tgt.scoping_for_audit is False
        assert tgt.reason_for_exclusion is None
        assert tgt.deal_level_stage_1 is None
        assert tgt.deal_level_stage_2 is None
        assert tgt.auditor is None
        assert tgt.category_of_auditor is None
        assert tgt.tentative_audit_completion_date is None
        assert tgt.category is None
        assert tgt.py_audit_status is None

        # Idempotent re-run: update in place, no duplicate row.
        result2 = rollover_review_cycle(
            db_session, source_cycle_id=src_id, target_cycle_id=tgt_id
        )
        assert result2["metadata_created"] == 0
        assert result2["metadata_updated"] == 1
        count = db_session.execute(
            select(func.count())
            .select_from(PortfolioCompanyMetadata)
            .where(
                PortfolioCompanyMetadata.deal_id == deal_id,
                PortfolioCompanyMetadata.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        assert count == 1

    def test_copies_entities_with_parent_remapping(self, db_session, isolated_cycle_ids):
        from sqlalchemy import select

        from src.db.models import Entity
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        src_pc = self._seed_company(db_session, cycle_id=src_id, company_id=company_id)

        parent = Entity(
            portfolio_company_id=src_pc.id,
            name="Parent Co",
            entity_type="parent",
            is_parent=True,
            review_cycle=src_id,
            status="In Progress",  # workflow — should NOT carry over
            extra_data={},
        )
        db_session.add(parent)
        db_session.flush()
        child = Entity(
            portfolio_company_id=src_pc.id,
            name="Child Co",
            entity_type="subsidiary",
            is_parent=False,
            parent_entity_id=parent.id,
            review_cycle=src_id,
            status="Done",
            extra_data={},
        )
        db_session.add(child)
        db_session.flush()

        rollover_review_cycle(db_session, source_cycle_id=src_id, target_cycle_id=tgt_id)

        tgt_entities = (
            db_session.execute(
                select(Entity).where(Entity.review_cycle == tgt_id, Entity.name.in_(["Parent Co", "Child Co"]))
            )
            .scalars()
            .all()
        )
        assert len(tgt_entities) == 2
        by_name = {e.name: e for e in tgt_entities}
        assert by_name["Child Co"].parent_entity_id == by_name["Parent Co"].id
        # Workflow reset: entity status (the audit state of record) restarts at "Not applicable".
        assert by_name["Parent Co"].status == "Not applicable"
        assert by_name["Child Co"].status == "Not applicable"

    def test_copies_org_chart_link(self, db_session, isolated_cycle_ids):
        from src.db.models import File
        from src.services.review_cycle_rollover import rollover_review_cycle

        src_id, tgt_id = isolated_cycle_ids
        self._seed_cycle(db_session, src_id)
        company_id = f"itest-{uuid.uuid4().hex[:8]}"
        src_pc = self._seed_company(db_session, cycle_id=src_id, company_id=company_id)

        f = File(
            portfolio_company_id=src_pc.id,
            review_cycle_id=src_id,
            filename="orgchart.pdf",
            content_type="application/pdf",
            storage_uri="s3://bucket/orgchart.pdf",
            size_bytes=12345,
            status="uploaded",
            tags=["org_chart"],
            pending_audit_log=[],
        )
        db_session.add(f)
        db_session.flush()
        src_pc.org_chart_file_id = f.id
        db_session.flush()

        rollover_review_cycle(db_session, source_cycle_id=src_id, target_cycle_id=tgt_id)

        from sqlalchemy import select

        from src.db.models import PortfolioCompany

        tgt_pc = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id,
                PortfolioCompany.review_cycle_id == tgt_id,
            )
        ).scalar_one()
        assert tgt_pc.org_chart_file_id is not None
        new_file = db_session.get(File, tgt_pc.org_chart_file_id)
        assert new_file.storage_uri == "s3://bucket/orgchart.pdf"
        assert new_file.filename == "orgchart.pdf"
        assert new_file.portfolio_company_id == tgt_pc.id

    def test_missing_source_cycle_is_safe_skip(self, db_session):
        from src.services.review_cycle_rollover import rollover_review_cycle

        result = rollover_review_cycle(
            db_session, source_cycle_id="CY77-FY78", target_cycle_id="CY78-FY79"
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "source_cycle_missing"
