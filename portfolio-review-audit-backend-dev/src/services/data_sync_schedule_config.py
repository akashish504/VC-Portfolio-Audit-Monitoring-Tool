"""
Loads data-sync schedule config from config_table and applies defaults.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CONFIG_KEY = "data_sync_schedule_config_v1"

_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "frequency": "weekly",
    "time": "21:00",
    "timezone": "Asia/Kolkata",
    "day_of_week": "sun",
}


class DataSyncScheduleConfig:
    def __init__(
        self,
        enabled: bool,
        frequency: Literal["daily", "weekly"],
        time: str,
        timezone: str,
        day_of_week: str,
    ) -> None:
        self.enabled = enabled
        self.frequency = frequency
        self.time = time
        self.timezone = timezone
        self.day_of_week = day_of_week

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


async def load_data_sync_schedule_config(session: AsyncSession) -> DataSyncScheduleConfig:
    """Load schedule config from config_table; return defaults if row is absent or keys missing."""
    from src.db.models import ConfigTable  # avoid circular at module level

    try:
        result = await session.execute(
            select(ConfigTable).where(ConfigTable.key == CONFIG_KEY)
        )
        row = result.scalars().first()
        raw: Dict[str, Any] = (row.value or {}) if row is not None else {}
    except Exception:
        logger.exception("Failed to load %s from config_table; using defaults", CONFIG_KEY)
        raw = {}

    def _get(key: str) -> Any:
        return raw.get(key, _DEFAULTS[key])

    return DataSyncScheduleConfig(
        enabled=bool(_get("enabled")),
        frequency=_get("frequency"),
        time=_get("time"),
        timezone=_get("timezone"),
        day_of_week=_get("day_of_week"),
    )
