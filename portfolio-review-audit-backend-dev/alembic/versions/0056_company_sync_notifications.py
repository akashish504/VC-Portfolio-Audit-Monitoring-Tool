"""Create company_sync_notifications table for sync alert feature.

Revision ID: 0056_company_sync_notifications
Revises: 0055_files_source_email_provenance
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_company_sync_notifications"
down_revision = "0055_files_source_email_provenance"
branch_labels = None
depends_on = None

_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.create_table(
        "company_sync_notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.String(64), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("investment_stage", sa.String(64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_email", sa.String(320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_company_sync_notifications_company_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_company_sync_notifications_is_read_created_at",
        "company_sync_notifications",
        ["is_read", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_company_sync_notifications_id",
        "company_sync_notifications",
        ["id"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_sync_notifications_is_read_created_at",
        table_name="company_sync_notifications",
        schema=_SCHEMA,
    )
    op.drop_index(
        "ix_company_sync_notifications_id",
        table_name="company_sync_notifications",
        schema=_SCHEMA,
    )
    op.drop_table("company_sync_notifications", schema=_SCHEMA)
