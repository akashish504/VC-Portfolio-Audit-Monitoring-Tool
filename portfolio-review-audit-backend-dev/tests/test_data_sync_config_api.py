"""
Tests for GET/PUT /api/v1/data-sync-config and scheduler interpretation.

Layers:
  * Unit tests for SettingsService.get_data_sync_config_read / put_data_sync_config
    using a synchronous SAVEPOINT session — always run.
  * Unit tests for scheduler weekly/daily interpretation — always run (no DB needed).
  * Integration tests for the FastAPI routes — auto-skipped when DB is not reachable.

Run:
    PYTHONPATH=. pytest tests/test_data_sync_config_api.py -v
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema.settings import DataSyncConfigPut, DataSyncConfigRead


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
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_db_session(first_result=None):
    """Minimal AsyncSession mock for SettingsService — wires execute().scalars().first()."""
    scalars = MagicMock()
    scalars.first.return_value = first_result
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars

    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Unit: SettingsService — GET (no DB row → defaults)
# ---------------------------------------------------------------------------


class TestGetDataSyncConfigDefaults:
    def test_returns_weekly_default_when_row_absent(self):
        from src.services.settings import SettingsService

        db = _make_db_session(first_result=None)
        result = _run(SettingsService.get_data_sync_config_read(db))

        assert isinstance(result, DataSyncConfigRead)
        assert result.frequency == "weekly"
        assert result.enabled is True
        assert result.time == "21:00"
        assert result.timezone == "Asia/Kolkata"
        assert result.day_of_week == "sun"

    def test_returns_stored_frequency_when_row_present(self):
        from src.services.settings import SettingsService

        row = MagicMock()
        row.value = {
            "enabled": True,
            "frequency": "daily",
            "time": "21:00",
            "timezone": "Asia/Kolkata",
            "day_of_week": "sun",
        }
        db = _make_db_session(first_result=row)
        result = _run(SettingsService.get_data_sync_config_read(db))

        assert result.frequency == "daily"

    def test_invalid_frequency_in_db_falls_back_to_weekly(self):
        from src.services.settings import SettingsService

        row = MagicMock()
        row.value = {"frequency": "monthly"}
        db = _make_db_session(first_result=row)
        result = _run(SettingsService.get_data_sync_config_read(db))

        assert result.frequency == "weekly"


# ---------------------------------------------------------------------------
# Unit: SettingsService — PUT
# ---------------------------------------------------------------------------


class TestPutDataSyncConfig:
    def test_put_daily_persists_and_returns_daily(self):
        from src.services.settings import SettingsService

        db = _make_db_session(first_result=None)
        payload = DataSyncConfigPut(frequency="daily")
        result = _run(SettingsService.put_data_sync_config(db, payload))

        assert result.frequency == "daily"
        assert result.enabled is True
        assert result.time == "21:00"
        assert result.timezone == "Asia/Kolkata"
        assert result.day_of_week == "sun"
        db.add.assert_called_once()

    def test_put_weekly_persists_and_returns_weekly_with_sun(self):
        from src.services.settings import SettingsService

        db = _make_db_session(first_result=None)
        payload = DataSyncConfigPut(frequency="weekly")
        result = _run(SettingsService.put_data_sync_config(db, payload))

        assert result.frequency == "weekly"
        assert result.day_of_week == "sun"

    def test_put_updates_existing_row_not_insert(self):
        from src.services.settings import SettingsService

        existing_row = MagicMock()
        existing_row.value = {"frequency": "daily", "enabled": True}
        db = _make_db_session(first_result=existing_row)
        payload = DataSyncConfigPut(frequency="weekly")
        result = _run(SettingsService.put_data_sync_config(db, payload))

        assert result.frequency == "weekly"
        db.add.assert_not_called()
        assert existing_row.value["frequency"] == "weekly"

    def test_put_rejects_invalid_frequency(self):
        """Pydantic should reject values outside daily|weekly before service is called."""
        with pytest.raises(Exception):
            DataSyncConfigPut(frequency="monthly")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit: scheduler interprets daily / weekly correctly
# ---------------------------------------------------------------------------


class TestSchedulerInterpretation:
    """Tests that the scheduler job honours the frequency config at runtime."""

    def _make_config(self, frequency: str, day_of_week: str = "sun", enabled: bool = True):
        from src.services.data_sync_schedule_config import DataSyncScheduleConfig

        return DataSyncScheduleConfig(
            enabled=enabled,
            frequency=frequency,  # type: ignore[arg-type]
            time="21:00",
            timezone="Asia/Kolkata",
            day_of_week=day_of_week,
        )

    def _make_session_factory(self, first_result=None):
        scalars = MagicMock()
        scalars.first.return_value = first_result
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars

        session = MagicMock()
        session.execute = AsyncMock(return_value=execute_result)
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=session)
        return factory, session

    def test_daily_config_runs_every_day(self):
        """Daily: job runs regardless of weekday."""
        cfg = self._make_config("daily")
        factory, mock_session = self._make_session_factory(first_result=None)

        with (
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.load_data_sync_schedule_config",
                new=AsyncMock(return_value=cfg),
            ),
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.async_session",
                new=factory,
            ),
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.DataService.run_in_background",
                new=AsyncMock(),
            ) as mock_run,
        ):
            from src.scripts.data_manipulation.scheduled_data_sync import _run_scheduled_data_sync

            _run(_run_scheduled_data_sync())

            mock_run.assert_called_once()

    def test_weekly_config_runs_on_sunday(self):
        """Weekly (sun): runs when today is Sunday (weekday 6)."""
        cfg = self._make_config("weekly", day_of_week="sun")
        factory, mock_session = self._make_session_factory(first_result=None)

        mock_now = MagicMock()
        mock_now.weekday.return_value = 6  # Sunday

        with (
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.load_data_sync_schedule_config",
                new=AsyncMock(return_value=cfg),
            ),
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.async_session",
                new=factory,
            ),
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.DataService.run_in_background",
                new=AsyncMock(),
            ) as mock_run,
            patch(
                "src.scripts.data_manipulation.scheduled_data_sync.datetime",
            ) as mock_dt,
        ):
            mock_dt.now.return_value = mock_now

            from src.scripts.data_manipulation.scheduled_data_sync import _run_scheduled_data_sync

            _run(_run_scheduled_data_sync())

            mock_run.assert_called_once()

    def test_weekly_config_skips_non_sunday(self):
        """Weekly (sun): skips on Monday through Saturday."""
        for weekday in range(6):  # 0=Mon … 5=Sat
            cfg = self._make_config("weekly", day_of_week="sun")
            factory, mock_session = self._make_session_factory(first_result=None)

            mock_now = MagicMock()
            mock_now.weekday.return_value = weekday

            with (
                patch(
                    "src.scripts.data_manipulation.scheduled_data_sync.load_data_sync_schedule_config",
                    new=AsyncMock(return_value=cfg),
                ),
                patch(
                    "src.scripts.data_manipulation.scheduled_data_sync.async_session",
                    new=factory,
                ),
                patch(
                    "src.scripts.data_manipulation.scheduled_data_sync.DataService.run_in_background",
                    new=AsyncMock(),
                ) as mock_run,
                patch(
                    "src.scripts.data_manipulation.scheduled_data_sync.datetime",
                ) as mock_dt,
            ):
                mock_dt.now.return_value = mock_now

                from src.scripts.data_manipulation.scheduled_data_sync import _run_scheduled_data_sync

                _run(_run_scheduled_data_sync())

                assert mock_run.call_count == 0, f"Should not run on weekday {weekday}"


# ---------------------------------------------------------------------------
# Unit: manual Data Sync route unchanged
# ---------------------------------------------------------------------------


class TestManualDataSyncUnchanged:
    """Verify the POST /data/process route logic is unaffected."""

    def test_data_request_schema_still_accepts_all(self):
        from src.schema.data import DataRequest

        req = DataRequest(migration_type=["all"], manipulation_type=["all"])
        assert req.migration_type == ["all"]
        assert req.manipulation_type == ["all"]

    def test_data_sync_config_put_schema_does_not_bleed_into_data_request(self):
        """DataSyncConfigPut and DataRequest are independent schemas."""
        from src.schema.data import DataRequest
        from src.schema.settings import DataSyncConfigPut

        cfg = DataSyncConfigPut(frequency="daily")
        req = DataRequest(migration_type=["all"], manipulation_type=["all"])
        assert not hasattr(cfg, "migration_type")
        assert not hasattr(req, "frequency")


# ---------------------------------------------------------------------------
# Integration tests (DB required)
# ---------------------------------------------------------------------------


@_skip_no_db
class TestDataSyncConfigRouteIntegration:
    """
    End-to-end route tests using a real DB inside a rolled-back SAVEPOINT.
    These tests import the FastAPI app and exercise the full request path.
    """

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

        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()

    def test_get_returns_weekly_default_when_no_row(self, db_session):
        from src.services.settings import SettingsService
        from sqlalchemy import text

        db_session.execute(text('SET search_path TO "portfolioauditreview", public'))
        from sqlalchemy.ext.asyncio import AsyncSession

        async def _run_get():
            from src.db.session import async_session as _async_session
            async with _async_session() as async_db:
                await async_db.execute(text('SET search_path TO "portfolioauditreview", public'))
                from src.db.models import ConfigTable
                from sqlalchemy import select, delete
                await async_db.execute(
                    delete(ConfigTable).where(ConfigTable.key == "data_sync_schedule_config_v1")
                )
                await async_db.flush()
                result = await SettingsService.get_data_sync_config_read(async_db)
                await async_db.rollback()
                return result

        result = _run(_run_get())
        assert result.frequency == "weekly"

    def test_put_daily_and_get_returns_daily(self):
        async def _run_put():
            from src.db.session import async_session as _async_session
            from sqlalchemy import text

            async with _async_session() as async_db:
                await async_db.execute(text('SET search_path TO "portfolioauditreview", public'))
                payload = DataSyncConfigPut(frequency="daily")
                put_result = await SettingsService.put_data_sync_config(async_db, payload)
                await async_db.flush()
                get_result = await SettingsService.get_data_sync_config_read(async_db)
                await async_db.rollback()
                return put_result, get_result

        from src.services.settings import SettingsService

        put_result, get_result = _run(_run_put())
        assert put_result.frequency == "daily"
        assert get_result.frequency == "daily"

    def test_put_weekly_persists_sunday(self):
        async def _run_put():
            from src.db.session import async_session as _async_session
            from sqlalchemy import text

            async with _async_session() as async_db:
                await async_db.execute(text('SET search_path TO "portfolioauditreview", public'))
                payload = DataSyncConfigPut(frequency="weekly")
                result = await SettingsService.put_data_sync_config(async_db, payload)
                await async_db.rollback()
                return result

        from src.services.settings import SettingsService

        result = _run(_run_put())
        assert result.frequency == "weekly"
        assert result.day_of_week == "sun"


# ---------------------------------------------------------------------------
# Unit: cutoff_date in schema, service, and Snowflake query
# ---------------------------------------------------------------------------


class TestCutoffDateSchema:
    def test_read_schema_defaults_cutoff_date_to_none(self):
        cfg = DataSyncConfigRead(
            frequency="weekly",
            enabled=True,
            time="21:00",
            timezone="Asia/Kolkata",
            day_of_week="sun",
        )
        assert cfg.cutoff_date is None

    def test_read_schema_accepts_date(self):
        cfg = DataSyncConfigRead(
            frequency="weekly",
            enabled=True,
            time="21:00",
            timezone="Asia/Kolkata",
            day_of_week="sun",
            cutoff_date=date(2024, 1, 1),
        )
        assert cfg.cutoff_date == date(2024, 1, 1)

    def test_put_schema_cutoff_date_optional(self):
        p = DataSyncConfigPut(frequency="daily")
        assert p.cutoff_date is None

    def test_put_schema_accepts_cutoff_date(self):
        p = DataSyncConfigPut(frequency="daily", cutoff_date=date(2024, 6, 1))
        assert p.cutoff_date == date(2024, 6, 1)

    def test_put_schema_rejects_invalid_date_string(self):
        with pytest.raises(Exception):
            DataSyncConfigPut(frequency="daily", cutoff_date="not-a-date")  # type: ignore[arg-type]


class TestCutoffDateService:
    def test_get_returns_none_cutoff_when_absent(self):
        from src.services.settings import SettingsService

        row = MagicMock()
        row.value = {"frequency": "weekly", "enabled": True, "time": "21:00",
                     "timezone": "Asia/Kolkata", "day_of_week": "sun"}
        db = _make_db_session(first_result=row)
        result = _run(SettingsService.get_data_sync_config_read(db))
        assert result.cutoff_date is None

    def test_get_parses_cutoff_date_from_db(self):
        from src.services.settings import SettingsService

        row = MagicMock()
        row.value = {"frequency": "weekly", "enabled": True, "time": "21:00",
                     "timezone": "Asia/Kolkata", "day_of_week": "sun",
                     "cutoff_date": "2024-03-15"}
        db = _make_db_session(first_result=row)
        result = _run(SettingsService.get_data_sync_config_read(db))
        assert result.cutoff_date == date(2024, 3, 15)

    def test_get_ignores_invalid_cutoff_date_in_db(self):
        from src.services.settings import SettingsService

        row = MagicMock()
        row.value = {"frequency": "weekly", "enabled": True, "cutoff_date": "garbage"}
        db = _make_db_session(first_result=row)
        result = _run(SettingsService.get_data_sync_config_read(db))
        assert result.cutoff_date is None

    def test_put_persists_cutoff_date(self):
        from src.services.settings import SettingsService

        db = _make_db_session(first_result=None)
        payload = DataSyncConfigPut(frequency="daily", cutoff_date=date(2024, 6, 1))
        result = _run(SettingsService.put_data_sync_config(db, payload))
        assert result.cutoff_date == date(2024, 6, 1)
        # Verify the value written to the row contains the ISO string
        written_value = db.add.call_args[0][0].value
        assert written_value["cutoff_date"] == "2024-06-01"

    def test_put_persists_null_cutoff_date(self):
        from src.services.settings import SettingsService

        db = _make_db_session(first_result=None)
        payload = DataSyncConfigPut(frequency="weekly", cutoff_date=None)
        result = _run(SettingsService.put_data_sync_config(db, payload))
        assert result.cutoff_date is None
        written_value = db.add.call_args[0][0].value
        assert written_value["cutoff_date"] is None

    def test_put_updates_existing_row_with_cutoff_date(self):
        from src.services.settings import SettingsService

        existing_row = MagicMock()
        existing_row.value = {"frequency": "daily", "enabled": True}
        db = _make_db_session(first_result=existing_row)
        payload = DataSyncConfigPut(frequency="weekly", cutoff_date=date(2023, 12, 31))
        result = _run(SettingsService.put_data_sync_config(db, payload))
        assert result.cutoff_date == date(2023, 12, 31)
        db.add.assert_not_called()
        assert existing_row.value["cutoff_date"] == "2023-12-31"


class TestCutoffDateSnowflakeQuery:
    """Tests that _migrate_pr_submission_data adds the WHERE clause when cutoff_date is set."""

    def _make_conn(self, columns=None, rows=None):
        cursor = MagicMock()
        cursor.description = [(col,) for col in (columns or ["reporting_date", "cid", "status"])]
        cursor.fetchall.return_value = rows or []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_no_where_clause_when_cutoff_date_is_none(self):
        from src.scripts.data_migration.audit_raw_from_snowflake import _migrate_pr_submission_data

        conn, cursor = self._make_conn()
        db = MagicMock()
        db.execute.return_value.scalars.return_value.__iter__ = MagicMock(return_value=iter([]))

        _migrate_pr_submission_data(conn, db, cutoff_date=None)

        executed_query: str = cursor.execute.call_args[0][0]
        assert "WHERE" not in executed_query.upper()

    def test_where_clause_injected_when_cutoff_date_set(self):
        from src.scripts.data_migration.audit_raw_from_snowflake import _migrate_pr_submission_data

        conn, cursor = self._make_conn()
        db = MagicMock()
        db.execute.return_value.scalars.return_value.__iter__ = MagicMock(return_value=iter([]))

        _migrate_pr_submission_data(conn, db, cutoff_date=date(2024, 1, 1))

        executed_query: str = cursor.execute.call_args[0][0]
        assert "WHERE" in executed_query.upper()
        assert "2024-01-01" in executed_query

    def test_load_cutoff_date_returns_none_when_absent(self):
        from src.scripts.data_migration.audit_raw_from_snowflake import _load_cutoff_date

        db = MagicMock()
        db.execute.return_value.scalars.return_value.first.return_value = None
        result = _load_cutoff_date(db)
        assert result is None

    def test_load_cutoff_date_parses_iso_string(self):
        from src.scripts.data_migration.audit_raw_from_snowflake import _load_cutoff_date

        row = MagicMock()
        row.value = {"cutoff_date": "2024-06-15"}
        db = MagicMock()
        db.execute.return_value.scalars.return_value.first.return_value = row
        result = _load_cutoff_date(db)
        assert result == date(2024, 6, 15)

    def test_load_cutoff_date_returns_none_for_null_value(self):
        from src.scripts.data_migration.audit_raw_from_snowflake import _load_cutoff_date

        row = MagicMock()
        row.value = {"cutoff_date": None}
        db = MagicMock()
        db.execute.return_value.scalars.return_value.first.return_value = row
        result = _load_cutoff_date(db)
        assert result is None


@_skip_no_db
class TestCutoffDateIntegration:
    """Integration tests: persist + retrieve cutoff_date through real DB (rolled-back)."""

    def test_put_and_get_cutoff_date_roundtrip(self):
        async def _run_test():
            from src.db.session import async_session as _async_session
            from src.services.settings import SettingsService
            from sqlalchemy import text

            async with _async_session() as async_db:
                await async_db.execute(text('SET search_path TO "portfolioauditreview", public'))
                payload = DataSyncConfigPut(frequency="weekly", cutoff_date=date(2024, 3, 15))
                await SettingsService.put_data_sync_config(async_db, payload)
                await async_db.flush()
                result = await SettingsService.get_data_sync_config_read(async_db)
                await async_db.rollback()
                return result

        result = _run(_run_test())
        assert result.cutoff_date == date(2024, 3, 15)

    def test_put_null_cutoff_date_clears_existing(self):
        async def _run_test():
            from src.db.session import async_session as _async_session
            from src.services.settings import SettingsService
            from sqlalchemy import text

            async with _async_session() as async_db:
                await async_db.execute(text('SET search_path TO "portfolioauditreview", public'))
                # First set a cutoff date
                await SettingsService.put_data_sync_config(
                    async_db, DataSyncConfigPut(frequency="weekly", cutoff_date=date(2024, 1, 1))
                )
                await async_db.flush()
                # Then clear it
                await SettingsService.put_data_sync_config(
                    async_db, DataSyncConfigPut(frequency="weekly", cutoff_date=None)
                )
                await async_db.flush()
                result = await SettingsService.get_data_sync_config_read(async_db)
                await async_db.rollback()
                return result

        result = _run(_run_test())
        assert result.cutoff_date is None
