"""Narrow financial_data metrics to six canonical columns (revenue, ebitda, pbt, pat, cash, debt).

Revision ID: 0005_financial_data_six_metrics
Revises: 0004_temp_email_and_classifier_logs
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0005_financial_data_six_metrics"
down_revision = "0004_temp_email_and_classifier_logs"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))

    for table in ("financial_data", "financial_data_snowflake"):
        op.execute(
            text(
                f"""
                ALTER TABLE "{S}".{table}
                  DROP COLUMN IF EXISTS gross_margin,
                  DROP COLUMN IF EXISTS net_margin,
                  DROP COLUMN IF EXISTS operating_expenses,
                  DROP COLUMN IF EXISTS cash_burn,
                  DROP COLUMN IF EXISTS runway_months,
                  DROP COLUMN IF EXISTS arr,
                  DROP COLUMN IF EXISTS mrr,
                  DROP COLUMN IF EXISTS headcount;
                """
            )
        )
        op.execute(
            text(
                f"""
                ALTER TABLE "{S}".{table}
                  ADD COLUMN IF NOT EXISTS cash NUMERIC(20, 4),
                  ADD COLUMN IF NOT EXISTS debt NUMERIC(20, 4);
                """
            )
        )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))

    for table in ("financial_data", "financial_data_snowflake"):
        op.execute(
            text(
                f"""
                ALTER TABLE "{S}".{table}
                  DROP COLUMN IF EXISTS cash,
                  DROP COLUMN IF EXISTS debt;
                """
            )
        )
        # Restore prior optional metric columns (financial_data used Numeric for headcount;
        # snowflake used INTEGER — match 0001_reset schema.)
        if table == "financial_data":
            op.execute(
                text(
                    f"""
                    ALTER TABLE "{S}".financial_data
                      ADD COLUMN IF NOT EXISTS gross_margin NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS net_margin NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS operating_expenses NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS cash_burn NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS runway_months NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS arr NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS mrr NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS headcount NUMERIC(20, 4);
                    """
                )
            )
        else:
            op.execute(
                text(
                    f"""
                    ALTER TABLE "{S}".financial_data_snowflake
                      ADD COLUMN IF NOT EXISTS gross_margin NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS net_margin NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS operating_expenses NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS cash_burn NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS runway_months NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS arr NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS mrr NUMERIC(20, 4),
                      ADD COLUMN IF NOT EXISTS headcount INTEGER;
                    """
                )
            )
