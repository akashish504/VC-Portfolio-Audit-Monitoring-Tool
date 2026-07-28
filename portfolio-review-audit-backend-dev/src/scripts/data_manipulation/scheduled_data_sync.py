"""
APScheduler job: run the data-sync pipeline automatically at 21:00 IST.

Reads config from config_table at runtime so changes take effect without
a restart.  The job is registered as a daily cron; weekly mode is handled
inside the function by skipping non-matching weekdays.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

import pytz
from sqlalchemy import select, text

from src.db.models import APP_SCHEMA, DataSyncProcessBatch
from src.db.session import async_session
from src.schema.data import DataRequest
from src.services.data_service import DataService
from src.services.data_sync_schedule_config import load_data_sync_schedule_config

logger = logging.getLogger(__name__)

_WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


async def _run_scheduled_data_sync() -> None:
    async with async_session() as session:
        await session.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        config = await load_data_sync_schedule_config(session)

        if not config.enabled:
            logger.info("Scheduled data sync is disabled; skipping.")
            return

        if config.frequency == "weekly":
            tz = pytz.timezone(config.timezone)
            today_weekday = datetime.now(tz).weekday()
            expected_weekday = _WEEKDAY_MAP.get(config.day_of_week.lower(), 0)
            if today_weekday != expected_weekday:
                logger.info(
                    "Scheduled data sync (weekly): today is weekday %d, configured day is %s (%d); skipping.",
                    today_weekday,
                    config.day_of_week,
                    expected_weekday,
                )
                return

        result = await session.execute(
            select(DataSyncProcessBatch)
            .where(DataSyncProcessBatch.status != "SUCCESS")
            .order_by(DataSyncProcessBatch.created_at.desc())
        )
        existing = result.scalars().first()
        if existing:
            logger.info(
                "Scheduled data sync: batch %s is already %s; skipping to prevent overlap.",
                existing.id,
                existing.status,
            )
            return

        batch_id = str(uuid.uuid4())
        batch = DataSyncProcessBatch(id=batch_id, status="QUEUED")
        session.add(batch)
        await session.flush()
        await session.refresh(batch)
        await session.commit()
        logger.info("Scheduled data sync: created batch %s", batch_id)

    data_request = DataRequest(migration_type=["all"], manipulation_type=["all"])
    try:
        await DataService.run_in_background(batch_id, data_request)
        logger.info("Scheduled data sync: batch %s completed", batch_id)
    except Exception:
        logger.exception("Scheduled data sync: batch %s raised an unexpected error", batch_id)


def run_scheduled_data_sync() -> None:
    """Synchronous entry point called by APScheduler."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_run_scheduled_data_sync())
        else:
            loop.run_until_complete(_run_scheduled_data_sync())
    except Exception:
        logger.exception("Scheduled data sync: unexpected error in job wrapper")
