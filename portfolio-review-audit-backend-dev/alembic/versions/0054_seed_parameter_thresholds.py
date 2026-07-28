"""Seed parameter_thresholds and insert review_cycles CY26-FY27 through CY28-FY29.

Two changes:
1. Seed default rows into parameter_thresholds for the six financial metrics
   if they don't already exist.
2. Insert CY26-FY27, CY27-FY28, CY28-FY29 following the same pattern as the
   existing rows (string PKs, meta label), skipping any that already exist.

Revision ID: 0054_seed_parameter_thresholds
Revises: 0053_drop_email_history_legacy_fks
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0054_seed_parameter_thresholds"
down_revision = "0053_drop_email_history_legacy_fks"
branch_labels = None
depends_on = None

APP_SCHEMA = "portfolioauditreview"

_METRICS = ["revenue", "ebitda", "pbt", "pat", "cash", "debt"]

_DEFAULT_VALUE = '{"percent_threshold": 0.005, "absolute_threshold": 0}'

_NEW_CYCLES = [(26, 27), (27, 28), (28, 29)]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Seed parameter thresholds
    for metric in _METRICS:
        conn.execute(
            text(
                f"""
                INSERT INTO "{APP_SCHEMA}".parameter_thresholds (key, value, description)
                VALUES (:key, CAST(:value AS json), :description)
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {
                "key": metric,
                "value": _DEFAULT_VALUE,
                "description": f"Variance threshold for {metric.upper()}",
            },
        )

    # 2. Insert CY26-FY27, CY27-FY28, CY28-FY29 if not present
    for cy, fy in _NEW_CYCLES:
        cycle_id = f"CY{cy:02d}-FY{fy:02d}"
        display_name = f"CY {cy:02d} - FY {fy:02d}"
        conn.execute(
            text(
                f"""
                INSERT INTO "{APP_SCHEMA}".review_cycles
                    (id, name, status, starts_at, ends_at, meta, created_at, updated_at)
                VALUES
                    (:id, :name, 'active',
                     '20{cy:02d}-06-01 00:00:00+00'::timestamptz,
                     '20{fy:02d}-05-31 23:59:59+00'::timestamptz,
                     CAST(:meta AS json), now(), now())
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": cycle_id,
                "name": display_name,
                "meta": f'{{"label": "{display_name}"}}',
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for metric in _METRICS:
        conn.execute(
            text(
                f'DELETE FROM "{APP_SCHEMA}".parameter_thresholds WHERE key = :key'
            ),
            {"key": metric},
        )
    for cy, fy in _NEW_CYCLES:
        conn.execute(
            text(f'DELETE FROM "{APP_SCHEMA}".review_cycles WHERE id = :id'),
            {"id": f"CY{cy:02d}-FY{fy:02d}"},
        )
