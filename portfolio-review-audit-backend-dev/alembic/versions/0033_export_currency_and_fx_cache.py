"""Add export_jobs.output_currency column and fx_rate_cache table.

Revision ID: 0033_export_currency_and_fx_cache
Revises: 0032_portfolio_company_phase_category
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0033_export_currency_and_fx_cache"
down_revision = "0032_portfolio_company_phase_category"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(f"""
        ALTER TABLE "{S}".export_jobs
        ADD COLUMN IF NOT EXISTS output_currency VARCHAR(8)
    """))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS "{S}".fx_rate_cache (
            id              SERIAL PRIMARY KEY,
            from_currency   VARCHAR(8)  NOT NULL,
            to_currency     VARCHAR(8)  NOT NULL,
            rate_date       DATE        NOT NULL,
            rate            DOUBLE PRECISION NOT NULL,
            fx_timestamp    VARCHAR(64),
            source          VARCHAR(32),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ
        )
    """))
    conn.execute(text(
        f'CREATE UNIQUE INDEX IF NOT EXISTS ix_fx_rate_cache_lookup '
        f'ON "{S}".fx_rate_cache (from_currency, to_currency, rate_date)'
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f'DROP TABLE IF EXISTS "{S}".fx_rate_cache'))
    conn.execute(text(f'ALTER TABLE "{S}".export_jobs DROP COLUMN IF EXISTS output_currency'))
