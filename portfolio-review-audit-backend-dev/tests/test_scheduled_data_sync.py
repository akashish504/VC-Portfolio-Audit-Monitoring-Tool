"""
Tests for the scheduled data-sync job.

Two layers:
  * Pure-logic / unit tests that mock all I/O — always run.
  * Integration tests against the configured Postgres DB inside a SAVEPOINT
    that is rolled back at the end, so test data never persists.  These are
    auto-skipped when no DB is reachable (e.g. CI without secrets).

Run:
    PYTHONPATH=. pytest tests/test_scheduled_data_sync.py -v
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.data_sync_schedule_config import (
    CONFIG_KEY,
    DataSyncScheduleConfig,
    _DEFAULTS,
    load_data_sync_schedule_config,
)


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
    """Run an async coroutine synchronously (no pytest-asyncio required)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_config(**kwargs: Any) -> DataSyncScheduleConfig:
    defaults = dict(
        enabled=True,
        frequency="daily",
        time="21:00",
        timezone="Asia/Kolkata",
        day_of_week="mon",
    )
    defaults.update(kwargs)
    return DataSyncScheduleConfig(**defaults)  # type: ignore[arg-type]


def _make_mock_session(first_result=None, side_effect=None):
    """Return an AsyncMock that behaves like an async_session context manager.

    AsyncMock makes ALL attribute accesses return coroutines, so we need to
    wire the execute() chain using MagicMock for the synchronous parts
    (.scalars(), .first()) and only make execute() itself awaitable.
    """
    scalars_result = MagicMock()
    scalars_result.first.return_value = first_result

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    mock_session = MagicMock()
    if side_effect is not None:
        mock_session.execute = AsyncMock(side_effect=side_effect)
    else:
        mock_session.execute = AsyncMock(return_value=execute_result)
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ---------------------------------------------------------------------------
# Unit: config default loading
# ---------------------------------------------------------------------------


def _make_config_session(row=None, raise_exc=None):
    """AsyncSession mock for load_data_sync_schedule_config — only needs execute()."""
    session = _make_mock_session(first_result=row, side_effect=raise_exc)
    return session


class TestDataSyncScheduleConfigDefaults:
    """load_data_sync_schedule_config returns correct defaults when row is absent."""

    def test_returns_defaults_when_row_absent(self):
        session = _make_config_session(row=None)

        cfg = _run(load_data_sync_schedule_config(session))

        assert cfg.enabled == _DEFAULTS["enabled"]
        assert cfg.frequency == _DEFAULTS["frequency"]
        assert cfg.time == _DEFAULTS["time"]
        assert cfg.timezone == _DEFAULTS["timezone"]
        assert cfg.day_of_week == _DEFAULTS["day_of_week"]

    def test_returns_defaults_when_db_raises(self):
        session = _make_config_session(raise_exc=Exception("db error"))

        cfg = _run(load_data_sync_schedule_config(session))

        assert cfg.enabled == _DEFAULTS["enabled"]
        assert cfg.frequency == _DEFAULTS["frequency"]

    def test_partial_overrides_use_defaults_for_missing_keys(self):
        row = MagicMock()
        row.value = {"enabled": True, "frequency": "weekly"}
        session = _make_config_session(row=row)

        cfg = _run(load_data_sync_schedule_config(session))

        assert cfg.enabled is True
        assert cfg.frequency == "weekly"
        assert cfg.time == _DEFAULTS["time"]
        assert cfg.timezone == _DEFAULTS["timezone"]
        assert cfg.day_of_week == _DEFAULTS["day_of_week"]

    def test_full_config_from_row(self):
        row = MagicMock()
        row.value = {
            "enabled": True,
            "frequency": "weekly",
            "time": "09:00",
            "timezone": "UTC",
            "day_of_week": "fri",
        }
        session = _make_config_session(row=row)

        cfg = _run(load_data_sync_schedule_config(session))

        assert cfg.enabled is True
        assert cfg.frequency == "weekly"
        assert cfg.time == "09:00"
        assert cfg.timezone == "UTC"
        assert cfg.day_of_week == "fri"
        assert cfg.hour == 9
        assert cfg.minute == 0


# ---------------------------------------------------------------------------
# Unit: scheduled job behaviour
# ---------------------------------------------------------------------------


def _session_factory(first_result=None):
    """
    Return a callable that acts as async_session() — i.e. calling it returns
    an async context manager that yields a mock session.
    """
    mock_session = _make_mock_session(first_result=first_result)
    factory = MagicMock(return_value=mock_session)
    return factory, mock_session


class TestScheduledDataSyncJobUnit:
    """Unit tests — all DB / DataService calls are mocked."""

    def test_disabled_config_exits_without_creating_batch(self):
        cfg = _make_config(enabled=False)
        factory, mock_session = _session_factory()

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

            mock_session.add.assert_not_called()
            mock_run.assert_not_called()

    def test_skips_if_batch_already_queued(self):
        cfg = _make_config(enabled=True, frequency="daily")
        existing = MagicMock()
        existing.id = "existing-batch-id"
        existing.status = "QUEUED"
        factory, mock_session = _session_factory(first_result=existing)

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

            mock_session.add.assert_not_called()
            mock_run.assert_not_called()

    def test_skips_if_batch_already_in_progress(self):
        cfg = _make_config(enabled=True, frequency="daily")
        existing = MagicMock()
        existing.id = "existing-batch-id"
        existing.status = "IN_PROGRESS"
        factory, mock_session = _session_factory(first_result=existing)

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

            mock_session.add.assert_not_called()
            mock_run.assert_not_called()

    def test_creates_batch_and_calls_run_in_background_with_all(self):
        cfg = _make_config(enabled=True, frequency="daily")
        factory, mock_session = _session_factory(first_result=None)

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
            patch("src.scripts.data_manipulation.scheduled_data_sync.uuid") as mock_uuid,
        ):
            mock_uuid.uuid4.return_value = MagicMock(__str__=lambda _: "test-batch-uuid")

            from src.scripts.data_manipulation.scheduled_data_sync import _run_scheduled_data_sync

            _run(_run_scheduled_data_sync())

            mock_session.add.assert_called_once()
            added_batch = mock_session.add.call_args[0][0]
            assert added_batch.status == "QUEUED"

            mock_run.assert_called_once()
            _, call_data_request = mock_run.call_args[0]
            assert call_data_request.migration_type == ["all"]
            assert call_data_request.manipulation_type == ["all"]

    def test_weekly_config_skips_on_wrong_weekday(self):
        """A weekly job configured for monday must skip when today is tuesday."""
        cfg = _make_config(enabled=True, frequency="weekly", day_of_week="mon")
        factory, mock_session = _session_factory(first_result=None)

        mock_now = MagicMock()
        mock_now.weekday.return_value = 1  # Tuesday

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

            mock_run.assert_not_called()
            mock_session.add.assert_not_called()

    def test_weekly_config_runs_on_correct_weekday(self):
        """A weekly job configured for tuesday must run when today is tuesday."""
        cfg = _make_config(enabled=True, frequency="weekly", day_of_week="tue")
        factory, mock_session = _session_factory(first_result=None)

        mock_now = MagicMock()
        mock_now.weekday.return_value = 1  # Tuesday

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
