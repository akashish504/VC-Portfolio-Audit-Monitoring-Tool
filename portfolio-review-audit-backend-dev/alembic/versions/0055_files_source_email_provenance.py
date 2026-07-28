"""Add source_email_id and source_attachment_key provenance columns to files.

Links each File row back to the EmailHistory row and S3 attachment key it was
created from. Null for manual uploads and org-chart uploads.

Revision ID: 0055_files_source_email_provenance
Revises: 0054_seed_parameter_thresholds
Create Date: 2026-06-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0055_files_source_email_provenance"
down_revision = "0054_seed_parameter_thresholds"
branch_labels = None
depends_on = None

_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("source_email_id", sa.String(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "files",
        sa.Column("source_attachment_key", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_files_source_email_id",
        "files",
        "email_history",
        ["source_email_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_files_source_email_id",
        "files",
        ["source_email_id"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_files_source_email_id", table_name="files", schema=_SCHEMA)
    op.drop_constraint("fk_files_source_email_id", "files", schema=_SCHEMA, type_="foreignkey")
    op.drop_column("files", "source_attachment_key", schema=_SCHEMA)
    op.drop_column("files", "source_email_id", schema=_SCHEMA)
