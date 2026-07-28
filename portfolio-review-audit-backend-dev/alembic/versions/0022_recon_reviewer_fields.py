"""Replace l1/l2 reviewer remarks + highlighted_to_investor with company_response, reviewer_remarks, flagged.

Applies to both financial_metric_reconciliation and manual_reconciliation_queries.

Revision ID: 0022_recon_reviewer_fields
Revises: 0021_financial_metric_reconciliation
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0022_recon_reviewer_fields"
down_revision = "0021_financial_metric_reconciliation"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    for tbl in ("financial_metric_reconciliation", "manual_reconciliation_queries"):
        # Drop old columns
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS l1_reviewer_remarks'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS l2_reviewer_remarks'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS highlighted_to_investor'))

        # Add new columns
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS company_response TEXT'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS reviewer_remarks TEXT'))
        conn.execute(text(
            f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS flagged BOOLEAN NOT NULL DEFAULT FALSE'
        ))

        # Index on flagged
        idx_name = f"ix_{tbl}_flagged"
        conn.execute(text(
            f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{S}"."{tbl}" (flagged)'
        ))


def downgrade() -> None:
    conn = op.get_bind()

    for tbl in ("financial_metric_reconciliation", "manual_reconciliation_queries"):
        idx_name = f"ix_{tbl}_flagged"
        conn.execute(text(f'DROP INDEX IF EXISTS "{S}"."{idx_name}"'))

        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS flagged'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS reviewer_remarks'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" DROP COLUMN IF EXISTS company_response'))

        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS l1_reviewer_remarks TEXT'))
        conn.execute(text(f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS l2_reviewer_remarks TEXT'))
        conn.execute(text(
            f'ALTER TABLE "{S}"."{tbl}" ADD COLUMN IF NOT EXISTS highlighted_to_investor BOOLEAN NOT NULL DEFAULT FALSE'
        ))
