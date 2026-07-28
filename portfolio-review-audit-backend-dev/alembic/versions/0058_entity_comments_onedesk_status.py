"""Add comments and one_desk_email_status to entities table.

Revision ID: 0058_entity_comments_onedesk_status
Revises: 0057_fx_monthly_rate
Create Date: 2026-06-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058_entity_comments_onedesk_status"
down_revision = "0057_fx_monthly_rate"
branch_labels = None
depends_on = None

_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column("comments", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "entities",
        sa.Column("one_desk_email_status", sa.String(128), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("entities", "one_desk_email_status", schema=_SCHEMA)
    op.drop_column("entities", "comments", schema=_SCHEMA)
