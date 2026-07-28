"""Make review_cycle_id nullable on org_chart_upload_batches and org_chart_upload_records.

Revision ID: 0037_nullable_review_cycle_org_chart_batch
Revises: 0036_org_chart_reconciliation
Create Date: 2026-06-01
"""
from __future__ import annotations

from alembic import op

revision = "0037_nullable_review_cycle_org_chart_batch"
down_revision = "0036_org_chart_reconciliation"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".org_chart_upload_batches ALTER COLUMN review_cycle_id DROP NOT NULL')
    op.execute(f'ALTER TABLE "{S}".org_chart_upload_records ALTER COLUMN review_cycle_id DROP NOT NULL')


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".org_chart_upload_batches ALTER COLUMN review_cycle_id SET NOT NULL')
    op.execute(f'ALTER TABLE "{S}".org_chart_upload_records ALTER COLUMN review_cycle_id SET NOT NULL')
