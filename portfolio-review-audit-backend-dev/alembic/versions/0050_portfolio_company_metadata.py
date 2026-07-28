"""Create portfolio_company_metadata table.

This table was defined in the ORM (PortfolioCompanyMetadata) but was never
added to the reset migration or any subsequent migration, causing 500 errors
on /api/v1/master-scoping.

Revision ID: 0050_portfolio_company_metadata
Revises: 0049_review_stage_migration
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0050_portfolio_company_metadata"
down_revision = "0049_review_stage_migration"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f"""
        CREATE TABLE IF NOT EXISTS "{S}".portfolio_company_metadata (
            id SERIAL PRIMARY KEY,
            fund VARCHAR(100) NOT NULL,
            deal_id VARCHAR(100) NOT NULL,
            deal_name VARCHAR(255) NOT NULL,
            strategy VARCHAR(100) NOT NULL,
            il_main VARCHAR(100),
            sector_l1 VARCHAR(100),
            sector_l2 VARCHAR(100),
            geo_l1 VARCHAR(100),
            geo_l2 VARCHAR(100),
            cost NUMERIC(20, 2),
            distributed NUMERIC(20, 2),
            proceeds NUMERIC(20, 2),
            fmv NUMERIC(20, 2),
            ownership NUMERIC(10, 6),
            scoping_for_audit BOOLEAN DEFAULT false,
            reason_for_exclusion TEXT,
            category VARCHAR(100),
            unique_by_company_id NUMERIC(20, 2),
            unique_by_company_id_strategy NUMERIC(20, 2),
            consolidated_cost_and_fmv NUMERIC(20, 2),
            deal_level_stage_1 VARCHAR(100),
            deal_level_stage_2 VARCHAR(100),
            tentative_audit_completion_date VARCHAR(100),
            fy_end VARCHAR(100),
            auditor VARCHAR(255),
            category_of_auditor VARCHAR(100),
            py_audit_status VARCHAR(100),
            review_cycle_id VARCHAR(128),
            comments TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_pcm_fund_deal_strategy UNIQUE (fund, deal_id, strategy)
        )
    """))

    indexes = [
        f'CREATE INDEX IF NOT EXISTS ix_pcm_fund ON "{S}".portfolio_company_metadata (fund)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_deal_id ON "{S}".portfolio_company_metadata (deal_id)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_strategy ON "{S}".portfolio_company_metadata (strategy)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_sector_l1 ON "{S}".portfolio_company_metadata (sector_l1)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_geo_l1 ON "{S}".portfolio_company_metadata (geo_l1)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_category ON "{S}".portfolio_company_metadata (category)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_py_audit_status ON "{S}".portfolio_company_metadata (py_audit_status)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_review_cycle_id ON "{S}".portfolio_company_metadata (review_cycle_id)',
        f'CREATE INDEX IF NOT EXISTS ix_pcm_fund_deal_strategy ON "{S}".portfolio_company_metadata (fund, deal_id, strategy)',
    ]
    for idx_sql in indexes:
        op.execute(text(idx_sql))


def downgrade() -> None:
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".portfolio_company_metadata'))
