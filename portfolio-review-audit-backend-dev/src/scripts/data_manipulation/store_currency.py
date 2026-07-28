"""
Populate PortfolioCompany.currency from the Currency raw table.

Equivalent of portfolio-review-app-api-develop store_currency.py, adapted for
the new schema:
  - Currency.cid (Numeric 38,0) → PortfolioCompany.company_id (String)
  - Updates PortfolioCompany.currency (String) for all matching company rows.
  - Review cycle resolved from PRSubmissionDataRaw.reporting_date for the
    matching cid; falls back to updating all rows for that company_id when no
    PR raw data is available.
"""
from __future__ import annotations

import decimal
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import APP_SCHEMA, Currency, PortfolioCompany, PRSubmissionDataRaw
from src.db.session import get_sync_db
from src.scripts.data_manipulation.sync_utils import (
    resolve_portfolio_company,
    resolve_review_cycle_for_date,
)

logger = logging.getLogger(__name__)


def _cid_to_str(cid: object) -> Optional[str]:
    """Convert Numeric(38,0) cid to the string form stored in PortfolioCompany.company_id.

    Uses Decimal to avoid float precision loss on large IDs.
    """
    if cid is None:
        return None
    try:
        d = decimal.Decimal(cid) if not isinstance(cid, decimal.Decimal) else cid
    except (decimal.InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite():
        return None
    return str(int(d))


def store_currency(db: Optional[Session] = None) -> bool:
    """
    Update PortfolioCompany.currency for all companies that have a Currency record.

    Strategy per cid:
    1. Find all PRSubmissionDataRaw rows for this cid to get known reporting_dates.
    2. For each reporting_date, resolve to a ReviewCycle, then find the single
       PortfolioCompany(company_id, review_cycle_id) and update its currency.
    3. If no PR raw rows exist for this cid, fall back to updating all
       PortfolioCompany rows with this company_id.
    4. Log and skip ambiguous / unresolved cases.

    Returns True on success, False on failure.
    """
    own_session = db is None
    if own_session:
        db = get_sync_db()

    try:
        from sqlalchemy import text
        db.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        currency_records = db.execute(select(Currency)).scalars().all()
        if not currency_records:
            logger.info("[store_currency] Currency table is empty — nothing to sync")
            return True

        updated = 0
        skipped = 0

        for rec in currency_records:
            if not rec.currency:
                continue

            cid_str = _cid_to_str(rec.cid)
            if not cid_str:
                logger.debug("[store_currency] Unparseable cid=%s — skipping", rec.cid)
                skipped += 1
                continue

            # Find PR raw rows for this cid to get reporting dates
            pr_rows = (
                db.execute(
                    select(PRSubmissionDataRaw).where(
                        PRSubmissionDataRaw.cid == rec.cid
                    )
                )
                .scalars()
                .all()
            )

            if pr_rows:
                for pr_row in pr_rows:
                    rc = resolve_review_cycle_for_date(db, pr_row.reporting_date)
                    if rc is None:
                        logger.debug(
                            "[store_currency] No review cycle for cid=%s reporting_date=%s — skipping row",
                            cid_str,
                            pr_row.reporting_date,
                        )
                        skipped += 1
                        continue

                    company = resolve_portfolio_company(
                        db, cid_str, rc.id, context=f"store_currency cid={cid_str}"
                    )
                    if company is None:
                        skipped += 1
                        continue

                    company.currency = rec.currency
                    db.add(company)
                    updated += 1
            else:
                # No PR rows — update all PortfolioCompany rows for this company_id
                companies = (
                    db.execute(
                        select(PortfolioCompany).where(
                            PortfolioCompany.company_id == cid_str
                        )
                    )
                    .scalars()
                    .all()
                )
                if not companies:
                    logger.debug(
                        "[store_currency] No PortfolioCompany rows for cid=%s — skipping",
                        cid_str,
                    )
                    skipped += 1
                    continue
                for company in companies:
                    company.currency = rec.currency
                    db.add(company)
                    updated += 1

        if own_session:
            db.commit()

        logger.info("[store_currency] Done: updated=%d skipped=%d", updated, skipped)
        return True

    except Exception:
        logger.exception("[store_currency] Failed")
        if own_session:
            db.rollback()
        return False
    finally:
        if own_session:
            db.close()
