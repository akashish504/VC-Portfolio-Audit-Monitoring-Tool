"""Add poc_email_ids and poc_cc_email_ids to portfolio_companies.

Revision ID: 0028_portfolio_company_poc_emails
Revises: 0027_export_jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0028_portfolio_company_poc_emails"
down_revision = "0027_export_jobs"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "portfolio_companies",
        sa.Column("poc_email_ids", ARRAY(sa.Text()), nullable=True),
        schema=S,
    )
    op.add_column(
        "portfolio_companies",
        sa.Column("poc_cc_email_ids", ARRAY(sa.Text()), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("portfolio_companies", "poc_cc_email_ids", schema=S)
    op.drop_column("portfolio_companies", "poc_email_ids", schema=S)
