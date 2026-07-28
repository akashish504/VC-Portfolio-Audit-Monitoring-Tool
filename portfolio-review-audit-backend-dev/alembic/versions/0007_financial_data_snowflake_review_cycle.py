"""Align financial_data_snowflake with financial_data: review_cycle; drop period columns.

Revision ID: 0007_financial_data_snowflake_review_cycle
Revises: 0006_financial_data_review_cycle
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0007_financial_data_snowflake_review_cycle"
down_revision = "0006_financial_data_review_cycle"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data_snowflake
              ADD COLUMN IF NOT EXISTS review_cycle VARCHAR(128) NULL;
            """
        )
    )
    # Backfill from JSON payload where present (keeps synced rows usable after drop).
    op.execute(
        text(
            f"""
            UPDATE "{S}".financial_data_snowflake
            SET review_cycle = NULLIF(trim(COALESCE(payload, '{{}}'::json)::jsonb->>'review_cycle'), '')
            WHERE (review_cycle IS NULL OR trim(review_cycle) = '')
              AND COALESCE(payload, '{{}}'::json)::jsonb ? 'review_cycle';
            """
        )
    )
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_snowflake_period_start'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_snowflake_period_end'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data_snowflake
              DROP COLUMN IF EXISTS period_start,
              DROP COLUMN IF EXISTS period_end;
            """
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_company_review_cycle '
            f'ON "{S}".financial_data_snowflake (portfolio_company_id, entity_id, review_cycle)'
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_company_entity '
            f'ON "{S}".financial_data_snowflake (portfolio_company_id, entity_id)'
        )
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_snowflake_company_entity'))
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_financial_data_snowflake_company_review_cycle'))
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data_snowflake
              ADD COLUMN IF NOT EXISTS period_start TIMESTAMPTZ NULL,
              ADD COLUMN IF NOT EXISTS period_end TIMESTAMPTZ NULL;
            """
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_period_start '
            f'ON "{S}".financial_data_snowflake (period_start)'
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_period_end '
            f'ON "{S}".financial_data_snowflake (period_end)'
        )
    )
    op.execute(
        text(
            f"""
            ALTER TABLE "{S}".financial_data_snowflake
              DROP COLUMN IF EXISTS review_cycle;
            """
        )
    )
