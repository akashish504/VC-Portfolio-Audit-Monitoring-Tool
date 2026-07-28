"""Add entity lifecycle date fields and EmailHistory.affected_entity_ids.

Revision ID: 0025_entity_lifecycle_dates_email_entity_ids
Revises: 0024_files_entity_detached_acknowledged
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0025_entity_lifecycle_dates_email_entity_ids"
down_revision = "0024_files_entity_detached_acknowledged"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"""
        ALTER TABLE "{S}".entities
          ADD COLUMN IF NOT EXISTS discrepancy_email_sent_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS reminder_1_sent_at        TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS reminder_2_sent_at        TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS first_reply_received_at   TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS resolved_at               TIMESTAMPTZ
    """))

    conn.execute(text(f"""
        ALTER TABLE "{S}".email_history
          ADD COLUMN IF NOT EXISTS affected_entity_ids INTEGER[]
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"""
        ALTER TABLE "{S}".entities
          DROP COLUMN IF EXISTS discrepancy_email_sent_at,
          DROP COLUMN IF EXISTS reminder_1_sent_at,
          DROP COLUMN IF EXISTS reminder_2_sent_at,
          DROP COLUMN IF EXISTS first_reply_received_at,
          DROP COLUMN IF EXISTS resolved_at
    """))
    conn.execute(text(f"""
        ALTER TABLE "{S}".email_history
          DROP COLUMN IF EXISTS affected_entity_ids
    """))
