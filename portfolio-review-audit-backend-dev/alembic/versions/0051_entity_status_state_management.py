"""Move audit state management from portfolio_companies.review_stage to entities.status.

State is now tracked per entity. This backfills ``entities.status`` from each
entity's parent company ``review_stage`` (the 16-value lifecycle vocabulary),
preserving non-workflow scoping/archival states already set on the entity
('Archive Entity', 'Scoped Out', 'Scoped In'). Also normalizes the one legacy
workflow value with divergent casing ('Discrepancy Identified' -> 'Discrepancy
identified').

The ``review_stage`` column is intentionally left in place but dormant: no flow
writes it anymore. Dropping it is deferred to a later migration once external
consumers are confirmed migrated.

Revision ID: 0051_entity_status_state_management
Revises: 0050_portfolio_company_metadata
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op

revision = "0051_entity_status_state_management"
down_revision = "0050_portfolio_company_metadata"
branch_labels = None
depends_on = None

SCHEMA = "portfolioauditreview"

# Statuses on the entity that are NOT part of the review workflow and must be
# preserved as-is during backfill.
_PRESERVE = ("Archive Entity", "Scoped Out", "Scoped In")


def upgrade() -> None:
    preserve_list = ", ".join(f"'{s}'" for s in _PRESERVE)
    op.execute(
        f"""
        UPDATE {SCHEMA}.entities AS e
        SET status = COALESCE(NULLIF(TRIM(pc.review_stage), ''), 'Not applicable')
        FROM {SCHEMA}.portfolio_companies AS pc
        WHERE e.portfolio_company_id = pc.id
          AND COALESCE(TRIM(e.status), '') NOT IN ({preserve_list})
        """
    )
    # Normalize the single legacy workflow value whose casing differs from the
    # canonical review-stage vocabulary.
    op.execute(
        f"""
        UPDATE {SCHEMA}.entities
        SET status = 'Discrepancy identified'
        WHERE status = 'Discrepancy Identified'
        """
    )


def downgrade() -> None:
    # Data migration: reset workflow entities to the former default sub-state.
    # Scoping/archival states are left untouched.
    preserve_list = ", ".join(f"'{s}'" for s in _PRESERVE)
    op.execute(
        f"""
        UPDATE {SCHEMA}.entities
        SET status = 'Pending Review'
        WHERE COALESCE(TRIM(status), '') NOT IN ({preserve_list})
        """
    )
