"""
Populate PortfolioCompany rows from CompanyDataRaw (Snowflake company master).

For each CompanyDataRaw row that has no PortfolioCompany entry anywhere in the
database, create one PortfolioCompany row per ReviewCycle.

Only investment_stage is populated; company_stage and company_phase_category are
left as None.
"""
from __future__ import annotations

import decimal
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import APP_SCHEMA, CompanyDataRaw, CompanySyncNotification, PortfolioCompany, PortfolioCompanyMetadata, ReviewCycle
from src.db.session import get_sync_db

logger = logging.getLogger(__name__)


def _cid_to_str(cid: object) -> Optional[str]:
    """Convert Numeric(38,0) cid to the string form stored in PortfolioCompany.company_id."""
    if cid is None:
        return None
    try:
        d = decimal.Decimal(cid) if not isinstance(cid, decimal.Decimal) else cid
    except (decimal.InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite():
        return None
    return str(int(d))


def _resolve_investment_stage(row: CompanyDataRaw) -> Optional[str]:
    if row.is_venture or row.is_growth:
        return "Venture/Growth"
    if row.is_seed:
        return "Seed/Surge"
    return None


def sync_company_master_to_portfolio(db: Optional[Session] = None) -> bool:
    """
    Create PortfolioCompany rows from CompanyDataRaw for any company not yet present.

    For each raw company not already in PortfolioCompany (matched by company_id),
    creates one row per ReviewCycle with investment_stage derived from the
    is_venture/is_growth/is_seed flags. Audit state is tracked per entity
    (``entities.status``); the company shell carries no review stage.

    Returns True on success, False on failure.
    """
    own_session = db is None
    if own_session:
        db = get_sync_db()

    try:
        from sqlalchemy import text

        db.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        raw_rows = db.execute(select(CompanyDataRaw)).scalars().all()
        if not raw_rows:
            logger.info("[sync_company_master] CompanyDataRaw is empty — nothing to sync")
            return True

        existing_company_ids: set[str] = set(
            db.execute(select(PortfolioCompany.company_id).distinct()).scalars().all()
        )

        review_cycles = db.execute(select(ReviewCycle)).scalars().all()

        created = 0
        created_companies = 0
        skipped_existing = 0
        skipped_bad_cid = 0
        skipped_missing_name = 0
        skipped_no_review_cycles = 0

        for row in raw_rows:
            company_id = _cid_to_str(row.cid)
            if company_id is None:
                logger.debug(
                    "[sync_company_master] Unparseable cid=%s — skipping", row.cid
                )
                skipped_bad_cid += 1
                continue

            if company_id in existing_company_ids:
                skipped_existing += 1
                continue

            name = (row.name or "").strip() or (row.display_name or "").strip()
            if not name:
                logger.debug(
                    "[sync_company_master] No name for cid=%s — skipping", company_id
                )
                skipped_missing_name += 1
                continue

            if not review_cycles:
                logger.warning(
                    "[sync_company_master] No ReviewCycle rows — cannot create PortfolioCompany for cid=%s",
                    company_id,
                )
                skipped_no_review_cycles += 1
                continue

            name = name[:255]
            investment_stage = _resolve_investment_stage(row)

            new_rows = [
                PortfolioCompany(
                    company_id=company_id,
                    name=name,
                    investment_stage=investment_stage,
                    review_cycle_id=cycle.id,
                )
                for cycle in review_cycles
            ]
            db.add_all(new_rows)

            # Create one PortfolioCompanyMetadata placeholder per cycle so the
            # company appears in Actionable Deals until a real fund/strategy is assigned.
            pcm_rows = [
                PortfolioCompanyMetadata(
                    fund="UNASSIGNED",
                    deal_id=company_id,
                    deal_name=name,
                    strategy="UNASSIGNED",
                    review_cycle_id=cycle.id,
                )
                for cycle in review_cycles
            ]
            db.add_all(pcm_rows)

            db.flush()
            created += len(new_rows)
            created_companies += 1
            existing_company_ids.add(company_id)

            notification_message = (
                f"New company added: {name} (ID: {company_id}, "
                f"stage: {investment_stage or 'Unknown'})"
            )
            db.add(
                CompanySyncNotification(
                    company_id=company_id,
                    company_name=name,
                    investment_stage=investment_stage,
                    message=notification_message,
                )
            )
            db.flush()

        if own_session:
            db.commit()

        logger.info(
            "[sync_company_master] Done: created=%d created_companies=%d "
            "skipped_existing=%d skipped_bad_cid=%d skipped_missing_name=%d "
            "skipped_no_review_cycles=%d",
            created,
            created_companies,
            skipped_existing,
            skipped_bad_cid,
            skipped_missing_name,
            skipped_no_review_cycles,
        )
        return True

    except Exception:
        logger.exception("[sync_company_master] Failed")
        if own_session:
            db.rollback()
        return False
    finally:
        if own_session:
            db.close()
