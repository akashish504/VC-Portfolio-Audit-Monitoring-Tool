"""Add entity_detached_acknowledged to files table.

Revision ID: 0024_files_entity_detached_acknowledged
Revises: 0023_files_review_cycle_id
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0024_files_entity_detached_acknowledged"
down_revision = "0023_files_review_cycle_id"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        f"""
        ALTER TABLE "{S}".files
          ADD COLUMN IF NOT EXISTS entity_detached_acknowledged BOOLEAN NOT NULL DEFAULT FALSE
        """
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        f'ALTER TABLE "{S}".files DROP COLUMN IF EXISTS entity_detached_acknowledged'
    ))
