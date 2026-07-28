"""Seed the v2 Snowflake PR → FinancialDataSnowflake formula config.

Inserts ``snowflake_pr_financial_metric_mapping_v2`` into ``config_table`` with
sensible default formulas (P&L → annual yr_1/yr_2 columns; cash/debt → point-in-time
columns), only if the key does not already exist. The deterministic record/year
selection lives in code (``snowflake_pr_match``); this config only defines which
columns compose each metric per slot.

Revision ID: 0061_seed_snowflake_pr_v2_mapping
Revises: 0060_pcm_deal_id_for_analysis
Create Date: 2026-06-24
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

revision = "0061_seed_snowflake_pr_v2_mapping"
down_revision = "0060_pcm_deal_id_for_analysis"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"
V2_KEY = "snowflake_pr_financial_metric_mapping_v2"


def _pnl(revenue: str, ebitda: str, pbt: str, pat: str) -> dict:
    return {
        "revenue": {"formula": revenue},
        "ebitda": {"formula": ebitda},
        "pbt": {"formula": pbt},
        "pat": {"formula": pat},
    }


def _balance() -> dict:
    return {"cash": {"formula": "cash_on_hand"}, "debt": {"formula": "debt_total"}}


_DEFAULT_BODY = {
    "stage_groups": {
        "surge_seed": {
            "pnl": _pnl("revenue_yr_1", "ebitda_yr_1", "pbt_yr_1", "pat_yr_1"),
            "balance": _balance(),
        },
        "growth_venture": {
            "pnl_aligned": _pnl("revenue_yr_2", "ebitda_yr_2", "pbt_yr_2", "pat_yr_2"),
            "pnl_lagged": _pnl("revenue_yr_1", "ebitda_yr_1", "pbt_yr_1", "pat_yr_1"),
            "balance": _balance(),
        },
    }
}


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            INSERT INTO "{APP_SCHEMA}".config_table (key, value, description, created_at, updated_at)
            VALUES (:key, CAST(:value AS json), :description, now(), now())
            ON CONFLICT (key) DO NOTHING
            """
        ),
        {
            "key": V2_KEY,
            "value": json.dumps(_DEFAULT_BODY),
            "description": "Snowflake PR submission → FinancialDataSnowflake metric formulas (v2)",
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(f'DELETE FROM "{APP_SCHEMA}".config_table WHERE key = :key'),
        {"key": V2_KEY},
    )
