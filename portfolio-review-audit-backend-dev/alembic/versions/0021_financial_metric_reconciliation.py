"""Tall ``financial_metric_reconciliation`` table; manual queries; migrate from wide financial_data + discrepancies.

Revision ID: 0021_financial_metric_reconciliation
Revises: 0020_fy_end
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0021_financial_metric_reconciliation"
down_revision = "0020_fy_end"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))

    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS "{S}".financial_metric_reconciliation (
                id SERIAL PRIMARY KEY,
                portfolio_company_id INTEGER NOT NULL
                    REFERENCES "{S}".portfolio_companies(id) ON DELETE CASCADE,
                entity_id INTEGER NOT NULL
                    REFERENCES "{S}".entities(id) ON DELETE CASCADE,
                review_cycle VARCHAR(128) NOT NULL,
                metric_key VARCHAR(32) NOT NULL,
                mis_amount NUMERIC(20,4),
                afs_amount NUMERIC(20,4),
                mis_currency VARCHAR(8),
                afs_currency VARCHAR(8),
                frequency VARCHAR(32),
                category VARCHAR(16) NOT NULL DEFAULT 'financial',
                type VARCHAR(64),
                discrepency_text TEXT NOT NULL DEFAULT '',
                status VARCHAR(64),
                enable BOOLEAN NOT NULL DEFAULT false,
                l1_reviewer_remarks TEXT,
                l2_reviewer_remarks TEXT,
                variance_category TEXT,
                highlighted_to_investor BOOLEAN NOT NULL DEFAULT false,
                extra_data JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_fmr_company_entity_cycle_metric UNIQUE (
                    portfolio_company_id,
                    entity_id,
                    review_cycle,
                    metric_key
                ),
                CONSTRAINT ck_fmr_metric_key CHECK (
                    metric_key IN ('revenue', 'ebitda', 'pbt', 'pat', 'cash', 'debt')
                )
            );
            CREATE INDEX IF NOT EXISTS ix_fmr_company_entity_cycle
              ON "{S}".financial_metric_reconciliation (portfolio_company_id, entity_id, review_cycle);
            CREATE INDEX IF NOT EXISTS ix_fmr_company
              ON "{S}".financial_metric_reconciliation (portfolio_company_id);
            CREATE INDEX IF NOT EXISTS ix_fmr_metric_key ON "{S}".financial_metric_reconciliation (metric_key);
            CREATE INDEX IF NOT EXISTS ix_fmr_enable ON "{S}".financial_metric_reconciliation ("enable");

            CREATE TABLE IF NOT EXISTS "{S}".manual_reconciliation_queries (
                id SERIAL PRIMARY KEY,
                portfolio_company_id INTEGER NOT NULL
                    REFERENCES "{S}".portfolio_companies(id) ON DELETE CASCADE,
                entity_id INTEGER NOT NULL
                    REFERENCES "{S}".entities(id) ON DELETE CASCADE,
                discrepency_text TEXT NOT NULL,
                category VARCHAR(16) NOT NULL DEFAULT 'manual',
                type VARCHAR(64),
                status VARCHAR(64),
                enable BOOLEAN NOT NULL DEFAULT true,
                l1_reviewer_remarks TEXT,
                l2_reviewer_remarks TEXT,
                highlighted_to_investor BOOLEAN NOT NULL DEFAULT false,
                variance_category TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_manual_queries_company_entity
              ON "{S}".manual_reconciliation_queries (portfolio_company_id, entity_id);
            """
        )
    )

    conn = op.get_bind()

    conn.execute(
        text(
            f"""
            INSERT INTO "{S}".financial_metric_reconciliation (
                portfolio_company_id, entity_id, review_cycle, metric_key,
                mis_amount, afs_amount, mis_currency, afs_currency, frequency,
                extra_data,
                category, type, discrepency_text, status, enable,
                l1_reviewer_remarks, l2_reviewer_remarks,
                variance_category, highlighted_to_investor
            )
            SELECT
                fd.portfolio_company_id,
                fd.entity_id,
                trim(fd.review_cycle),
                kv.mk,
                NULL,
                kv.val,
                NULL,
                CASE
                    WHEN fd.currency IS NOT NULL AND trim(fd.currency::text) <> ''
                    THEN upper(substr(trim(fd.currency::text), 1, 8))
                END,
                fd.frequency,
                COALESCE(fd.extra_data::jsonb, '{{}}'::jsonb),
                'financial',
                kv.mk,
                '',
                'Open',
                false,
                NULL, NULL,
                NULL, false
            FROM "{S}".financial_data fd
            CROSS JOIN LATERAL (
                SELECT v.mk, v.val
                FROM (VALUES
                    ('revenue', fd.revenue),
                    ('ebitda', fd.ebitda),
                    ('pbt', fd.pbt),
                    ('pat', fd.pat),
                    ('cash', fd.cash),
                    ('debt', fd.debt)
                ) AS v(mk, val)
            ) AS kv(mk, val)
            WHERE fd.entity_id IS NOT NULL
              AND fd.review_cycle IS NOT NULL
              AND trim(fd.review_cycle) <> '';
            """
        )
    )

    conn.execute(
        text(
            f"""
            INSERT INTO "{S}".manual_reconciliation_queries (
                portfolio_company_id, entity_id, discrepency_text,
                category, type, status, enable,
                l1_reviewer_remarks, l2_reviewer_remarks,
                highlighted_to_investor, variance_category
            )
            SELECT
                portfolio_company_id,
                entity_id,
                discrepency_text,
                category,
                type,
                status,
                enable,
                l1_reviewer_remarks,
                l2_reviewer_remarks,
                highlighted_to_investor,
                variance_category
            FROM "{S}".discrepancies d
            WHERE lower(trim(category)) = 'manual'
              AND entity_id IS NOT NULL;
            """
        )
    )

    disc_rows = conn.execute(
        text(
            f"""
            SELECT
                portfolio_company_id, entity_id, type, discrepency_text, status, enable,
                l1_reviewer_remarks, l2_reviewer_remarks, highlighted_to_investor, variance_category
            FROM "{S}".discrepancies
            WHERE lower(trim(category)) = 'financial'
              AND entity_id IS NOT NULL
              AND trim(COALESCE(type, '')) <> '';
            """
        )
    ).mappings().all()

    for row in disc_rows:
        mk = str(row["type"]).strip().lower()
        if mk not in ("revenue", "ebitda", "pbt", "pat", "cash", "debt"):
            continue
        pid = row["portfolio_company_id"]
        eid = row["entity_id"]
        conn.execute(
            text(
                f"""
                UPDATE "{S}".financial_metric_reconciliation AS fm
                SET
                    discrepency_text = CAST(:dq AS TEXT),
                    status = COALESCE(CAST(:st AS VARCHAR), fm.status),
                    enable = CAST(:en AS BOOLEAN),
                    l1_reviewer_remarks = CAST(:l1 AS TEXT),
                    l2_reviewer_remarks = CAST(:l2 AS TEXT),
                    highlighted_to_investor = CAST(:hi AS BOOLEAN),
                    variance_category = CAST(:vc AS TEXT)
                FROM (
                    SELECT fm2.id,
                        ROW_NUMBER() OVER (
                            ORDER BY
                                CASE
                                    WHEN trim(COALESCE(fm2.review_cycle, '')) = trim(COALESCE(pc.review_cycle_id, ''))
                                    THEN 0 ELSE 1
                                END,
                                fm2.review_cycle DESC NULLS LAST,
                                fm2.id ASC
                        ) AS rn
                    FROM "{S}".financial_metric_reconciliation fm2
                    INNER JOIN "{S}".portfolio_companies pc ON pc.id = fm2.portfolio_company_id
                    WHERE fm2.portfolio_company_id = :pid
                      AND fm2.entity_id = :eid
                      AND lower(trim(fm2.metric_key)) = :mk
                ) picked
                WHERE fm.id = picked.id AND picked.rn = 1;
                """
            ),
            {
                "dq": row["discrepency_text"] or "",
                "st": row["status"],
                "en": bool(row["enable"]),
                "l1": row["l1_reviewer_remarks"],
                "l2": row["l2_reviewer_remarks"],
                "hi": bool(row["highlighted_to_investor"]),
                "vc": row["variance_category"],
                "pid": pid,
                "eid": eid,
                "mk": mk,
            },
        )

    op.execute(text(f'DROP TABLE IF EXISTS "{S}".discrepancies CASCADE'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".financial_data CASCADE'))


def downgrade() -> None:
    op.execute(text(f'SET search_path TO "{S}", public'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".manual_reconciliation_queries CASCADE'))
    op.execute(text(f'DROP TABLE IF EXISTS "{S}".financial_metric_reconciliation CASCADE'))

    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS "{S}".financial_data (
                id SERIAL PRIMARY KEY,
                entity_id INTEGER REFERENCES "{S}".entities(id) ON DELETE SET NULL,
                portfolio_company_id INTEGER NOT NULL
                    REFERENCES "{S}".portfolio_companies(id) ON DELETE CASCADE,
                review_cycle VARCHAR(128),
                frequency VARCHAR(32),
                currency VARCHAR(8),
                revenue NUMERIC(20,4),
                ebitda NUMERIC(20,4),
                pbt NUMERIC(20,4),
                pat NUMERIC(20,4),
                cash NUMERIC(20,4),
                debt NUMERIC(20,4),
                extra_data JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_financial_data_company_entity ON "{S}".financial_data (portfolio_company_id, entity_id);

            CREATE TABLE IF NOT EXISTS "{S}".discrepancies (
                id SERIAL PRIMARY KEY,
                portfolio_company_id INTEGER NOT NULL
                    REFERENCES "{S}".portfolio_companies(id) ON DELETE CASCADE,
                type VARCHAR(64),
                category VARCHAR(16) NOT NULL,
                entity_id INTEGER,
                discrepency_text TEXT NOT NULL,
                status VARCHAR(64),
                enable BOOLEAN NOT NULL DEFAULT TRUE,
                l1_reviewer_remarks TEXT,
                l2_reviewer_remarks TEXT,
                highlighted_to_investor BOOLEAN NOT NULL DEFAULT FALSE,
                variance_category TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_discrepancies_company ON "{S}".discrepancies (portfolio_company_id);
            """
        )
    )
