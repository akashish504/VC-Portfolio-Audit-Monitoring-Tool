"""Add deal_id_for_analysis and deal_id_for_analysis_and_strategy to portfolio_company_metadata.

Revision ID: 0060_pcm_deal_id_for_analysis
Revises: 0059_file_ocr_metadata_metric_breakdown
Create Date: 2026-06-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_pcm_deal_id_for_analysis"
down_revision = "0059_file_ocr_metadata_metric_breakdown"
branch_labels = None
depends_on = None

_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "portfolio_company_metadata",
        sa.Column("deal_id_for_analysis", sa.String(100), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "portfolio_company_metadata",
        sa.Column("deal_id_for_analysis_and_strategy", sa.String(100), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(
        "portfolio_company_metadata",
        "deal_id_for_analysis_and_strategy",
        schema=_SCHEMA,
    )
    op.drop_column(
        "portfolio_company_metadata",
        "deal_id_for_analysis",
        schema=_SCHEMA,
    )
