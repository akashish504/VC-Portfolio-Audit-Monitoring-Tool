"""Portfolio company unique per (company_id, review_cycle_id).

Revision ID: 0018_portfolio_company_per_cycle
Revises: 0017_snowflake_pr_financial_mapping
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op

revision = "0018_portfolio_company_per_cycle"
down_revision = "0017_snowflake_pr_financial_mapping"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'DROP CONSTRAINT IF EXISTS uq_portfolio_companies_company_id'
    )
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'ADD CONSTRAINT uq_portfolio_companies_company_cycle '
        f'UNIQUE (company_id, review_cycle_id)'
    )


def downgrade() -> None:
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'DROP CONSTRAINT IF EXISTS uq_portfolio_companies_company_cycle'
    )
    op.execute(
        f'ALTER TABLE "{APP_SCHEMA}".portfolio_companies '
        f'ADD CONSTRAINT uq_portfolio_companies_company_id UNIQUE (company_id)'
    )
