"""Widen email_history VARCHAR(255) columns to TEXT.

The table was created with VARCHAR(255) for sender, subject, thread_id,
company_id, company_pr_cycle_id, and reply_to. Real inbound emails from
portfolioreview.temp_email_history can have subjects and senders longer than
255 characters, causing StringDataRightTruncation on commit in the
classify_incoming_emails job.

Revision ID: 0044_email_history_widen_varchar_to_text
Revises: 0043_files_portfolio_company_id_nullable
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0044_email_history_widen_varchar_to_text"
down_revision = "0043_files_portfolio_company_id_nullable"
branch_labels = None
depends_on = None

S = "portfolioauditreview"

_COLUMNS = [
    "sender",
    "subject",
    "thread_id",
    "company_id",
    "company_pr_cycle_id",
    "reply_to",
]


def upgrade() -> None:
    conn = op.get_bind()
    for col in _COLUMNS:
        conn.execute(text(
            f'ALTER TABLE "{S}".email_history ALTER COLUMN {col} TYPE TEXT'
        ))


def downgrade() -> None:
    conn = op.get_bind()
    for col in _COLUMNS:
        conn.execute(text(
            f'ALTER TABLE "{S}".email_history ALTER COLUMN {col} TYPE VARCHAR(255)'
        ))
