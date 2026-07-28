"""Add FY end to entities and created_at to PR submission raw data.

Revision ID: 0039_entity_fy_end
Revises: 0038_portfolio_company_reason_to_scope_out
"""

from __future__ import annotations

from alembic import op

revision = "0039_entity_fy_end"
down_revision = "0038_portfolio_company_reason_to_scope_out"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE "{S}".entities '
        f'ADD COLUMN IF NOT EXISTS fy_end VARCHAR(16)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_entities_fy_end '
        f'ON "{S}".entities (fy_end)'
    )
    op.execute(
        f'ALTER TABLE "{S}".pr_submission_data_raw '
        f'ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ'
    )


def downgrade() -> None:
    op.execute(
        f'ALTER TABLE "{S}".pr_submission_data_raw '
        f'DROP COLUMN IF EXISTS created_at'
    )
    op.execute(f'DROP INDEX IF EXISTS "{S}".ix_entities_fy_end')
    op.execute(
        f'ALTER TABLE "{S}".entities '
        f'DROP COLUMN IF EXISTS fy_end'
    )
