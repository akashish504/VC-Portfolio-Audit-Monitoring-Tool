"""Add review_cycle_id to PCM unique constraint; split consolidated_cost_and_fmv.

Two changes bundled together:
1. The uniqueness key (fund, deal_id, strategy) now includes review_cycle_id so
   the same deal can exist across multiple review cycles without a constraint
   violation.
2. The single consolidated_cost_and_fmv column is replaced by two separate
   columns: consolidated_cost and consolidated_fmv.

Revision ID: 0052_pcm_unique_add_review_cycle_id
Revises: 0051_entity_status_state_management
Create Date: 2026-06-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0052_pcm_unique_add_review_cycle_id"
down_revision = "0051_entity_status_state_management"
branch_labels = None
depends_on = None

S = "portfolioauditreview"
TABLE = f'"{S}".portfolio_company_metadata'

OLD_UQ = "uq_pcm_fund_deal_strategy"
NEW_UQ = "uq_pcm_fund_deal_strategy_cycle"

OLD_IDX = "ix_pcm_fund_deal_strategy"
NEW_IDX = "ix_pcm_fund_deal_strategy_cycle"


def upgrade() -> None:
    # --- 1. Unique constraint / index update ---
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".{OLD_IDX}'))
    op.execute(text(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {OLD_UQ}'))

    op.execute(text(
        f'ALTER TABLE {TABLE} '
        f'ADD CONSTRAINT {NEW_UQ} UNIQUE (fund, deal_id, strategy, review_cycle_id)'
    ))
    op.execute(text(
        f'CREATE INDEX IF NOT EXISTS {NEW_IDX} ON {TABLE} (fund, deal_id, strategy, review_cycle_id)'
    ))

    # --- 2. Split consolidated_cost_and_fmv into two columns ---
    op.execute(text(
        f'ALTER TABLE {TABLE} '
        f'ADD COLUMN IF NOT EXISTS consolidated_cost NUMERIC(20, 2), '
        f'ADD COLUMN IF NOT EXISTS consolidated_fmv NUMERIC(20, 2)'
    ))
    op.execute(text(
        f'ALTER TABLE {TABLE} DROP COLUMN IF EXISTS consolidated_cost_and_fmv'
    ))


def downgrade() -> None:
    # --- 2. Restore consolidated_cost_and_fmv ---
    op.execute(text(
        f'ALTER TABLE {TABLE} '
        f'ADD COLUMN IF NOT EXISTS consolidated_cost_and_fmv NUMERIC(20, 2)'
    ))
    op.execute(text(
        f'ALTER TABLE {TABLE} '
        f'DROP COLUMN IF EXISTS consolidated_cost, '
        f'DROP COLUMN IF EXISTS consolidated_fmv'
    ))

    # --- 1. Revert unique constraint / index ---
    op.execute(text(f'DROP INDEX IF EXISTS "{S}".{NEW_IDX}'))
    op.execute(text(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {NEW_UQ}'))

    op.execute(text(
        f'ALTER TABLE {TABLE} '
        f'ADD CONSTRAINT {OLD_UQ} UNIQUE (fund, deal_id, strategy)'
    ))
    op.execute(text(
        f'CREATE INDEX IF NOT EXISTS {OLD_IDX} ON {TABLE} (fund, deal_id, strategy)'
    ))
