"""Add original_currency to files table.

Stores the first currency code detected from the uploaded audit document
(either LLM-detected on extraction or user-set via the UI). Never overwritten
by subsequent FX conversion or re-extraction passes — read by report generation.

Revision ID: 0045_files_original_currency
Revises: 0044_email_history_widen_varchar_to_text
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_files_original_currency"
down_revision = "0044_email_history_widen_varchar_to_text"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("original_currency", sa.String(8), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("files", "original_currency", schema=S)
