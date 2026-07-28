"""Align company_data_raw with Snowflake company master.

Revision ID: 0040_company_data_raw_company_master
Revises: 0039_entity_fy_end
"""

from __future__ import annotations

from alembic import op

revision = "0040_company_data_raw_company_master"
down_revision = "0039_entity_fy_end"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ALTER COLUMN id TYPE NUMERIC(38, 0) USING id::numeric'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ALTER COLUMN cid TYPE NUMERIC(38, 0) USING cid::numeric'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ALTER COLUMN name TYPE VARCHAR(256)'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS short_description TEXT'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS is_venture BOOLEAN'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS is_seed BOOLEAN'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS is_growth BOOLEAN'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS display_name VARCHAR(400)'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS reporting_date'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS booking_type'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS currency'
    )


def downgrade() -> None:
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS reporting_date DATE'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS booking_type VARCHAR(100)'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ADD COLUMN IF NOT EXISTS currency VARCHAR(256)'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS short_description'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS is_venture'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS is_seed'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS is_growth'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'DROP COLUMN IF EXISTS display_name'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ALTER COLUMN id TYPE VARCHAR USING id::text'
    )
    op.execute(
        f'ALTER TABLE "{S}".company_data_raw '
        f'ALTER COLUMN cid TYPE INTEGER USING cid::integer'
    )
