"""Scheduled job: append next review cycle and clone company shells (1 April yearly)."""

from __future__ import annotations

import logging

from src.db.session import get_sync_db
from src.services.review_cycle_provisioning import provision_next_review_cycle

logger = logging.getLogger(__name__)


def run_provision_next_review_cycle() -> None:
    db = get_sync_db()
    try:
        result = provision_next_review_cycle(db)
        logger.info("provision_next_review_cycle finished: %s", result)
    except Exception:
        logger.exception("provision_next_review_cycle failed")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_provision_next_review_cycle()
