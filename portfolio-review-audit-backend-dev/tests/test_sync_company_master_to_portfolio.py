"""
Tests for sync_company_master_to_portfolio.

Two layers:
  * Integration tests against the configured Postgres DB inside a SAVEPOINT
    that is rolled back at the end, so test data never persists.  These are
    auto-skipped when no DB is reachable (e.g. CI without secrets).

Run:
    PYTHONPATH=. pytest tests/test_sync_company_master_to_portfolio.py -v
"""
from __future__ import annotations

import os
import uuid

import pytest


# ---------------------------------------------------------------------------
# DB reachability guard
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Session bound to a SAVEPOINT that is rolled back, so test data never persists."""
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
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_review_cycle(session, cycle_id: str | None = None) -> str:
    from src.db.models import ReviewCycle

    cycle_id = cycle_id or f"TEST-{uuid.uuid4().hex[:8]}"
    if session.get(ReviewCycle, cycle_id) is None:
        session.add(
            ReviewCycle(
                id=cycle_id,
                name=f"Test Cycle {cycle_id}",
                status="active",
                meta={},
            )
        )
        session.flush()
    return cycle_id


def _seed_raw_company(session, *, cid, name=None, display_name=None,
                      is_venture=False, is_seed=False, is_growth=False):
    from src.db.models import CompanyDataRaw

    row = CompanyDataRaw(
        id=cid,
        cid=cid,
        name=name,
        display_name=display_name,
        is_venture=is_venture,
        is_seed=is_seed,
        is_growth=is_growth,
    )
    session.add(row)
    session.flush()
    return row


def _seed_portfolio_company(session, *, company_id: str, cycle_id: str, name="Existing"):
    from src.db.models import PortfolioCompany

    pc = PortfolioCompany(
        company_id=company_id,
        name=name,
        review_cycle_id=cycle_id,
        review_stage="Created",
    )
    session.add(pc)
    session.flush()
    return pc


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@_skip_no_db
class TestSyncCompanyMasterToPortfolio:
    def test_creates_rows_across_all_review_cycles(self, db_session):
        """Creates one PortfolioCompany per ReviewCycle for a new company.

        The DB may contain pre-existing review cycles; we verify that one row
        was created for each of our two seeded cycles (at minimum).
        """
        from sqlalchemy import select

        from src.db.models import PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        # Use a large random-ish cid unlikely to exist in real data.
        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        cycle_a = _seed_review_cycle(db_session)
        cycle_b = _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name="Acme", is_venture=True)
        company_id_str = str(cid)

        result = sync_company_master_to_portfolio(db_session)

        assert result is True

        rows = (
            db_session.execute(
                select(PortfolioCompany).where(PortfolioCompany.company_id == company_id_str)
            )
            .scalars()
            .all()
        )
        cycle_ids = {r.review_cycle_id for r in rows}
        # Both seeded cycles must be present (there may be more from existing DB data).
        assert cycle_a in cycle_ids
        assert cycle_b in cycle_ids
        for r in rows:
            assert r.name == "Acme"
            assert r.investment_stage == "Venture/Growth"

    def test_skips_when_company_already_exists(self, db_session):
        """Does not create additional rows when company_id already exists in any cycle."""
        from sqlalchemy import func, select

        from src.db.models import PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        cycle_a = _seed_review_cycle(db_session)
        _seed_review_cycle(db_session)
        _seed_portfolio_company(db_session, company_id=company_id_str, cycle_id=cycle_a)
        _seed_raw_company(db_session, cid=cid, name="Acme")

        result = sync_company_master_to_portfolio(db_session)

        assert result is True

        count = db_session.execute(
            select(func.count())
            .select_from(PortfolioCompany)
            .where(PortfolioCompany.company_id == company_id_str)
        ).scalar_one()
        # Only the one we seeded — no new rows added.
        assert count == 1

    def test_seed_surge_mapping(self, db_session):
        """is_seed=True (no venture/growth) maps to 'Seed/Surge'."""
        from sqlalchemy import select

        from src.db.models import PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        cycle_id = _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name="SeedCo", is_seed=True)

        sync_company_master_to_portfolio(db_session)

        # Filter to our seeded cycle to get exactly one row regardless of existing cycles.
        row = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id_str,
                PortfolioCompany.review_cycle_id == cycle_id,
            )
        ).scalar_one()
        assert row.investment_stage == "Seed/Surge"

    def test_venture_growth_takes_precedence_over_seed(self, db_session):
        """is_seed=True with is_growth=True maps to 'Venture/Growth', not 'Seed/Surge'."""
        from sqlalchemy import select

        from src.db.models import PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        cycle_id = _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name="MixedCo", is_seed=True, is_growth=True)

        sync_company_master_to_portfolio(db_session)

        row = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id_str,
                PortfolioCompany.review_cycle_id == cycle_id,
            )
        ).scalar_one()
        assert row.investment_stage == "Venture/Growth"

    def test_skips_bad_rows(self, db_session):
        """Rows with null cid or blank name/display_name are skipped."""
        from sqlalchemy import func, select

        from src.db.models import CompanyDataRaw, PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        _seed_review_cycle(db_session)

        # Generate unique PKs that won't collide with real data.
        pk_base = int(uuid.uuid4().int % (10**12)) + 9 * 10**12
        blank_cid = pk_base + 2
        ws_cid = pk_base + 3

        # null cid — use a fresh pk but null cid
        db_session.add(CompanyDataRaw(id=pk_base + 1, cid=None, name="NullCid"))

        # blank name and no display_name
        db_session.add(CompanyDataRaw(id=blank_cid, cid=blank_cid, name="", display_name=None))

        # whitespace-only name and whitespace display_name
        db_session.add(CompanyDataRaw(id=ws_cid, cid=ws_cid, name="   ", display_name="   "))

        db_session.flush()

        result = sync_company_master_to_portfolio(db_session)

        assert result is True

        expected_ids = [str(blank_cid), str(ws_cid)]
        count = db_session.execute(
            select(func.count())
            .select_from(PortfolioCompany)
            .where(PortfolioCompany.company_id.in_(expected_ids))
        ).scalar_one()
        assert count == 0

    def test_display_name_fallback(self, db_session):
        """When name is null, display_name is used as the company name."""
        from sqlalchemy import select

        from src.db.models import PortfolioCompany
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        cycle_id = _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name=None, display_name="Display Co")

        sync_company_master_to_portfolio(db_session)

        row = db_session.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == company_id_str,
                PortfolioCompany.review_cycle_id == cycle_id,
            )
        ).scalar_one()
        assert row.name == "Display Co"
