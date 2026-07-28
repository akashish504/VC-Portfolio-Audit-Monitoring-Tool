"""Add audit_qualitative JSON to file_ocr_metadata.

Revision ID: 0011_file_ocr_audit_qualitative
Revises: 0010_files_size_bytes
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0011_file_ocr_audit_qualitative"
down_revision = "0010_files_size_bytes"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".file_ocr_metadata
              ADD COLUMN IF NOT EXISTS audit_qualitative JSON NULL;
            """
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'ALTER TABLE "{S}".file_ocr_metadata DROP COLUMN IF EXISTS audit_qualitative'))
