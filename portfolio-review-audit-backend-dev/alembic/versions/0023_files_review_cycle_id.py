"""Add review_cycle_id to files table and backfill from portfolio_companies.

Revision ID: 0023_files_review_cycle_id
Revises: 0022_recon_reviewer_fields
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0023_files_review_cycle_id"
down_revision = "0022_recon_reviewer_fields"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    # Add the column
    conn.execute(text(
        f"""
        ALTER TABLE "{S}".files
          ADD COLUMN IF NOT EXISTS review_cycle_id VARCHAR(128)
          REFERENCES "{S}".review_cycles(id) ON DELETE SET NULL
        """
    ))

    # Index for query performance
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS "ix_files_review_cycle_id" ON "{S}".files (review_cycle_id)'
    ))

    # Backfill: derive review_cycle_id from the linked portfolio_company
    conn.execute(text(
        f"""
        UPDATE "{S}".files f
        SET review_cycle_id = pc.review_cycle_id
        FROM "{S}".portfolio_companies pc
        WHERE f.portfolio_company_id = pc.id
          AND pc.review_cycle_id IS NOT NULL
          AND f.review_cycle_id IS NULL
        """
    ))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(f'DROP INDEX IF EXISTS "{S}"."ix_files_review_cycle_id"'))
    conn.execute(text(f'ALTER TABLE "{S}".files DROP COLUMN IF EXISTS review_cycle_id'))
