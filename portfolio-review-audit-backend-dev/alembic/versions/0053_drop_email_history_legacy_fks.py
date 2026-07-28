"""Drop legacy cross-schema FKs on email_history.

email_history.company_id and email_history.company_pr_cycle_id carry FK
constraints into the legacy portfolioreview schema (company_data,
pr_submission_data) inherited from the original schema dump. The ORM model
treats both columns as plain strings and the app links emails through
portfolio_company_id, so any value not seeded in the legacy tables aborted
the insert — killing audited-financials email ingestion before attachment
extraction. Dropping the constraints removes the failure mode; the columns
and their data are kept.

Constraint names differ between environments (each column even has duplicate
constraints locally), so after dropping the known names a catch-all sweep
removes any remaining FK on email_history that references a portfolioreview
table. The intra-schema fk_email_history_portfolio_company_id is untouched.

Revision ID: 0053_drop_email_history_legacy_fks
Revises: 0052_pcm_unique_add_review_cycle_id
Create Date: 2026-06-12
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0053_drop_email_history_legacy_fks"
down_revision = "0052_pcm_unique_add_review_cycle_id"
branch_labels = None
depends_on = None

S = "portfolioauditreview"
TABLE = f'"{S}".email_history'

KNOWN_LEGACY_FKS = (
    "email_history_company_id_fkey",
    "fk_email_history_company_data",
    "email_history_company_pr_cycle_id_fkey",
    "fk_email_history_portfolio_companies",  # despite the name: → pr_submission_data
)


def upgrade() -> None:
    for name in KNOWN_LEGACY_FKS:
        op.execute(text(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}'))

    # Catch-all for environment-specific constraint names: drop any remaining
    # FK on email_history that points at a table in the portfolioreview schema.
    op.execute(text(
        f"""
        DO $$
        DECLARE
            fk record;
        BEGIN
            FOR fk IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class ref ON ref.oid = con.confrelid
                JOIN pg_namespace refns ON refns.oid = ref.relnamespace
                WHERE con.conrelid = '{S}.email_history'::regclass
                  AND con.contype = 'f'
                  AND refns.nspname = 'portfolioreview'
            LOOP
                EXECUTE format(
                    'ALTER TABLE {TABLE} DROP CONSTRAINT %I', fk.conname
                );
            END LOOP;
        END $$;
        """
    ))


def downgrade() -> None:
    # Re-add the two canonical constraints as NOT VALID so existing rows
    # (NULLs and historical values) are not re-checked.
    op.execute(text(
        f"""
        DO $$
        BEGIN
            IF to_regclass('portfolioreview.company_data') IS NOT NULL THEN
                ALTER TABLE {TABLE}
                    ADD CONSTRAINT email_history_company_id_fkey
                    FOREIGN KEY (company_id)
                    REFERENCES portfolioreview.company_data (id) NOT VALID;
            END IF;
            IF to_regclass('portfolioreview.pr_submission_data') IS NOT NULL THEN
                ALTER TABLE {TABLE}
                    ADD CONSTRAINT email_history_company_pr_cycle_id_fkey
                    FOREIGN KEY (company_pr_cycle_id)
                    REFERENCES portfolioreview.pr_submission_data (id) NOT VALID;
            END IF;
        END $$;
        """
    ))
