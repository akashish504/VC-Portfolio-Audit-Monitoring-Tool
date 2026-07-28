"""data_sync_process_batch — tracks async data sync runs (reference: portfolioreview.data_sync_process_batch).

Revision ID: 0003_data_sync_process_batch
Revises: 0002_portfolio_company_dashboard_fields
Create Date: 2026-04-28
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0003_data_sync_process_batch"
down_revision = "0002_portfolio_company_dashboard_fields"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    log = logging.getLogger("alembic")
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            CREATE TABLE "{S}".data_sync_process_batch (
                id VARCHAR(64) PRIMARY KEY,
                status VARCHAR(64) NOT NULL,
                time_taken DOUBLE PRECISION,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )
    log.info("[0003] Created %s.data_sync_process_batch", S)


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".data_sync_process_batch'))
