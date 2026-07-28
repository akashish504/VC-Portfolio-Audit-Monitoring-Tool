"""Add reason_to_scope_out column to portfolio_companies.

Revision ID: 0038_portfolio_company_reason_to_scope_out
Revises: 0037_nullable_review_cycle_org_chart_batch
Create Date: 2026-06-02
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0038_portfolio_company_reason_to_scope_out"
down_revision = "0037_nullable_review_cycle_org_chart_batch"
branch_labels = None
depends_on = None

S = "portfolioauditreview"
T = "portfolio_companies"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        f'ALTER TABLE "{S}".{T} ADD COLUMN IF NOT EXISTS reason_to_scope_out TEXT'
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        f'ALTER TABLE "{S}".{T} DROP COLUMN IF EXISTS reason_to_scope_out'
    ))
