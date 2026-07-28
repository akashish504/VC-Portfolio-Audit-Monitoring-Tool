"""Drop in_review_status; widen review_stage to 128 chars.

Consolidates audit workflow state into review_stage (16-value enum).
in_review_status is no longer used by the application.

Revision ID: 0049_review_stage_migration
Revises: 0048_files_is_reconciliation_source
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_review_stage_migration"
down_revision = "0048_files_is_reconciliation_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("portfolio_companies", schema="portfolioauditreview") as batch_op:
        batch_op.drop_column("in_review_status")
        batch_op.alter_column(
            "review_stage",
            existing_type=sa.String(64),
            type_=sa.String(128),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("portfolio_companies", schema="portfolioauditreview") as batch_op:
        batch_op.alter_column(
            "review_stage",
            existing_type=sa.String(128),
            type_=sa.String(64),
            existing_nullable=True,
        )
        batch_op.add_column(
            sa.Column("in_review_status", sa.String(64), nullable=True)
        )
