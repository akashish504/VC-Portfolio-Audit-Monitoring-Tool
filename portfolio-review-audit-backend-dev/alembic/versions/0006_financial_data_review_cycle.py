"""Add review_cycle to financial_data; drop period_start/period_end; partial unique index.

Revision ID: 0006_financial_data_review_cycle
Revises: 0005_financial_data_six_metrics
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0006_financial_data_review_cycle"
down_revision = "0005_financial_data_six_metrics"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data
              ADD COLUMN IF NOT EXISTS review_cycle VARCHAR(128) NULL;
            """
        )
    )
    # Indexes on period_* must be dropped before columns.
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_company_period'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_period_start'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_period_end'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data
              DROP COLUMN IF EXISTS period_start,
              DROP COLUMN IF EXISTS period_end;
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_data_company_entity_review_cycle
            ON "{S}".financial_data (portfolio_company_id, entity_id, review_cycle)
            WHERE entity_id IS NOT NULL AND review_cycle IS NOT NULL;
            """
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_company_review_cycle '
            f'ON "{S}".financial_data (portfolio_company_id, entity_id, review_cycle)'
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_company_review_cycle'))
    op.execute(
        text(f'DROP INDEX IF EXISTS "{S}".uq_financial_data_company_entity_review_cycle')
    )
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data
              ADD COLUMN IF NOT EXISTS period_start TIMESTAMPTZ NULL,
              ADD COLUMN IF NOT EXISTS period_end TIMESTAMPTZ NULL;
            """
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_period_start ON "{S}".financial_data (period_start)'
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_period_end ON "{S}".financial_data (period_end)'
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_company_period '
            f'ON "{S}".financial_data (portfolio_company_id, entity_id, period_end)'
        )
    )
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data
              DROP COLUMN IF EXISTS review_cycle;
            """
        )
    )
