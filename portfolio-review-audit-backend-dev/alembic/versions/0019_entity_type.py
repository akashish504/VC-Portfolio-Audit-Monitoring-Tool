"""Add optional entity_type to entities.

Revision ID: 0019_entity_type
Revises: 0018_portfolio_company_per_cycle
Create Date: 2026-05-21
"""

from __future__ import annotations

from alembic import op

revision = "0019_entity_type"
down_revision = "0018_portfolio_company_per_cycle"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".entities '
        f'ADD COLUMN IF NOT EXISTS entity_type VARCHAR(32)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_entities_entity_type '
        f'ON "{APP_SCHEMA}".entities (entity_type)'
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS "{APP_SCHEMA}".ix_entities_entity_type')
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".entities '
        f'DROP COLUMN IF EXISTS entity_type'
    )
