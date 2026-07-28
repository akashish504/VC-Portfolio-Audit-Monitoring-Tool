"""Scheduled job + manual CLI: yearly review-cycle rollover (3:00 AM IST on 1 June).

Run manually:
    python -m src.scripts.data_manipulation.rollover_review_cycle
"""
from __future__ import annotations

import logging

from src.db.session import get_sync_db
from src.services.review_cycle_rollover import rollover_review_cycle

logger = logging.getLogger(__name__)


def run_rollover_review_cycle() -> None:
    db = get_sync_db()
    try:
        result = rollover_review_cycle(db)
        logger.info("rollover_review_cycle finished: %s", result)
    except Exception:
        logger.exception("rollover_review_cycle failed")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_rollover_review_cycle()
