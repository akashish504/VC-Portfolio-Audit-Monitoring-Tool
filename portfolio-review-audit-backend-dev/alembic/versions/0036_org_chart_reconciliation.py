"""Add reconciliation columns to org_chart_upload_records.

Revision ID: 0036_org_chart_reconciliation
Revises: 0035_org_chart_batch_upload
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0036_org_chart_reconciliation"
down_revision = "0035_org_chart_batch_upload"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(f"""
        ALTER TABLE "{S}".org_chart_upload_records
        ADD COLUMN IF NOT EXISTS reconciliation_status  VARCHAR(32),
        ADD COLUMN IF NOT EXISTS applied_at             TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS applied_by             VARCHAR(255),
        ADD COLUMN IF NOT EXISTS reconciliation_payload JSON,
        ADD COLUMN IF NOT EXISTS applied_entity_ids     INTEGER[]
    """))

    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocur_reconciliation_status '
        f'ON "{S}".org_chart_upload_records (reconciliation_status)'
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        f'DROP INDEX IF EXISTS "{S}".ix_ocur_reconciliation_status'
    ))
    conn.execute(text(f"""
        ALTER TABLE "{S}".org_chart_upload_records
        DROP COLUMN IF EXISTS reconciliation_status,
        DROP COLUMN IF EXISTS applied_at,
        DROP COLUMN IF EXISTS applied_by,
        DROP COLUMN IF EXISTS reconciliation_payload,
        DROP COLUMN IF EXISTS applied_entity_ids
    """))
