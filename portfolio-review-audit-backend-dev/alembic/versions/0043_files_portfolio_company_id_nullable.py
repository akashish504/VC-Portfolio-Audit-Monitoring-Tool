"""Make files.portfolio_company_id nullable and fix FK action to SET NULL.

The baseline migration (0001) created files.portfolio_company_id as NOT NULL
with ON DELETE CASCADE, but the ORM model defines it as nullable (to support
org chart batch uploads where files are created before being mapped to a
company). This divergence only manifested in fresh environments — dev was
unaffected because the column had already been relaxed manually there.

Revision ID: 0043_files_portfolio_company_id_nullable
Revises: 0042_data_sync_config_weekly_default
Create Date: 2026-06-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0043_files_portfolio_company_id_nullable"
down_revision = "0042_data_sync_config_weekly_default"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    # Drop NOT NULL constraint
    conn.execute(text(
        f'ALTER TABLE "{S}".files ALTER COLUMN portfolio_company_id DROP NOT NULL'
    ))

    # Fix FK action from CASCADE to SET NULL to match the ORM model
    conn.execute(text(
        f'ALTER TABLE "{S}".files DROP CONSTRAINT IF EXISTS files_portfolio_company_id_fkey'
    ))
    conn.execute(text(
        f"""
        ALTER TABLE "{S}".files
          ADD CONSTRAINT files_portfolio_company_id_fkey
          FOREIGN KEY (portfolio_company_id)
          REFERENCES "{S}".portfolio_companies (id)
          ON DELETE SET NULL
        """
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Revert FK action back to CASCADE
    conn.execute(text(
        f'ALTER TABLE "{S}".files DROP CONSTRAINT IF EXISTS files_portfolio_company_id_fkey'
    ))
    conn.execute(text(
        f"""
        ALTER TABLE "{S}".files
          ADD CONSTRAINT files_portfolio_company_id_fkey
          FOREIGN KEY (portfolio_company_id)
          REFERENCES "{S}".portfolio_companies (id)
          ON DELETE CASCADE
        """
    ))

    # Restore NOT NULL (will fail if any NULL values exist — expected in new envs
    # after org chart uploads, so downgrade may need manual cleanup first)
    conn.execute(text(
        f'ALTER TABLE "{S}".files ALTER COLUMN portfolio_company_id SET NOT NULL'
    ))
