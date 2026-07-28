"""Add auditor_opinion JSON to file_ocr_metadata.

Revision ID: 0009_file_ocr_auditor_opinion
Revises: 0008_discrepancy_variance_category
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009_file_ocr_auditor_opinion"
down_revision = "0008_discrepancy_variance_category"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".file_ocr_metadata
              ADD COLUMN IF NOT EXISTS auditor_opinion JSON NULL;
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'ALTER TABLE "{S}".file_ocr_metadata DROP COLUMN IF EXISTS auditor_opinion'))
