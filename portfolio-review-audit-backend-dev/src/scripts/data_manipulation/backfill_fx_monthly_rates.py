"""Backfill month-end USD FX rates into ``fx_monthly_rate``.

Fetches the last N month-ends (default 24) from XE — one call per month, each
covering all configured quote currencies — and upserts them. Idempotent: months
already stored are skipped unless ``--overwrite``. Throttled between calls to
stay well under XE rate limits.

Usage:
    python -m src.scripts.data_manipulation.backfill_fx_monthly_rates
    python -m src.scripts.data_manipulation.backfill_fx_monthly_rates --months 24 --sleep 2.0
    python -m src.scripts.data_manipulation.backfill_fx_monthly_rates --overwrite
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, timezone

from src.db.session import async_session
from src.services.fx_service import _month_end, fetch_monthly_rate

logger = logging.getLogger(__name__)

_DEFAULT_MONTHS = 24
_DEFAULT_SLEEP_S = 2.0


def _month_ends_back(count: int, *, today: date) -> list[date]:
    """Return the last ``count`` month-ends, oldest first, ending at the most
    recently completed month (i.e. the month before ``today``'s month)."""
    ends: list[date] = []
    # Start from the last day of the previous month.
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    for _ in range(count):
        ends.append(_month_end(date(year, month, 1)))
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
    return sorted(ends)


async def backfill(*, months: int, sleep_s: float, overwrite: bool) -> None:
    today = datetime.now(timezone.utc).date()
    targets = _month_ends_back(months, today=today)
    logger.info(
        "FX backfill: %d months from %s to %s (overwrite=%s)",
        len(targets),
        targets[0].isoformat(),
        targets[-1].isoformat(),
        overwrite,
    )

    total_calls = 0
    total_rows = 0
    for i, month_end in enumerate(targets):
        async with async_session() as db:
            try:
                written = await fetch_monthly_rate(db, month_end=month_end, overwrite=overwrite)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.error("FX backfill failed for %s", month_end.isoformat(), exc_info=True)
                raise
        if written > 0:
            total_calls += 1
            total_rows += written
        # Throttle only between actual XE calls (skipped months cost nothing).
        if written > 0 and i < len(targets) - 1:
            await asyncio.sleep(sleep_s)

    logger.info(
        "FX backfill complete: %d XE calls, %d rows stored across %d months",
        total_calls,
        total_rows,
        len(targets),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill month-end USD FX rates from XE.")
    parser.add_argument("--months", type=int, default=_DEFAULT_MONTHS, help="Months to backfill (default 24).")
    parser.add_argument("--sleep", type=float, default=_DEFAULT_SLEEP_S, help="Seconds between XE calls (default 2.0).")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite months already stored.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(backfill(months=args.months, sleep_s=args.sleep, overwrite=args.overwrite))


if __name__ == "__main__":
    main()
