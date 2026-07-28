"""Add currency column to portfolio_companies.

Revision ID: 0029_portfolio_company_currency
Revises: 0028_portfolio_company_poc_emails
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_portfolio_company_currency"
down_revision = "0028_portfolio_company_poc_emails"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "portfolio_companies",
        sa.Column("currency", sa.String(8), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("portfolio_companies", "currency", schema=S)
