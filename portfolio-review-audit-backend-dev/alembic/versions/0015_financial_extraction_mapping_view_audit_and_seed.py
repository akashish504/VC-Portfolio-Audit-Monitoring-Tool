"""Add financial_extraction_mapping_view_audit and seed metric mapping config.

Revision ID: 0015_financial_extraction_mapping
Revises: 0014_parameter_threshold_view_audit
Create Date: 2026-05-22
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from src.services.financial_metric_mapping import (
    DEFAULT_METRIC_MAPPING,
    FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
)

revision = "0015_financial_extraction_mapping"
down_revision = "0014_parameter_threshold_view_audit"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{APP_SCHEMA}".financial_extraction_mapping_view_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            action VARCHAR(255),
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_fin_ext_mapping_audit_user_id '
        f'ON "{APP_SCHEMA}".financial_extraction_mapping_view_audit (user_id)'
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_fin_ext_mapping_audit_action '
        f'ON "{APP_SCHEMA}".financial_extraction_mapping_view_audit (action)'
    )

    body = {"metrics": dict(DEFAULT_METRIC_MAPPING)}
    payload_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    description = (
        "Financial metric extraction mapping (FinancialData revenue/ebitda/pbt/pat/cash/debt)"
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            f'INSERT INTO "{APP_SCHEMA}".config_table (key, value, description) '
            f"VALUES (:k, CAST(:payload AS json), :d) ON CONFLICT (key) DO NOTHING"
        ),
        {
            "k": FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
            "payload": payload_json,
            "d": description,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(f'DELETE FROM "{APP_SCHEMA}".config_table WHERE key = :k'),
        {"k": FINANCIAL_METRIC_MAPPING_CONFIG_KEY},
    )
    op.execute(f'DROP TABLE IF EXISTS "{APP_SCHEMA}".financial_extraction_mapping_view_audit')
