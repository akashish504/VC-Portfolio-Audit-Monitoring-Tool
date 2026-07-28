"""Add mapping_uploaded_at to org_chart_upload_batches.

Revision ID: 0047_org_chart_batch_mapping_uploaded_at
Revises: 0046_data_sync_config_cutoff_date
Create Date: 2026-06-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0047_org_chart_batch_mapping_uploaded_at"
down_revision = "0046_data_sync_config_cutoff_date"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "org_chart_upload_batches",
        sa.Column("mapping_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("org_chart_upload_batches", "mapping_uploaded_at", schema=S)
