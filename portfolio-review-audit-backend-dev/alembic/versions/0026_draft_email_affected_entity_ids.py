"""Add affected_entity_ids to draft_email table.

Revision ID: 0026_draft_email_affected_entity_ids
Revises: 0025_entity_lifecycle_dates_email_entity_ids
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0026_draft_email_affected_entity_ids"
down_revision = "0025_entity_lifecycle_dates_email_entity_ids"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"""
        ALTER TABLE "{S}".draft_email
          ADD COLUMN IF NOT EXISTS affected_entity_ids INTEGER[]
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"""
        ALTER TABLE "{S}".draft_email
          DROP COLUMN IF EXISTS affected_entity_ids
    """))
