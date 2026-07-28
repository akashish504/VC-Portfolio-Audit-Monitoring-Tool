"""Add investors ARRAY(Text) column to portfolio_companies.

Revision ID: 0031_portfolio_company_investors
Revises: 0030_efront_currency_ownership_company_raw_tables
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0031_portfolio_company_investors"
down_revision = "0030_efront_currency_ownership_company_raw_tables"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "portfolio_companies",
        sa.Column("investors", ARRAY(sa.Text()), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("portfolio_companies", "investors", schema=S)
