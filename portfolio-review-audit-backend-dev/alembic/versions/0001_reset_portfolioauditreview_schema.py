"""Reset portfolioauditreview schema from scratch.

This is an intentional squash/reset migration.

- Assumes the schema has been emptied manually (may only contain `alembic_version`)
- Creates tables, constraints, and indexes with explicit PostgreSQL DDL (no ORM
  `create_all`) so the migration is reviewable and matches `src/db/models.py`
  for schema ``portfolioauditreview`` only.

Does **not** create ``audit.events`` or ``public.users`` (other schemas).

Revision ID: 0001_reset_portfolioauditreview_schema
Revises: None
Create Date: 2026-04-15
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0001_reset_portfolioauditreview_schema"
down_revision = None
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def _run(log: logging.Logger, stmt: str) -> None:
    """Execute one DDL statement; log truncated preview for operators."""
    preview = " ".join(stmt.split())[:200]
    log.debug("[0001_reset] executing: %s...", preview)
    op.execute(text(stmt))


def upgrade() -> None:
    log = logging.getLogger("alembic")

    op.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{S}"'))
    op.execute(text(f'SET search_path TO "{S}", public'))

    log.info("0001_reset: creating portfolioauditreview objects via explicit DDL")

    # -------------------------------------------------------------------------
    # 1) Tables (FK-safe order)
    # -------------------------------------------------------------------------

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".portfolio_companies (
            id SERIAL PRIMARY KEY,
            company_id VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            contact_name VARCHAR(255),
            contact_email_id VARCHAR(320),
            contact_contact_id VARCHAR(64),
            investment_stage VARCHAR(64),
            investment_type VARCHAR(64),
            in_review_status VARCHAR(64),
            review_cycle_id VARCHAR(128),
            review_stage VARCHAR(64),
            org_chart_file_id INTEGER,
            partner_email VARCHAR(320),
            prepcreator_audit_email VARCHAR(320),
            reviewer_audit_email VARCHAR(320),
            extra_data JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_portfolio_companies_company_id UNIQUE (company_id)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".entities (
            id SERIAL PRIMARY KEY,
            portfolio_company_id INTEGER NOT NULL
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            geolocation VARCHAR(128),
            review_cycle VARCHAR(64),
            status VARCHAR(64),
            parent_entity_id INTEGER
                REFERENCES "{S}".entities (id) ON DELETE SET NULL,
            region VARCHAR(64),
            is_parent BOOLEAN NOT NULL DEFAULT false,
            extra_data JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".financial_data (
            id SERIAL PRIMARY KEY,
            entity_id INTEGER
                REFERENCES "{S}".entities (id) ON DELETE SET NULL,
            portfolio_company_id INTEGER NOT NULL
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            period_start TIMESTAMPTZ,
            period_end TIMESTAMPTZ,
            frequency VARCHAR(32),
            currency VARCHAR(8),
            revenue NUMERIC(20, 4),
            gross_margin NUMERIC(20, 4),
            pbt NUMERIC(20, 4),
            ebitda NUMERIC(20, 4),
            pat NUMERIC(20, 4),
            net_margin NUMERIC(20, 4),
            operating_expenses NUMERIC(20, 4),
            cash_burn NUMERIC(20, 4),
            runway_months NUMERIC(20, 4),
            arr NUMERIC(20, 4),
            mrr NUMERIC(20, 4),
            headcount NUMERIC(20, 4),
            extra_data JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".files (
            id SERIAL PRIMARY KEY,
            portfolio_company_id INTEGER NOT NULL
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            entity_id INTEGER
                REFERENCES "{S}".entities (id) ON DELETE SET NULL,
            filename VARCHAR(512) NOT NULL,
            content_type VARCHAR(128),
            storage_uri TEXT,
            status VARCHAR(64),
            tags JSON NOT NULL DEFAULT '[]'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".file_ocr_metadata (
            id SERIAL PRIMARY KEY,
            file_id INTEGER NOT NULL
                REFERENCES "{S}".files (id) ON DELETE CASCADE,
            ocr_text TEXT,
            ocr_json JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_file_ocr_metadata_file_id UNIQUE (file_id)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".discrepancies (
            id SERIAL PRIMARY KEY,
            portfolio_company_id INTEGER NOT NULL
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            type VARCHAR(64),
            category VARCHAR(16) NOT NULL,
            entity_id INTEGER,
            discrepency_text TEXT NOT NULL,
            status VARCHAR(64),
            enable BOOLEAN NOT NULL DEFAULT true,
            remarks TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".email_templates (
            id VARCHAR(255) PRIMARY KEY,
            template_name VARCHAR(255),
            body TEXT,
            subject VARCHAR(255),
            version_id VARCHAR(255),
            version_name VARCHAR(255),
            is_active BOOLEAN,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".draft_email (
            id VARCHAR(255) PRIMARY KEY,
            portfolio_company_id INTEGER
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            subject VARCHAR(255),
            to_add VARCHAR(255)[],
            cc VARCHAR(255)[],
            email_body TEXT,
            template_id VARCHAR(255)
                REFERENCES "{S}".email_templates (id) ON DELETE SET NULL,
            attachments VARCHAR(255)[],
            attachments_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".email_history (
            id VARCHAR(255) PRIMARY KEY,
            thread_id VARCHAR(255),
            portfolio_company_id INTEGER
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            company_id VARCHAR(255),
            company_pr_cycle_id VARCHAR(255),
            sender VARCHAR(255),
            recipients TEXT[],
            cc TEXT[],
            subject VARCHAR(255),
            body TEXT,
            email_type TEXT,
            attachments TEXT[],
            sent_at TIMESTAMPTZ,
            is_inbound BOOLEAN,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_on TIMESTAMPTZ,
            status INTEGER DEFAULT 0,
            associated_query_ids VARCHAR(255)[],
            bcc TEXT[],
            reply_to VARCHAR(255),
            body_html TEXT,
            template_id VARCHAR(255)
                REFERENCES "{S}".email_templates (id) ON DELETE SET NULL
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".company_view_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            company_id VARCHAR(255),
            action VARCHAR(255),
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".review_cycle_view_audit (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            review_cycle_id VARCHAR(255),
            action VARCHAR(255),
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".review_cycles (
            id VARCHAR(128) PRIMARY KEY,
            name VARCHAR(128),
            status VARCHAR(64),
            starts_at TIMESTAMPTZ,
            ends_at TIMESTAMPTZ,
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".parameter_thresholds (
            id SERIAL PRIMARY KEY,
            key VARCHAR(128) NOT NULL,
            value JSON NOT NULL DEFAULT '{{}}'::json,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_parameter_thresholds_key UNIQUE (key)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".config_table (
            id SERIAL PRIMARY KEY,
            key VARCHAR(128) NOT NULL,
            value JSON NOT NULL DEFAULT '{{}}'::json,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_config_table_key UNIQUE (key)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".financial_data_snowflake (
            id SERIAL PRIMARY KEY,
            source_ref VARCHAR(255),
            payload JSON NOT NULL DEFAULT '{{}}'::json,
            portfolio_company_id INTEGER
                REFERENCES "{S}".portfolio_companies (id) ON DELETE CASCADE,
            entity_id INTEGER
                REFERENCES "{S}".entities (id) ON DELETE SET NULL,
            period_start TIMESTAMPTZ,
            period_end TIMESTAMPTZ,
            frequency VARCHAR(32),
            currency VARCHAR(8),
            revenue NUMERIC(20, 4),
            ebitda NUMERIC(20, 4),
            pbt NUMERIC(20, 4),
            pat NUMERIC(20, 4),
            gross_margin NUMERIC(20, 4),
            net_margin NUMERIC(20, 4),
            operating_expenses NUMERIC(20, 4),
            cash_burn NUMERIC(20, 4),
            runway_months NUMERIC(20, 4),
            arr NUMERIC(20, 4),
            mrr NUMERIC(20, 4),
            headcount INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".daily_sync_process_batch (
            id SERIAL PRIMARY KEY,
            run_date VARCHAR(32),
            status VARCHAR(64),
            stats JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".apscheduler_jobs (
            id SERIAL PRIMARY KEY,
            job_name VARCHAR(255) NOT NULL,
            schedule VARCHAR(128),
            is_enabled BOOLEAN NOT NULL DEFAULT true,
            last_run_at TIMESTAMPTZ,
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_apscheduler_jobs_job_name UNIQUE (job_name)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".template_email_history (
            id SERIAL PRIMARY KEY,
            template_id VARCHAR(255)
                REFERENCES "{S}".email_templates (id) ON DELETE SET NULL,
            event VARCHAR(64) NOT NULL,
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".email_process_status (
            id SERIAL PRIMARY KEY,
            process_name VARCHAR(255) NOT NULL,
            status VARCHAR(64),
            message TEXT,
            meta JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_email_process_status_process_name UNIQUE (process_name)
        )
        """,
    )

    _run(
        log,
        f"""
        CREATE TABLE IF NOT EXISTS "{S}".email_processing_checkpoint (
            id SERIAL PRIMARY KEY,
            checkpoint_key VARCHAR(255) NOT NULL,
            checkpoint_value JSON NOT NULL DEFAULT '{{}}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_email_processing_checkpoint_key UNIQUE (checkpoint_key)
        )
        """,
    )

    # -------------------------------------------------------------------------
    # 2) Indexes (match ORM index=True + explicit Index(...) names)
    # -------------------------------------------------------------------------

    indexes: list[str] = [
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_name ON "{S}".portfolio_companies (name)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_contact_email_id ON "{S}".portfolio_companies (contact_email_id)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_contact_contact_id ON "{S}".portfolio_companies (contact_contact_id)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_investment_stage ON "{S}".portfolio_companies (investment_stage)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_investment_type ON "{S}".portfolio_companies (investment_type)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_in_review_status ON "{S}".portfolio_companies (in_review_status)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_review_cycle_id ON "{S}".portfolio_companies (review_cycle_id)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_review_stage ON "{S}".portfolio_companies (review_stage)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_org_chart_file_id ON "{S}".portfolio_companies (org_chart_file_id)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_partner_email ON "{S}".portfolio_companies (partner_email)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_prepcreator_audit_email ON "{S}".portfolio_companies (prepcreator_audit_email)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_reviewer_audit_email ON "{S}".portfolio_companies (reviewer_audit_email)',
        f'CREATE INDEX IF NOT EXISTS ix_portfolio_companies_name_company_id ON "{S}".portfolio_companies (name, company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_portfolio_company_id ON "{S}".entities (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_name ON "{S}".entities (name)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_geolocation ON "{S}".entities (geolocation)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_review_cycle ON "{S}".entities (review_cycle)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_status ON "{S}".entities (status)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_parent_entity_id ON "{S}".entities (parent_entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_region ON "{S}".entities (region)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_company_name ON "{S}".entities (portfolio_company_id, name)',
        f'CREATE INDEX IF NOT EXISTS ix_entities_company_parent ON "{S}".entities (portfolio_company_id, parent_entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_entity_id ON "{S}".financial_data (entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_portfolio_company_id ON "{S}".financial_data (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_period_start ON "{S}".financial_data (period_start)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_period_end ON "{S}".financial_data (period_end)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_frequency ON "{S}".financial_data (frequency)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_currency ON "{S}".financial_data (currency)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_company_entity ON "{S}".financial_data (portfolio_company_id, entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_company_period ON "{S}".financial_data (portfolio_company_id, entity_id, period_end)',
        f'CREATE INDEX IF NOT EXISTS ix_files_portfolio_company_id ON "{S}".files (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_files_entity_id ON "{S}".files (entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_files_status ON "{S}".files (status)',
        f'CREATE INDEX IF NOT EXISTS ix_files_company_filename ON "{S}".files (portfolio_company_id, filename)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_portfolio_company_id ON "{S}".discrepancies (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_type ON "{S}".discrepancies (type)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_category ON "{S}".discrepancies (category)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_entity_id ON "{S}".discrepancies (entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_status ON "{S}".discrepancies (status)',
        f'CREATE INDEX IF NOT EXISTS ix_discrepancies_enable ON "{S}".discrepancies (enable)',
        f'CREATE INDEX IF NOT EXISTS ix_draft_email_portfolio_company_id ON "{S}".draft_email (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_draft_email_template_id ON "{S}".draft_email (template_id)',
        f'CREATE INDEX IF NOT EXISTS ix_email_history_thread_id ON "{S}".email_history (thread_id)',
        f'CREATE INDEX IF NOT EXISTS ix_email_history_portfolio_company_id ON "{S}".email_history (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_email_history_template_id ON "{S}".email_history (template_id)',
        f'CREATE INDEX IF NOT EXISTS ix_company_view_audit_user_id ON "{S}".company_view_audit (user_id)',
        f'CREATE INDEX IF NOT EXISTS ix_company_view_audit_company_id ON "{S}".company_view_audit (company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_company_view_audit_action ON "{S}".company_view_audit (action)',
        f'CREATE INDEX IF NOT EXISTS ix_review_cycle_view_audit_user_id ON "{S}".review_cycle_view_audit (user_id)',
        f'CREATE INDEX IF NOT EXISTS ix_review_cycle_view_audit_review_cycle_id ON "{S}".review_cycle_view_audit (review_cycle_id)',
        f'CREATE INDEX IF NOT EXISTS ix_review_cycle_view_audit_action ON "{S}".review_cycle_view_audit (action)',
        f'CREATE INDEX IF NOT EXISTS ix_review_cycles_name ON "{S}".review_cycles (name)',
        f'CREATE INDEX IF NOT EXISTS ix_review_cycles_status ON "{S}".review_cycles (status)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_source_ref ON "{S}".financial_data_snowflake (source_ref)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_portfolio_company_id ON "{S}".financial_data_snowflake (portfolio_company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_entity_id ON "{S}".financial_data_snowflake (entity_id)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_period_start ON "{S}".financial_data_snowflake (period_start)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_period_end ON "{S}".financial_data_snowflake (period_end)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_frequency ON "{S}".financial_data_snowflake (frequency)',
        f'CREATE INDEX IF NOT EXISTS ix_financial_data_snowflake_currency ON "{S}".financial_data_snowflake (currency)',
        f'CREATE INDEX IF NOT EXISTS ix_daily_sync_process_batch_run_date ON "{S}".daily_sync_process_batch (run_date)',
        f'CREATE INDEX IF NOT EXISTS ix_daily_sync_process_batch_status ON "{S}".daily_sync_process_batch (status)',
        f'CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_schedule ON "{S}".apscheduler_jobs (schedule)',
        f'CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_is_enabled ON "{S}".apscheduler_jobs (is_enabled)',
        f'CREATE INDEX IF NOT EXISTS ix_template_email_history_template_id ON "{S}".template_email_history (template_id)',
        f'CREATE INDEX IF NOT EXISTS ix_template_email_history_event ON "{S}".template_email_history (event)',
        f'CREATE INDEX IF NOT EXISTS ix_email_process_status_status ON "{S}".email_process_status (status)',
    ]

    for idx_sql in indexes:
        _run(log, idx_sql)

    log.info("0001_reset: created %d index definitions", len(indexes))

    # -------------------------------------------------------------------------
    # 3) Sanity check
    # -------------------------------------------------------------------------
    bind = op.get_bind()
    reg = bind.execute(
        text("SELECT to_regclass(:q)"),
        {"q": f"{S}.portfolio_companies"},
    ).scalar()
    if reg is None:
        raise RuntimeError(
            "0001_reset: DDL finished but portfolioauditreview.portfolio_companies "
            "is missing — check DB permissions and search_path."
        )
    log.info("0001_reset: verified table %s.portfolio_companies exists", S)


def downgrade() -> None:
    # Non-reversible (reset migration). Keep downgrade as a no-op.
    pass
