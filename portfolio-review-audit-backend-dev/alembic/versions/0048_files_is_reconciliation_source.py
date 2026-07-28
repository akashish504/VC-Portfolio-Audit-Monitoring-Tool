"""Add is_reconciliation_source to files (authoritative AFS file per entity/cycle).

When an entity legitimately has more than one audited-financials file for a cycle, exactly
one is the source whose extraction drives reconciliation / breakdowns / query emails. This
flag marks it; a partial unique index enforces at most one true per (entity_id,
review_cycle_id). Default false → the resolver falls back to the latest non-empty file.

Revision ID: 0048_files_is_reconciliation_source
Revises: 0047_org_chart_batch_mapping_uploaded_at
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_files_is_reconciliation_source"
down_revision = "0047_org_chart_batch_mapping_uploaded_at"
branch_labels = None
depends_on = None

S = "portfolioauditreview"
_INDEX = "uq_files_primary_afs_per_entity_cycle"


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "is_reconciliation_source",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=S,
    )
    # At most one primary AFS file per (entity, review cycle).
    op.create_index(
        _INDEX,
        "files",
        ["entity_id", "review_cycle_id"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("is_reconciliation_source"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="files", schema=S)
    op.drop_column("files", "is_reconciliation_source", schema=S)
