"""Monthly FX refresh job.

Runs on the 1st of each month: fetches the previous month-end USD-> all
configured quote-currency rates from XE in a single call and upserts them into
``fx_monthly_rate``. This is the only routine path that calls XE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.db.session import async_session
from src.services.fx_service import fetch_monthly_rate, previous_month_end

logger = logging.getLogger(__name__)


async def run_fx_monthly_refresh() -> None:
    """Fetch & store last month-end USD rates. Safe to re-run (idempotent)."""
    today = datetime.now(timezone.utc).date()
    month_end = previous_month_end(today)
    logger.info("FX monthly refresh: fetching rates for month-end %s", month_end.isoformat())

    async with async_session() as db:
        try:
            written = await fetch_monthly_rate(db, month_end=month_end)
            await db.commit()
            logger.info(
                "FX monthly refresh complete: %d rows for %s", written, month_end.isoformat()
            )
        except Exception:
            await db.rollback()
            logger.error(
                "FX monthly refresh failed for %s", month_end.isoformat(), exc_info=True
            )
            raise
