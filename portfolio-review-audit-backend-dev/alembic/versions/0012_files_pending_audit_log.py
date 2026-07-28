"""Add files.pending_audit_log for pre-tag file audit history.

Revision ID: 0012_files_pending_audit_log
Revises: 0011_file_ocr_audit_qualitative
Create Date: 2026-05-20
"""

from alembic import op

revision = "0012_files_pending_audit_log"
down_revision = "0011_file_ocr_audit_qualitative"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE "{APP_SCHEMA}".files
        ADD COLUMN IF NOT EXISTS pending_audit_log JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE "{APP_SCHEMA}".files
        DROP COLUMN IF EXISTS pending_audit_log
        """
    )
