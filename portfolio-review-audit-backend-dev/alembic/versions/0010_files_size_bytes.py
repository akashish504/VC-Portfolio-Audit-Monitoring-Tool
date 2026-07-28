"""Add size_bytes to files (S3 object byte length).

Revision ID: 0010_files_size_bytes
Revises: 0009_file_ocr_auditor_opinion
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0010_files_size_bytes"
down_revision = "0009_file_ocr_auditor_opinion"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".files
              ADD COLUMN IF NOT EXISTS size_bytes BIGINT NULL;
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'ALTER TABLE "{S}".files DROP COLUMN IF EXISTS size_bytes'))
