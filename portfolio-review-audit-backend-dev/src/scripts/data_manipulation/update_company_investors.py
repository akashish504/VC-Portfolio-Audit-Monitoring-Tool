"""
Populate PortfolioCompany.investors from the efront reviewer/users tables.

Equivalent of portfolio-review-app-api-develop update_company_investors.py,
adapted for the new schema:
  - Builds a {entity_name → set(investor_full_names)} map from the reviewer/users
    join (same logic as old app: reviewer_2 first-word → users group_name).
  - Updates PortfolioCompany.investors (ARRAY Text) for the matching company row.
  - Review cycle resolved from the reviewer table's load_time via ReviewCycle date ranges.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    APP_SCHEMA,
    PortfolioCompany,
    PortfolioReviewGroupsReviewerEfront,
    PortfolioReviewGroupsUsersEfront,
)
from src.db.session import get_sync_db
from src.scripts.data_manipulation.sync_utils import resolve_company_for_name_and_date

logger = logging.getLogger(__name__)


def _build_investor_map(db: Session) -> dict[str, dict]:
    """
    Returns {entity_lower: {"canonical": str, "investors": set, "load_time": ...}}
    """
    reviewers = db.execute(select(PortfolioReviewGroupsReviewerEfront)).scalars().all()
    users_rows = db.execute(select(PortfolioReviewGroupsUsersEfront)).scalars().all()

    users_by_group: dict[str, list[PortfolioReviewGroupsUsersEfront]] = defaultdict(list)
    for u in users_rows:
        if u.group_name:
            users_by_group[u.group_name.lower()].append(u)

    investor_map: dict[str, dict] = {}

    for rev in reviewers:
        entity = rev.entity
        if not entity:
            continue
        entity_key = entity.lower()

        if entity_key not in investor_map:
            investor_map[entity_key] = {
                "canonical": entity,
                "investors": set(),
                "load_time": rev.load_time,
            }

        reviewer2 = rev.reviewer_2 or ""
        group_key = reviewer2.split(" ")[0].lower() if reviewer2 else ""
        if not group_key:
            continue

        for u in users_by_group.get(group_key, []):
            parts = [p for p in [u.first_name, u.last_name] if p and p.strip()]
            full_name = " ".join(parts).strip()
            if full_name:
                investor_map[entity_key]["investors"].add(full_name)

    return investor_map


def update_company_investors(db: Optional[Session] = None) -> bool:
    """
    Update PortfolioCompany.investors for all companies found in reviewer data.
    Returns True on success, False on failure.
    """
    own_session = db is None
    if own_session:
        db = get_sync_db()

    try:
        from sqlalchemy import text
        db.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        investor_map = _build_investor_map(db)
        if not investor_map:
            logger.info("[update_investors] No reviewer data found — nothing to sync")
            return True

        updated = 0
        skipped = 0

        for entity_key, info in investor_map.items():
            entity_name = info["canonical"]
            load_time = info.get("load_time")
            investor_list = sorted(info["investors"])

            if not investor_list:
                continue

            company = resolve_company_for_name_and_date(
                db, entity_name, load_time, context="update_company_investors"
            )
            if company is None:
                logger.debug(
                    "[update_investors] No matching PortfolioCompany for entity=%s — skipping",
                    entity_name,
                )
                skipped += 1
                continue

            company.investors = investor_list
            db.add(company)
            updated += 1

        if own_session:
            db.commit()

        logger.info("[update_investors] Done: updated=%d skipped=%d", updated, skipped)
        return True

    except Exception:
        logger.exception("[update_investors] Failed")
        if own_session:
            db.rollback()
        return False
    finally:
        if own_session:
            db.close()
