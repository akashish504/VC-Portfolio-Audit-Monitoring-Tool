"""Replace discrepancies.remarks with L1/L2 reviewer remarks and highlighted flag.

Revision ID: 0013_discrepancy_reviewer_remarks
Revises: 0012_files_pending_audit_log
"""

from __future__ import annotations

from alembic import op

revision = "0013_discrepancy_reviewer_remarks"
down_revision = "0012_files_pending_audit_log"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".discrepancies ADD COLUMN IF NOT EXISTS l1_reviewer_remarks TEXT NULL')
    op.execute(f'ALTER TABLE "{S}".discrepancies ADD COLUMN IF NOT EXISTS l2_reviewer_remarks TEXT NULL')
    op.execute(
        f'ALTER TABLE "{S}".discrepancies ADD COLUMN IF NOT EXISTS highlighted_to_investor BOOLEAN NOT NULL DEFAULT FALSE'
    )
    op.execute(
        f"""
        UPDATE "{S}".discrepancies
        SET l1_reviewer_remarks = remarks
        WHERE remarks IS NOT NULL AND btrim(remarks) <> ''
        """
    )
    op.execute(f'ALTER TABLE "{S}".discrepancies DROP COLUMN IF EXISTS remarks')


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".discrepancies ADD COLUMN IF NOT EXISTS remarks TEXT NULL')
    op.execute(
        f"""
        UPDATE "{S}".discrepancies
        SET remarks = l1_reviewer_remarks
        WHERE l1_reviewer_remarks IS NOT NULL AND btrim(l1_reviewer_remarks) <> ''
        """
    )
    op.execute(f'ALTER TABLE "{S}".discrepancies DROP COLUMN IF EXISTS l1_reviewer_remarks')
    op.execute(f'ALTER TABLE "{S}".discrepancies DROP COLUMN IF EXISTS l2_reviewer_remarks')
    op.execute(f'ALTER TABLE "{S}".discrepancies DROP COLUMN IF EXISTS highlighted_to_investor')
