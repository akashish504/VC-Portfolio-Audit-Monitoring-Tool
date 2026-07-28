"""Add normalization / comparison columns to financial_metric_reconciliation.

Revision ID: 0034_reconciliation_normalization
Revises: 0033_export_currency_and_fx_cache
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0034_reconciliation_normalization"
down_revision = "0033_export_currency_and_fx_cache"
branch_labels = None
depends_on = None

S = "portfolioauditreview"
T = "financial_metric_reconciliation"


_COLUMNS: list[tuple[str, str]] = [
    ("extracted_value_normalized", "NUMERIC(20,4)"),
    ("snowflake_value_normalized", "NUMERIC(20,4)"),
    ("target_currency", "VARCHAR(8)"),
    ("extracted_source_currency", "VARCHAR(8)"),
    ("snowflake_source_currency", "VARCHAR(8)"),
    ("fx_date", "DATE"),
    ("extracted_fx_rate", "DOUBLE PRECISION"),
    ("snowflake_fx_rate", "DOUBLE PRECISION"),
    ("absolute_difference", "NUMERIC(20,4)"),
    ("percentage_difference", "DOUBLE PRECISION"),
    ("threshold_value", "DOUBLE PRECISION"),
    ("is_within_threshold", "BOOLEAN"),
    ("discrepancy_status", "VARCHAR(32)"),
    ("last_reconciled_at", "TIMESTAMPTZ"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, ddl in _COLUMNS:
        conn.execute(text(
            f'ALTER TABLE "{S}".{T} ADD COLUMN IF NOT EXISTS {name} {ddl}'
        ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_fmr_target_currency '
        f'ON "{S}".{T} (target_currency)'
    ))
    conn.execute(text(
        f'CREATE INDEX IF NOT EXISTS ix_fmr_discrepancy_status '
        f'ON "{S}".{T} (discrepancy_status)'
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_fmr_discrepancy_status'))
    conn.execute(text(f'DROP INDEX IF EXISTS "{S}".ix_fmr_target_currency'))
    for name, _ddl in reversed(_COLUMNS):
        conn.execute(text(
            f'ALTER TABLE "{S}".{T} DROP COLUMN IF EXISTS {name}'
        ))
