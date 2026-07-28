"""Add cutoff_date field to data_sync_schedule_config_v1 config row.

Revision ID: 0046_data_sync_config_cutoff_date
Revises: 0045_files_original_currency
Create Date: 2026-06-04
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0046_data_sync_config_cutoff_date"
down_revision = "0045_files_original_currency"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    log = logging.getLogger("alembic")
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            UPDATE "{S}".config_table
               SET value = (value::jsonb || '{{"cutoff_date": "2023-01-01"}}'::jsonb)::json
             WHERE key = 'data_sync_schedule_config_v1'
               AND NOT (value::jsonb ? 'cutoff_date')
            """
        )
    )
    log.info("[0046] Added cutoff_date field to data_sync_schedule_config_v1")


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            UPDATE "{S}".config_table
               SET value = value - 'cutoff_date'
             WHERE key = 'data_sync_schedule_config_v1'
            """
        )
    )
