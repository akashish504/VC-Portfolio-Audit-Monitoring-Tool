"""Add parameter_threshold_view_audit table.

Revision ID: 0014_parameter_threshold_view_audit
Revises: 0013_discrepancy_reviewer_remarks
Create Date: 2026-05-20
"""

from alembic import op

revision = "0014_parameter_threshold_view_audit"
down_revision = "0013_discrepancy_reviewer_remarks"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{APP_SCHEMA}".parameter_threshold_view_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            action VARCHAR(255),
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_parameter_threshold_view_audit_user_id '
        f'ON "{APP_SCHEMA}".parameter_threshold_view_audit (user_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_parameter_threshold_view_audit_action '
        f'ON "{APP_SCHEMA}".parameter_threshold_view_audit (action)'
    )


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{APP_SCHEMA}".parameter_threshold_view_audit')
