"""Add efront, currency, ownership_records, and company_data_raw tables.

Revision ID: 0030_efront_currency_ownership_company_raw_tables
Revises: 0029_portfolio_company_currency
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_efront_currency_ownership_company_raw_tables"
down_revision = "0029_portfolio_company_currency"
branch_labels = None
depends_on = None

S = "portfolioauditreview"


def upgrade() -> None:
    op.create_table(
        "portfolio_review_groups_users_efront",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_name", sa.String(500), nullable=True),
        sa.Column("user_identifier", sa.String(500), nullable=True),
        sa.Column("first_name", sa.String(500), nullable=True),
        sa.Column("last_name", sa.String(500), nullable=True),
        sa.Column("email", sa.String(500), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=True),
        sa.Column("load_time", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index(
        op.f("ix_portfolio_review_groups_users_efront_id"),
        "portfolio_review_groups_users_efront",
        ["id"],
        unique=False,
        schema=S,
    )

    op.create_table(
        "portfolio_review_groups_reviewer_efront",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("entity", sa.String(500), nullable=True),
        sa.Column("submitter", sa.String(500), nullable=True),
        sa.Column("supervisor", sa.String(500), nullable=True),
        sa.Column("reviewer", sa.String(500), nullable=True),
        sa.Column("reviewer_2", sa.String(500), nullable=True),
        sa.Column("acceptor", sa.String(500), nullable=True),
        sa.Column("administrator", sa.String(500), nullable=True),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("load_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index(
        op.f("ix_portfolio_review_groups_reviewer_efront_id"),
        "portfolio_review_groups_reviewer_efront",
        ["id"],
        unique=False,
        schema=S,
    )

    op.create_table(
        "portfolio_review_groups_contacts_efront",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("first_name", sa.String(500), nullable=True),
        sa.Column("last_name", sa.String(500), nullable=True),
        sa.Column("email", sa.String(500), nullable=True),
        sa.Column("contact_type", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index(
        op.f("ix_portfolio_review_groups_contacts_efront_id"),
        "portfolio_review_groups_contacts_efront",
        ["id"],
        unique=False,
        schema=S,
    )

    op.create_table(
        "currency",
        sa.Column("cid", sa.Numeric(38, 0), nullable=False),
        sa.Column("currency", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("cid"),
        schema=S,
    )
    op.create_index(
        op.f("ix_currency_cid"),
        "currency",
        ["cid"],
        unique=False,
        schema=S,
    )

    op.create_table(
        "ownership_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("fund_name", sa.String(), nullable=False),
        sa.Column("ownership_percentage", sa.Float(), nullable=False),
        sa.Column("cid", sa.Numeric(38, 0), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index(
        op.f("ix_ownership_records_id"),
        "ownership_records",
        ["id"],
        unique=False,
        schema=S,
    )

    op.create_table(
        "company_data_raw",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cid", sa.Integer(), nullable=True),
        sa.Column("reporting_date", sa.Date(), nullable=True),
        sa.Column("booking_type", sa.String(100), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("currency", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index(
        op.f("ix_company_data_raw_id"),
        "company_data_raw",
        ["id"],
        unique=False,
        schema=S,
    )
    op.create_index(
        op.f("ix_company_data_raw_name"),
        "company_data_raw",
        ["name"],
        unique=False,
        schema=S,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_company_data_raw_name"), table_name="company_data_raw", schema=S)
    op.drop_index(op.f("ix_company_data_raw_id"), table_name="company_data_raw", schema=S)
    op.drop_table("company_data_raw", schema=S)

    op.drop_index(op.f("ix_ownership_records_id"), table_name="ownership_records", schema=S)
    op.drop_table("ownership_records", schema=S)

    op.drop_index(op.f("ix_currency_cid"), table_name="currency", schema=S)
    op.drop_table("currency", schema=S)

    op.drop_index(
        op.f("ix_portfolio_review_groups_contacts_efront_id"),
        table_name="portfolio_review_groups_contacts_efront",
        schema=S,
    )
    op.drop_table("portfolio_review_groups_contacts_efront", schema=S)

    op.drop_index(
        op.f("ix_portfolio_review_groups_reviewer_efront_id"),
        table_name="portfolio_review_groups_reviewer_efront",
        schema=S,
    )
    op.drop_table("portfolio_review_groups_reviewer_efront", schema=S)

    op.drop_index(
        op.f("ix_portfolio_review_groups_users_efront_id"),
        table_name="portfolio_review_groups_users_efront",
        schema=S,
    )
    op.drop_table("portfolio_review_groups_users_efront", schema=S)
