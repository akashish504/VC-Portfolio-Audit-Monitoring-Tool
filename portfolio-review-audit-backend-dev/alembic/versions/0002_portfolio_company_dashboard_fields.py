"""Add portfolio company dashboard / fund economics columns (all string storage).

Revision ID: 0002_portfolio_company_dashboard_fields
Revises: 0001_reset_portfolioauditreview_schema
Create Date: 2026-04-15
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0002_portfolio_company_dashboard_fields"
down_revision = "0001_reset_portfolioauditreview_schema"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def _run(log: logging.Logger, stmt: str) -> None:
    preview = " ".join(stmt.split())[:200]
    log.debug("[0002] executing: %s...", preview)
    op.execute(text(stmt))


def upgrade() -> None:
    log = logging.getLogger("alembic")
    op.execute(text(f'SET search_path TO "{S}", public'))

    cols = [
        ("fund", "VARCHAR(255)"),
        ("investment_lead", "VARCHAR(255)"),
        ("company_stage", "VARCHAR(64)"),
        ("geography", "VARCHAR(64)"),
        ("ownership_pct", "VARCHAR(128)"),
        ("cost", "VARCHAR(128)"),
        ("fmv", "VARCHAR(128)"),
        ("position_is_unique", "VARCHAR(32)"),
        ("consolidated_ownership_pct", "VARCHAR(128)"),
        ("consolidated_cost", "VARCHAR(128)"),
        ("consolidated_fmv", "VARCHAR(128)"),
        ("company_category_1", "VARCHAR(128)"),
        ("company_category_2", "VARCHAR(128)"),
        ("scoped_in_for_audit", "VARCHAR(32)"),
        ("exclusion_reason", "TEXT"),
        ("fy_end_date", "VARCHAR(32)"),
        ("due_date", "VARCHAR(32)"),
        ("audit_status", "VARCHAR(128)"),
        ("auditor", "VARCHAR(255)"),
        ("tentative_completion_date", "VARCHAR(32)"),
        ("company_response", "TEXT"),
        ("peak_xv_actionable", "TEXT"),
    ]
    for name, typ in cols:
        _run(
            log,
            f'ALTER TABLE "{S}".portfolio_companies ADD COLUMN IF NOT EXISTS {name} {typ}',
        )

    _run(
        log,
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_fund ON "{S}".portfolio_companies (fund)',
    )
    _run(
        log,
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_company_stage ON "{S}".portfolio_companies (company_stage)',
    )
    _run(
        log,
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_geography ON "{S}".portfolio_companies (geography)',
    )
    _run(
        log,
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_scoped_in ON "{S}".portfolio_companies (scoped_in_for_audit)',
    )
    _run(
        log,
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_audit_status ON "{S}".portfolio_companies (audit_status)',
    )


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    for ix in (
        "ix_portfolio_companies_audit_status",
        "ix_portfolio_companies_scoped_in",
        "ix_portfolio_companies_geography",
        "ix_portfolio_companies_company_stage",
        "ix_portfolio_companies_fund",
    ):
        op.execute(text(f'DROP INDEX IF EXISTS "{S}".{ix}'))
    names = (
        "peak_xv_actionable",
        "company_response",
        "tentative_completion_date",
        "auditor",
        "audit_status",
        "due_date",
        "fy_end_date",
        "exclusion_reason",
        "scoped_in_for_audit",
        "company_category_2",
        "company_category_1",
        "consolidated_fmv",
        "consolidated_cost",
        "consolidated_ownership_pct",
        "position_is_unique",
        "fmv",
        "cost",
        "ownership_pct",
        "geography",
        "company_stage",
        "investment_lead",
        "fund",
    )
    for name in names:
        op.execute(text(f'ALTER TABLE "{S}".portfolio_companies DROP COLUMN IF EXISTS {name}'))
