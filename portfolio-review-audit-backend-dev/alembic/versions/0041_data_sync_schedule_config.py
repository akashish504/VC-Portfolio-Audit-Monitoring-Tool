"""Seed config_table with the default data-sync schedule configuration.

Revision ID: 0041_data_sync_schedule_config
Revises: 0040_company_data_raw_company_master
Create Date: 2026-06-02
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0041_data_sync_schedule_config"
down_revision = "0040_company_data_raw_company_master"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    log = logging.getLogger("alembic")
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            INSERT INTO "{S}".config_table (key, value, description)
            VALUES (
                'data_sync_schedule_config_v1',
                '{{
                    "enabled": false,
                    "frequency": "daily",
                    "time": "21:00",
                    "timezone": "Asia/Kolkata",
                    "day_of_week": "mon"
                }}'::jsonb,
                'APScheduler config for the nightly data-sync job. '
                'Set enabled=true to activate. '
                'frequency: daily | weekly. '
                'time: HH:MM in the configured timezone. '
                'day_of_week: mon..sun (only used when frequency=weekly).'
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    log.info("[0041] Seeded config_table row: data_sync_schedule_config_v1")


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"DELETE FROM \"{S}\".config_table WHERE key = 'data_sync_schedule_config_v1'"
        )
    )
