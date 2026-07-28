"""
Deployment-safe runner for the review-cycle rollover.

Defaults to DRY-RUN: the script runs the full rollover inside a transaction
that is ROLLED BACK at the end — you see exactly what would change, nothing
is persisted.  Pass --live to commit.

Usage (in deployment, with Doppler injecting POSTGRES_*):

    doppler run -- bash -c 'PYTHONPATH=. python scripts/run_rollover_deployment.py \\
        --source CY20-FY21 --target CY21-FY22'

    doppler run -- bash -c 'PYTHONPATH=. python scripts/run_rollover_deployment.py \\
        --source CY20-FY21 --target CY21-FY22 --live'

Notes:
  * --source / --target are required.  We never auto-derive in deployment to
    avoid accidentally targeting an already-active cycle.
  * In dry-run mode the service still calls session.commit() internally, but
    because we wrap the whole thing in an outer transaction with rollback,
    nothing actually lands.
  * After --live, run `scripts/verify_rollover_sanity.sql` (or .py) to confirm.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy.orm import Session

from src.db.session import sync_engine
from src.services.review_cycle_rollover import rollover_review_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("deploy-rollover")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Source cycle id, e.g. CY20-FY21")
    ap.add_argument("--target", required=True, help="Target cycle id, e.g. CY21-FY22")
    ap.add_argument(
        "--live",
        action="store_true",
        help="Actually commit. Default is dry-run with rollback.",
    )
    args = ap.parse_args(argv)

    mode = "LIVE — will COMMIT" if args.live else "DRY-RUN — will ROLLBACK"
    log.info("=" * 60)
    log.info("Mode: %s", mode)
    log.info("Source: %s   Target: %s", args.source, args.target)
    log.info("=" * 60)

    # Outer transaction; the service's internal commit() releases a SAVEPOINT
    # rather than actually committing when we bind the session to a connection
    # we control.
    connection = sync_engine.connect()
    outer_tx = connection.begin()
    session = Session(bind=connection, autoflush=False)
    nested = connection.begin_nested()

    from sqlalchemy import event

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            nested = connection.begin_nested()

    try:
        result = rollover_review_cycle(
            session,
            source_cycle_id=args.source,
            target_cycle_id=args.target,
        )
        log.info("Result:\n%s", json.dumps(result, indent=2, default=str))

        if args.live:
            outer_tx.commit()
            log.info("*** COMMITTED. ***")
        else:
            outer_tx.rollback()
            log.info("*** ROLLED BACK (dry run).  Re-run with --live to commit. ***")
        return 0 if result.get("status") in ("ok", "skipped") else 1
    except Exception:
        outer_tx.rollback()
        log.exception("rollover failed; rolled back")
        return 2
    finally:
        session.close()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
