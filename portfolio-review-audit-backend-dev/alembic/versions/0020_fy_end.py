"""Add portfolio_companies.fy_end (Mmm-YY).

Revision ID: 0020_fy_end
Revises: 0019_entity_type
Create Date: 2026-05-21
"""

from __future__ import annotations

from alembic import op

revision = "0020_fy_end"
down_revision = "0019_entity_type"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'ADD COLUMN IF NOT EXISTS fy_end VARCHAR(16)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_fy_end '
        f'ON "{APP_SCHEMA}".portfolio_companies (fy_end)'
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS "{APP_SCHEMA}".ix_portfolio_companies_fy_end')
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'DROP COLUMN IF EXISTS fy_end'
    )
