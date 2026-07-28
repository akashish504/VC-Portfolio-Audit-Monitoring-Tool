"""Add export_jobs table for async XLSX report generation.

Revision ID: 0027_export_jobs
Revises: 0026_draft_email_affected_entity_ids
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0027_export_jobs"
down_revision = "0026_draft_email_affected_entity_ids"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS "{S}".export_jobs (
            id          VARCHAR(64)  PRIMARY KEY,
            job_type    VARCHAR(64)  NOT NULL,
            status      VARCHAR(32)  NOT NULL DEFAULT 'pending',
            review_cycle_id VARCHAR(128),
            filename    VARCHAR(512),
            storage_uri TEXT,
            error_message TEXT,
            meta        JSON         NOT NULL DEFAULT '{{}}',
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ
        )
    """))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS ix_export_jobs_status ON "{S}".export_jobs (status)'))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS ix_export_jobs_job_type ON "{S}".export_jobs (job_type)'))
    conn.execute(text(f'CREATE INDEX IF NOT EXISTS ix_export_jobs_review_cycle_id ON "{S}".export_jobs (review_cycle_id)'))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f'DROP TABLE IF EXISTS "{S}".export_jobs'))
