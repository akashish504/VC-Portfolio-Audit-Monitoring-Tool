"""Resolve discrepancy/reminder template IDs from ConfigTable.

Config keys:
  email_templates.discrepancy  → template ID for the initial discrepancy email
  email_templates.reminder_1   → template ID for reminder 1
  email_templates.reminder_2   → template ID for reminder 2

Values are stored as JSON strings, e.g. {"value": "<uuid>"}.
If a key is missing, a descriptive ConfigurationError is raised instead of
silently falling back to name-based matching.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.models import ConfigTable

CONFIG_KEY_DISCREPANCY = "email_templates.discrepancy"
CONFIG_KEY_REMINDER_1 = "email_templates.reminder_1"
CONFIG_KEY_REMINDER_2 = "email_templates.reminder_2"


class ConfigurationError(Exception):
    """Raised when a required ConfigTable entry is absent or malformed."""


def _extract_template_id(row: ConfigTable, key: str) -> str:
    v = row.value
    if isinstance(v, dict):
        tid = v.get("value") or v.get("template_id") or v.get("id")
    elif isinstance(v, str):
        tid = v.strip()
    else:
        tid = None
    if not tid:
        raise ConfigurationError(
            f"ConfigTable key '{key}' exists but has no usable template ID. "
            f"Expected {{\"value\": \"<uuid>\"}} but got: {v!r}"
        )
    return str(tid).strip()


async def get_discrepancy_template_id(db: AsyncSession) -> str:
    row = (
        await db.execute(
            select(ConfigTable).where(ConfigTable.key == CONFIG_KEY_DISCREPANCY)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConfigurationError(
            f"ConfigTable key '{CONFIG_KEY_DISCREPANCY}' is not set. "
            "Please insert a row with the discrepancy email template ID."
        )
    return _extract_template_id(row, CONFIG_KEY_DISCREPANCY)


async def get_reminder_1_template_id(db: AsyncSession) -> str:
    row = (
        await db.execute(
            select(ConfigTable).where(ConfigTable.key == CONFIG_KEY_REMINDER_1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConfigurationError(
            f"ConfigTable key '{CONFIG_KEY_REMINDER_1}' is not set. "
            "Please insert a row with the reminder 1 email template ID."
        )
    return _extract_template_id(row, CONFIG_KEY_REMINDER_1)


async def get_reminder_2_template_id(db: AsyncSession) -> str:
    row = (
        await db.execute(
            select(ConfigTable).where(ConfigTable.key == CONFIG_KEY_REMINDER_2)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConfigurationError(
            f"ConfigTable key '{CONFIG_KEY_REMINDER_2}' is not set. "
            "Please insert a row with the reminder 2 email template ID."
        )
    return _extract_template_id(row, CONFIG_KEY_REMINDER_2)


# --------------------------------------------------------------------------- #
# Sync variants (used by the APScheduler / classify_incoming_emails job)
# --------------------------------------------------------------------------- #

def _get_template_id_sync(db: Session, key: str) -> str:
    row = db.query(ConfigTable).filter(ConfigTable.key == key).first()
    if row is None:
        raise ConfigurationError(
            f"ConfigTable key '{key}' is not set."
        )
    return _extract_template_id(row, key)


def get_discrepancy_template_id_sync(db: Session) -> str:
    return _get_template_id_sync(db, CONFIG_KEY_DISCREPANCY)
