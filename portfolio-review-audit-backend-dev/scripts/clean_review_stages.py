"""
Reset portfolio_companies rows whose review_stage holds a legacy or invalid value
to "Not applicable" (the canonical blank/rollover stage).

Legacy values targeted:
  - "Closed", "Closed - Flagged", "Resolved", "Completed"   ← silently written by
    the old patch_portfolio_company bug (stage in _COMPLETED_COMPANY_STAGES)
  - Any value not present in the CompanyReviewStage enum

Usage (env vars POSTGRES_* must be set, same as other scripts):

    # Dry-run — prints affected rows, makes no changes:
    PYTHONPATH=. python scripts/clean_review_stages.py

    # Commit the reset:
    PYTHONPATH=. python scripts/clean_review_stages.py --execute

    # Restrict to a single review cycle:
    PYTHONPATH=. python scripts/clean_review_stages.py --review-cycle-id CY24-FY25 --execute

Exits 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.db.models import PortfolioCompany
from src.db.session import get_sync_db
from src.schema.portfolio import CompanyReviewStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("clean-review-stages")

RESET_TARGET = CompanyReviewStage.NOT_APPLICABLE.value  # "Not applicable"

VALID_STAGES: frozenset[str] = frozenset(s.value for s in CompanyReviewStage)

# Values introduced by the pre-fix patch_portfolio_company bug and old CSV imports
KNOWN_LEGACY = frozenset({"Closed", "Closed - Flagged", "Resolved", "Completed"})


def _find_bad_rows(db: Session, review_cycle_id: Optional[str]) -> list[PortfolioCompany]:
    stmt = select(PortfolioCompany).where(PortfolioCompany.review_stage.isnot(None))
    if review_cycle_id:
        stmt = stmt.where(PortfolioCompany.review_cycle_id == review_cycle_id.strip())
    rows = db.execute(stmt).scalars().all()
    return [r for r in rows if r.review_stage not in VALID_STAGES]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Apply the reset (default is dry-run; no writes without this flag)",
    )
    ap.add_argument(
        "--review-cycle-id",
        metavar="CYCLE_ID",
        default=None,
        help="Restrict cleanup to a single review cycle id",
    )
    args = ap.parse_args(argv)

    db = get_sync_db()
    try:
        bad_rows = _find_bad_rows(db, args.review_cycle_id)

        if not bad_rows:
            log.info("No rows with invalid review_stage found — nothing to do.")
            return 0

        log.info(
            "Found %d row(s) with invalid/legacy review_stage%s:",
            len(bad_rows),
            f" in cycle {args.review_cycle_id!r}" if args.review_cycle_id else "",
        )
        for row in bad_rows:
            is_legacy = row.review_stage in KNOWN_LEGACY
            tag = " [known legacy]" if is_legacy else " [unknown/custom]"
            log.info(
                "  id=%-6d  company_id=%-30s  cycle=%-20s  stage=%r%s",
                row.id,
                row.company_id or "",
                row.review_cycle_id or "",
                row.review_stage,
                tag,
            )

        if not args.execute:
            log.info(
                "Dry-run complete. Re-run with --execute to reset %d row(s) to %r.",
                len(bad_rows),
                RESET_TARGET,
            )
            return 0

        bad_ids = [r.id for r in bad_rows]
        stmt = (
            update(PortfolioCompany)
            .where(PortfolioCompany.id.in_(bad_ids))
            .values(review_stage=RESET_TARGET)
            .execution_options(synchronize_session="fetch")
        )
        result = db.execute(stmt)
        db.commit()
        log.info(
            "Reset %d row(s) → review_stage=%r  (rowcount=%d)",
            len(bad_ids),
            RESET_TARGET,
            result.rowcount,
        )
        return 0

    except Exception:
        log.exception("Unexpected error during clean_review_stages")
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
