"""Add company_phase_category column to portfolio_companies.

Revision ID: 0032_portfolio_company_phase_category
Revises: 0031_portfolio_company_investors
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_portfolio_company_phase_category"
down_revision = "0031_portfolio_company_investors"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "portfolio_companies",
        sa.Column("company_phase_category", sa.String(length=128), nullable=True),
        schema=S,
    )
    op.create_index(
        "ix_portfolio_companies_company_phase_category",
        "portfolio_companies",
        ["company_phase_category"],
        schema=S,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_companies_company_phase_category",
        table_name="portfolio_companies",
        schema=S,
    )
    op.drop_column("portfolio_companies", "company_phase_category", schema=S)
