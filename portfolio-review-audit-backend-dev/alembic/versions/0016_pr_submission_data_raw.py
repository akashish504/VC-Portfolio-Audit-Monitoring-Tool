"""Create ``pr_submission_data_raw`` (PR submission staging) in portfolioauditreview.

Revision ID: 0016_pr_submission_data_raw
Revises: 0015_financial_extraction_mapping
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op

from src.db.models import PRSubmissionDataRaw

revision = "0016_pr_submission_data_raw"
down_revision = "0015_financial_extraction_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """DDL is generated from the ORM :class:`PRSubmissionDataRaw` table definition."""
    bind = op.get_bind()
    PRSubmissionDataRaw.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    PRSubmissionDataRaw.__table__.drop(bind, checkfirst=True)
