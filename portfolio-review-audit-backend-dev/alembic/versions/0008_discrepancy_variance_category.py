"""Add variance_category text column to discrepancies.

Revision ID: 0008_discrepancy_variance_category
Revises: 0007_financial_data_snowflake_review_cycle
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0008_discrepancy_variance_category"
down_revision = "0007_financial_data_snowflake_review_cycle"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".discrepancies
              ADD COLUMN IF NOT EXISTS variance_category TEXT NULL;
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'ALTER TABLE "{S}".discrepancies DROP COLUMN IF EXISTS variance_category'))
