"""Add metric_breakdown column to file_ocr_metadata.

Stores per-file financial metric breakdown (computed from the current mapping config)
independently of whether the file is the primary AFS reconciliation source.

Revision ID: 0059_file_ocr_metadata_metric_breakdown
Revises: 0058_entity_comments_onedesk_status
Create Date: 2026-06-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_file_ocr_metadata_metric_breakdown"
down_revision = "0058_entity_comments_onedesk_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_ocr_metadata",
        sa.Column("metric_breakdown", sa.JSON(), nullable=True),
        schema="portfolioauditreview",
    )


def downgrade() -> None:
    op.drop_column("file_ocr_metadata", "metric_breakdown", schema="portfolioauditreview")
