"""Add org_chart_upload_batches and org_chart_upload_records tables.

Revision ID: 0035_org_chart_batch_upload
Revises: 0034_reconciliation_normalization
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0035_org_chart_batch_upload"
down_revision = "0034_reconciliation_normalization"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS "{S}".org_chart_upload_batches (
            id                    SERIAL PRIMARY KEY,
            review_cycle_id       VARCHAR(128) NOT NULL
                                  REFERENCES "{S}".review_cycles(id) ON DELETE RESTRICT,
            original_zip_filename VARCHAR(512) NOT NULL,
            status                VARCHAR(32)  NOT NULL DEFAULT 'uploaded',
            file_count            INTEGER      NOT NULL DEFAULT 0,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """))

    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocub_review_cycle '
        f'ON "{S}".org_chart_upload_batches (review_cycle_id)'
    ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocub_status '
        f'ON "{S}".org_chart_upload_batches (status)'
    ))

    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS "{S}".org_chart_upload_records (
            id                   SERIAL PRIMARY KEY,
            batch_id             INTEGER      NOT NULL
                                 REFERENCES "{S}".org_chart_upload_batches(id) ON DELETE CASCADE,
            review_cycle_id      VARCHAR(128) NOT NULL,
            file_id              INTEGER      REFERENCES "{S}".files(id) ON DELETE SET NULL,
            file_name            VARCHAR(512) NOT NULL,
            storage_uri          TEXT,
            company_id           VARCHAR(64),
            name                 VARCHAR(255),
            portfolio_company_id INTEGER      REFERENCES "{S}".portfolio_companies(id) ON DELETE SET NULL,
            extracted_org_chart  JSON,
            extraction_status    VARCHAR(32)  NOT NULL DEFAULT 'pending_mapping',
            error_message        TEXT,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """))

    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocur_batch '
        f'ON "{S}".org_chart_upload_records (batch_id)'
    ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocur_review_cycle '
        f'ON "{S}".org_chart_upload_records (review_cycle_id)'
    ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocur_portfolio_company '
        f'ON "{S}".org_chart_upload_records (portfolio_company_id)'
    ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_ocur_extraction_status '
        f'ON "{S}".org_chart_upload_records (extraction_status)'
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f'DROP TABLE IF EXISTS "{S}".org_chart_upload_records'))
    conn.execute(text(f'DROP TABLE IF EXISTS "{S}".org_chart_upload_batches'))
