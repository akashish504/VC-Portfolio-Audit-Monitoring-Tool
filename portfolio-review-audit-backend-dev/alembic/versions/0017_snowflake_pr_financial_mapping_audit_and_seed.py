"""Add snowflake_pr_financial_mapping_view_audit and seed PR→Snowflake formulas config.

Revision ID: 0017_snowflake_pr_financial_mapping
Revises: 0016_pr_submission_data_raw
Create Date: 2026-05-23
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

# Inlined to avoid runtime dependency on the service module (constant was later removed).
SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY = "snowflake_pr_financial_metric_mapping_v1"
DEFAULT_SNOWFLAKE_PR_FINANCIAL_FORMULAS: dict = {
    "revenue": "audited_revenue",
    "ebitda": "audited_ebitda",
    "pbt": "audited_pbt",
    "pat": "pat_year",
    "cash": "cash_on_hand",
    "debt": "debt_total",
}

revision = "0017_snowflake_pr_financial_mapping"
down_revision = "0016_pr_submission_data_raw"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{APP_SCHEMA}".snowflake_pr_financial_mapping_view_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            action VARCHAR(255),
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_sf_pr_fin_mapping_audit_user_id '
        f'ON "{APP_SCHEMA}".snowflake_pr_financial_mapping_view_audit (user_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_sf_pr_fin_mapping_audit_action '
        f'ON "{APP_SCHEMA}".snowflake_pr_financial_mapping_view_audit (action)'
    )

    body = {"metrics": {k: {"formula": v} for k, v in DEFAULT_SNOWFLAKE_PR_FINANCIAL_FORMULAS.items()}}
    payload_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    description = (
        "Snowflake PR submission (pr_submission_data_raw) → FinancialDataSnowflake metric formulas"
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            f'INSERT INTO "{APP_SCHEMA}".config_table (key, value, description) '
            f"VALUES (:k, CAST(:payload AS json), :d) ON CONFLICT (key) DO NOTHING"
        ),
        {
            "k": SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY,
            "payload": payload_json,
            "d": description,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(f'DELETE FROM "{APP_SCHEMA}".config_table WHERE key = :k'),
        {"k": SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY},
    )
    op.execute(f'DROP TABLE IF EXISTS "{APP_SCHEMA}".snowflake_pr_financial_mapping_view_audit')
