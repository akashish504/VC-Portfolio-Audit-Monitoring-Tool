"""Staging + classifier batch log tables (portfolio-review-app-api-develop parity).

Revision ID: 0004_temp_email_and_classifier_logs
Revises: 0003_data_sync_process_batch
Create Date: 2026-04-28
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0004_temp_email_and_classifier_logs"
down_revision = "0003_data_sync_process_batch"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    log = logging.getLogger("alembic")
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS "{S}".temp_email_history (
                id BIGSERIAL PRIMARY KEY,
                message_id TEXT NOT NULL,
                from_email VARCHAR(200),
                -- "to" is reserved in SQL; quoted identifier matches reference column name.
                "to" TEXT[],
                cc TEXT[],
                subject TEXT,
                body TEXT,
                sent_at TIMESTAMP WITHOUT TIME ZONE,
                email_type TEXT,
                attachments TEXT[],
                created_on TIMESTAMP WITHOUT TIME ZONE,
                updated_on TIMESTAMP WITHOUT TIME ZONE,
                status SMALLINT,
                body_html TEXT
            );
            """
        )
    )
    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS "{S}".email_classification_batch_log (
                id VARCHAR(64) PRIMARY KEY,
                batch_id VARCHAR(128),
                email_type VARCHAR(255),
                error_message TEXT,
                email_payload TEXT,
                status VARCHAR(64),
                company_name VARCHAR(512),
                company_id VARCHAR(255),
                email_message_id VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )
    op.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS ix_email_class_batch_log_batch_id ON "{S}".email_classification_batch_log (batch_id);'
        )
    )
    log.info("[0004] Created temp_email_history and email_classification_batch_log in %s", S)


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".email_classification_batch_log'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".temp_email_history'))
