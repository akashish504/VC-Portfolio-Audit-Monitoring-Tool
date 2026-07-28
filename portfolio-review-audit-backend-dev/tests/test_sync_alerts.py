"""
Integration tests for CompanySyncNotification — sync creation and service layer.

All tests run inside a SAVEPOINT that is rolled back, so test data never persists.
Auto-skipped when no DB is reachable.

Run:
    PYTHONPATH=. pytest tests/test_sync_alerts.py -v
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
        session.add(ReviewCycle(id=cycle_id, name=f"Test Cycle {cycle_id}", status="active", meta={}))
        session.flush()
    return cycle_id


def _seed_raw_company(session, *, cid, name=None, display_name=None,
                      is_venture=False, is_seed=False, is_growth=False):
    from src.db.models import CompanyDataRaw

    row = CompanyDataRaw(
        id=cid, cid=cid, name=name, display_name=display_name,
        is_venture=is_venture, is_seed=is_seed, is_growth=is_growth,
    )
    session.add(row)
    session.flush()
    return row


def _seed_portfolio_company(session, *, company_id: str, cycle_id: str, name="Existing"):
    from src.db.models import PortfolioCompany

    pc = PortfolioCompany(
        company_id=company_id, name=name,
        review_cycle_id=cycle_id, review_stage="Created",
    )
    session.add(pc)
    session.flush()
    return pc


def _seed_notification(session, *, company_id: str, company_name: str = "TestCo",
                       is_read: bool = False):
    from src.db.models import CompanySyncNotification

    n = CompanySyncNotification(
        company_id=company_id,
        company_name=company_name,
        message=f"New company added: {company_name} (ID: {company_id}, stage: Unknown)",
        is_read=is_read,
    )
    session.add(n)
    session.flush()
    return n


# ---------------------------------------------------------------------------
# Sync integration tests
# ---------------------------------------------------------------------------


@_skip_no_db
class TestSyncCreatesAlerts:
    def test_sync_creates_notification_for_new_company(self, db_session):
        from sqlalchemy import select

        from src.db.models import CompanySyncNotification
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name="AlertCo", is_venture=True)

        result = sync_company_master_to_portfolio(db_session)
        assert result is True

        notif = db_session.execute(
            select(CompanySyncNotification).where(CompanySyncNotification.company_id == company_id_str)
        ).scalar_one()
        assert notif.company_name == "AlertCo"
        assert notif.investment_stage == "Venture/Growth"
        assert "AlertCo" in notif.message
        assert company_id_str in notif.message
        assert notif.is_read is False

    def test_sync_does_not_create_notification_for_existing_company(self, db_session):
        from sqlalchemy import func, select

        from src.db.models import CompanySyncNotification
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        cycle_id = _seed_review_cycle(db_session)
        _seed_portfolio_company(db_session, company_id=company_id_str, cycle_id=cycle_id)
        _seed_raw_company(db_session, cid=cid, name="ExistingCo")

        result = sync_company_master_to_portfolio(db_session)
        assert result is True

        count = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.company_id == company_id_str)
        ).scalar_one()
        assert count == 0

    def test_sync_one_notification_per_company_not_per_cycle(self, db_session):
        from sqlalchemy import func, select

        from src.db.models import CompanySyncNotification
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        cid = int(uuid.uuid4().int % (10**12)) + 10**12
        company_id_str = str(cid)
        _seed_review_cycle(db_session)
        _seed_review_cycle(db_session)
        _seed_raw_company(db_session, cid=cid, name="MultiCycleCo", is_seed=True)

        result = sync_company_master_to_portfolio(db_session)
        assert result is True

        count = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.company_id == company_id_str)
        ).scalar_one()
        assert count == 1

    def test_sync_skipped_companies_no_notification(self, db_session):
        from sqlalchemy import func, select

        from src.db.models import CompanyDataRaw, CompanySyncNotification
        from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
            sync_company_master_to_portfolio,
        )

        _seed_review_cycle(db_session)
        pk_base = int(uuid.uuid4().int % (10**12)) + 9 * 10**12

        # null cid
        db_session.add(CompanyDataRaw(id=pk_base + 1, cid=None, name="NullCid"))
        # blank name
        bad_cid = pk_base + 2
        db_session.add(CompanyDataRaw(id=bad_cid, cid=bad_cid, name="", display_name=None))
        db_session.flush()

        result = sync_company_master_to_portfolio(db_session)
        assert result is True

        count = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.company_id == str(bad_cid))
        ).scalar_one()
        assert count == 0


# ---------------------------------------------------------------------------
# Service integration tests
# ---------------------------------------------------------------------------


@_skip_no_db
class TestSyncAlertService:
    def test_list_filters_unread_read_all(self, db_session):
        import asyncio

        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        uid_a = f"svc-a-{uuid.uuid4().hex[:8]}"
        uid_b = f"svc-b-{uuid.uuid4().hex[:8]}"
        uid_c = f"svc-c-{uuid.uuid4().hex[:8]}"
        _seed_notification(db_session, company_id=uid_a, is_read=False)
        _seed_notification(db_session, company_id=uid_b, is_read=False)
        _seed_notification(db_session, company_id=uid_c, is_read=True)
        db_session.flush()

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                svc = SyncAlertService(async_db)
                _, total_unread = await svc.list_alerts("unread", limit=500, offset=0)
                _, total_read = await svc.list_alerts("read", limit=500, offset=0)
                _, total_all = await svc.list_alerts("all", limit=500, offset=0)
                return total_unread, total_read, total_all

        total_unread, total_read, total_all = asyncio.get_event_loop().run_until_complete(_run())
        assert total_unread >= 2
        assert total_read >= 1
        assert total_all >= total_unread + total_read

    def test_acknowledge_sets_read_metadata(self, db_session):
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.models import CompanySyncNotification
        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        uid = f"ack-{uuid.uuid4().hex[:8]}"
        notif = _seed_notification(db_session, company_id=uid, is_read=False)
        db_session.flush()
        notif_id = notif.id
        test_email = "tester@example.com"

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                await SyncAlertService(async_db).acknowledge(notif_id, test_email)
                await async_db.commit()

        asyncio.get_event_loop().run_until_complete(_run())

        db_session.expire_all()
        refreshed = db_session.execute(
            select(CompanySyncNotification).where(CompanySyncNotification.id == notif_id)
        ).scalar_one()
        assert refreshed.is_read is True
        assert refreshed.read_at is not None
        assert refreshed.acknowledged_by_user_email == test_email

    def test_acknowledge_idempotent(self, db_session):
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.models import CompanySyncNotification
        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        uid = f"idem-{uuid.uuid4().hex[:8]}"
        notif = _seed_notification(db_session, company_id=uid, is_read=False)
        db_session.flush()
        notif_id = notif.id

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                svc = SyncAlertService(async_db)
                await svc.acknowledge(notif_id, "a@example.com")
                await async_db.commit()
                # second call — should not raise
                await svc.acknowledge(notif_id, "a@example.com")
                await async_db.commit()

        asyncio.get_event_loop().run_until_complete(_run())

        db_session.expire_all()
        refreshed = db_session.execute(
            select(CompanySyncNotification).where(CompanySyncNotification.id == notif_id)
        ).scalar_one()
        assert refreshed.is_read is True

    def test_acknowledge_all(self, db_session):
        import asyncio

        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.models import CompanySyncNotification
        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        unread_ids = [f"all-{uuid.uuid4().hex[:8]}" for _ in range(3)]
        for cid in unread_ids:
            _seed_notification(db_session, company_id=cid, is_read=False)
        already_read_id = f"read-{uuid.uuid4().hex[:8]}"
        _seed_notification(db_session, company_id=already_read_id, is_read=True)
        db_session.flush()

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                count = await SyncAlertService(async_db).acknowledge_all("bulk@example.com")
                await async_db.commit()
                return count

        acknowledged = asyncio.get_event_loop().run_until_complete(_run())
        assert acknowledged >= 3

        db_session.expire_all()
        still_unread = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.company_id.in_(unread_ids))
            .where(CompanySyncNotification.is_read == False)  # noqa: E712
        ).scalar_one()
        assert still_unread == 0

    def test_unread_count(self, db_session):
        import asyncio

        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.models import CompanySyncNotification
        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        ids = [f"cnt-{uuid.uuid4().hex[:8]}" for _ in range(2)]
        for cid in ids:
            _seed_notification(db_session, company_id=cid, is_read=False)
        db_session.flush()

        db_count = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.is_read == False)  # noqa: E712
        ).scalar_one()

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                return await SyncAlertService(async_db).get_unread_count()

        svc_count = asyncio.get_event_loop().run_until_complete(_run())
        assert svc_count == db_count


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@_skip_no_db
class TestSyncAlertRoutes:
    def test_unread_count_endpoint_matches_db(self, db_session):
        import asyncio

        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import AsyncSession

        from src.db.models import CompanySyncNotification
        from src.db.session import async_engine
        from src.services.sync_alert_service import SyncAlertService

        ids = [f"api-cnt-{uuid.uuid4().hex[:8]}" for _ in range(2)]
        for cid in ids:
            _seed_notification(db_session, company_id=cid, is_read=False)
        db_session.flush()

        db_count = db_session.execute(
            select(func.count()).select_from(CompanySyncNotification)
            .where(CompanySyncNotification.is_read == False)  # noqa: E712
        ).scalar_one()

        async def _run():
            async with AsyncSession(async_engine) as async_db:
                return await SyncAlertService(async_db).get_unread_count()

        api_count = asyncio.get_event_loop().run_until_complete(_run())
        assert api_count == db_count
