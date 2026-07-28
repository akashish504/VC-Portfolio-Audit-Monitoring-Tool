"""
Populate PortfolioCompany contact fields from the efront reviewer/contacts tables.

Equivalent of portfolio-review-app-api-develop inserting_to_contact_names.py,
adapted for the new schema:

  - PortfolioCompany.poc_email_ids         ← TO contact emails
  - PortfolioCompany.poc_cc_email_ids      ← CC contact emails
  - PortfolioCompany.contact_name          ← first TO contact first name (primary)
  - PortfolioCompany.company_phase_category ← category from reviewer table

Matching:
  Reviewer table ENTITY → PortfolioCompany.name (case-insensitive).
  Review cycle resolved from the reviewer table's load_time via ReviewCycle date ranges.
  Falls back to latest PortfolioCompany row for that name when load_time is None or
  no ReviewCycle window matches.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    APP_SCHEMA,
    PortfolioCompany,
    PortfolioReviewGroupsContactsEfront,
    PortfolioReviewGroupsReviewerEfront,
    PortfolioReviewGroupsUsersEfront,
)
from src.db.session import get_sync_db
from src.scripts.data_manipulation.sync_utils import resolve_company_for_name_and_date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-assembly helpers
# ---------------------------------------------------------------------------

def _build_contact_map(db: Session) -> dict[str, dict]:
    """
    Replicate the SQL window from the old app's modified_query but in Python,
    using the already-populated raw efront tables.

    Returns a dict keyed by entity name (lower-cased for lookup):
      {
        entity_name: {
          "to_names": [...],
          "cc_names": [...],
          "to_emails": [...],
          "cc_emails": [...],
          "category": str | None,
          "canonical_entity": str,   # original casing
          "load_time": datetime | None,
        }
      }
    """
    reviewers = db.execute(select(PortfolioReviewGroupsReviewerEfront)).scalars().all()
    contacts_rows = db.execute(select(PortfolioReviewGroupsContactsEfront)).scalars().all()
    users_rows = db.execute(select(PortfolioReviewGroupsUsersEfront)).scalars().all()

    # Build lookup dicts (group_name lower → rows)
    contacts_by_group: dict[str, list[PortfolioReviewGroupsContactsEfront]] = defaultdict(list)
    for c in contacts_rows:
        if c.group_name:
            contacts_by_group[c.group_name.lower()].append(c)

    users_by_group: dict[str, list[PortfolioReviewGroupsUsersEfront]] = defaultdict(list)
    for u in users_rows:
        if u.group_name:
            users_by_group[u.group_name.lower()].append(u)

    result: dict[str, dict] = {}

    for rev in reviewers:
        entity = rev.entity
        if not entity:
            continue
        entity_key = entity.lower()

        if entity_key not in result:
            result[entity_key] = {
                "canonical_entity": entity,
                "to_names": [],
                "cc_names": [],
                "to_emails": [],
                "cc_emails": [],
                "category": None,
                "load_time": rev.load_time,
            }

        entry = result[entity_key]
        if rev.category and not entry["category"]:
            entry["category"] = rev.category

        # Submitter → TO contacts (joined via contacts table on first word of submitter)
        submitter = rev.submitter or ""
        submitter_group = submitter.split(" ")[0].lower() if submitter else ""
        if submitter_group:
            for c in contacts_by_group.get(submitter_group, []):
                contact_type = (c.contact_type or "").lower()
                if contact_type == "to":
                    if c.first_name and c.first_name not in entry["to_names"]:
                        entry["to_names"].append(c.first_name)
                    if c.email and c.email not in entry["to_emails"]:
                        entry["to_emails"].append(c.email)
                elif contact_type == "cc":
                    if c.first_name and c.first_name not in entry["cc_names"]:
                        entry["cc_names"].append(c.first_name)
                    if c.email and c.email not in entry["cc_emails"]:
                        entry["cc_emails"].append(c.email)

        # Reviewer_2 → CC users (joined via users table on first word of reviewer_2)
        reviewer2 = rev.reviewer_2 or ""
        reviewer2_group = reviewer2.split(" ")[0].lower() if reviewer2 else ""
        if reviewer2_group:
            for u in users_by_group.get(reviewer2_group, []):
                if u.first_name and u.first_name not in entry["cc_names"]:
                    entry["cc_names"].append(u.first_name)
                if u.email and u.email not in entry["cc_emails"]:
                    entry["cc_emails"].append(u.email)

    return result


# ---------------------------------------------------------------------------
# Main manipulation function
# ---------------------------------------------------------------------------

def insert_to_contact_names(db: Optional[Session] = None) -> bool:
    """
    Populate PortfolioCompany contact fields from efront reviewer/contacts data.
    Returns True on success, False on failure.
    """
    own_session = db is None
    if own_session:
        db = get_sync_db()

    try:
        from sqlalchemy import text
        db.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        contact_map = _build_contact_map(db)
        if not contact_map:
            logger.info("[contact_names] No reviewer data found — nothing to sync")
            return True

        updated = 0
        skipped = 0

        for entity_key, info in contact_map.items():
            entity_name = info["canonical_entity"]
            load_time = info.get("load_time")

            company = resolve_company_for_name_and_date(
                db, entity_name, load_time, context="insert_to_contact_names"
            )
            if company is None:
                logger.debug(
                    "[contact_names] No matching PortfolioCompany for entity=%s — skipping",
                    entity_name,
                )
                skipped += 1
                continue

            company.poc_email_ids = info["to_emails"] or company.poc_email_ids
            company.poc_cc_email_ids = info["cc_emails"] or company.poc_cc_email_ids
            if info["to_names"]:
                company.contact_name = info["to_names"][0]
            if info["category"]:
                company.company_phase_category = info["category"]

            db.add(company)
            updated += 1

        if own_session:
            db.commit()

        logger.info(
            "[contact_names] Done: updated=%d skipped=%d", updated, skipped
        )
        return True

    except Exception:
        logger.exception("[contact_names] Failed")
        if own_session:
            db.rollback()
        return False
    finally:
        if own_session:
            db.close()
