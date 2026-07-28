"""
Manual test runner for classify_incoming_emails against the real DB.

Overrides ENV=prod so the function doesn't short-circuit.
Runs as a dry-run by default: shows what would be processed without committing.

Usage:
    .venv/bin/python scripts/test_classifier.py           # dry-run
    .venv/bin/python scripts/test_classifier.py --commit  # actually commit
"""
from __future__ import annotations

import argparse
import logging
import sys
import os

# Must set ENV before any src imports so settings picks it up
os.environ.setdefault("ENV", "prod")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_classifier")


def peek_source_db() -> None:
    """Show what's in SourceTempEmailHistory beyond the current checkpoint."""
    from src.db.session import get_sync_db, get_pr_source_db
    from src.db.models import SourceTempEmailHistory, EmailProcessingCheckPoint

    db = get_sync_db()
    source_db = get_pr_source_db()
    try:
        ck = (
            db.query(EmailProcessingCheckPoint)
            .filter(EmailProcessingCheckPoint.checkpoint_key == "email_classifier")
            .first()
        )
        last_id = 0
        if ck and ck.checkpoint_value:
            last_id = int(ck.checkpoint_value.get("last_processed_id", 0))

        total = (
            source_db.query(SourceTempEmailHistory)
            .filter(SourceTempEmailHistory.id > last_id)
            .count()
        )
        logger.info("Checkpoint last_processed_id=%s", last_id)
        logger.info("Unprocessed SourceTempEmailHistory rows: %s", total)

        sample = (
            source_db.query(SourceTempEmailHistory)
            .filter(SourceTempEmailHistory.id > last_id)
            .order_by(SourceTempEmailHistory.id)
            .limit(10)
            .all()
        )
        for row in sample:
            logger.info(
                "  id=%-6s from=%-40s subject=%r attachments=%s",
                row.id,
                row.from_email or "",
                (row.subject or "")[:80],
                len(row.attachments or []),
            )
    finally:
        db.close()
        source_db.close()


def run_classifier(commit: bool) -> None:
    if not commit:
        logger.info("DRY-RUN mode — wrapping classify_incoming_emails in a rolled-back transaction")

    from src.scripts.data_manipulation.classifying_incoming_emails import classify_incoming_emails
    from src.db import session as db_session

    if not commit:
        # Monkey-patch get_sync_db to return a session that always rolls back on close
        _orig_get_sync_db = db_session.get_sync_db

        def _dry_run_db():
            s = _orig_get_sync_db()
            _orig_close = s.close

            def _rollback_close():
                logger.info("DRY-RUN: rolling back instead of persisting")
                s.rollback()
                _orig_close()

            s.close = _rollback_close
            return s

        db_session.get_sync_db = _dry_run_db
        logger.info("Patched get_sync_db for dry-run rollback")

    logger.info("=" * 60)
    logger.info("Running classify_incoming_emails (ENV=prod)")
    logger.info("=" * 60)

    classify_incoming_emails()

    logger.info("=" * 60)
    logger.info("classify_incoming_emails completed")
    logger.info("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test classify_incoming_emails against the real DB")
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually commit changes (default: dry-run / rollback)",
    )
    args = parser.parse_args()

    logger.info("Peeking at source DB...")
    peek_source_db()

    if not args.commit:
        logger.info("")
        logger.info("Starting DRY-RUN (pass --commit to persist changes)")
    else:
        logger.info("")
        logger.info("Starting LIVE RUN — changes will be committed")

    run_classifier(commit=args.commit)


if __name__ == "__main__":
    main()
