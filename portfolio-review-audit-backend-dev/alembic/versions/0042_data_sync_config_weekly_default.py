"""Update data_sync_schedule_config_v1 to enabled=true, frequency=weekly, day_of_week=sun.

Revision ID: 0042_data_sync_config_weekly_default
Revises: 0041_data_sync_schedule_config
Create Date: 2026-06-02
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0042_data_sync_config_weekly_default"
down_revision = "0041_data_sync_schedule_config"
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
                '{{"enabled": true, "frequency": "weekly", "time": "21:00",
                   "timezone": "Asia/Kolkata", "day_of_week": "sun"}}'::jsonb,
                'Automatic data sync schedule configuration'
            )
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  description = EXCLUDED.description
            """
        )
    )
    log.info("[0042] Upserted config_table row: data_sync_schedule_config_v1 (weekly/sun/enabled)")


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            UPDATE "{S}".config_table
               SET value = '{{"enabled": false, "frequency": "daily", "time": "21:00",
                              "timezone": "Asia/Kolkata", "day_of_week": "mon"}}'::jsonb
             WHERE key = 'data_sync_schedule_config_v1'
            """
        )
    )
