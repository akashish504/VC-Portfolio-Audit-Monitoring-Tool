"""
Raw staging → portfolio audit application tables.

Manipulation phase order (mirrors reference app collective_manipulation flow):
  1. sync_company_master_to_portfolio — seed PortfolioCompany rows from CompanyDataRaw
  2. insert_to_contact_names          — populate poc_email_ids / poc_cc_email_ids / contact_name
  3. store_currency                   — populate PortfolioCompany.currency from Currency table
  4. sync_submission_data             — populate FinancialDataSnowflake from PRSubmissionDataRaw
  5. update_company_investors         — populate PortfolioCompany.investors

Each step uses its own session (opened by the helper) so a failure in one step
is isolated and reported without rolling back the others.  The overall return
value is True only when all steps succeed.
"""

import logging

from sqlalchemy import text

from src.db.models import APP_SCHEMA
from src.db.session import get_sync_db
from src.scripts.data_manipulation.insert_to_contact_names import insert_to_contact_names
from src.scripts.data_manipulation.pr_submission_financial_sync import (
    sync_pr_submission_to_financial_data_snowflake,
)
from src.scripts.data_manipulation.store_currency import store_currency
from src.scripts.data_manipulation.sync_company_master_to_portfolio import (
    sync_company_master_to_portfolio,
)
from src.scripts.data_manipulation.update_company_investors import update_company_investors

logger = logging.getLogger(__name__)


def audit_application_tables_sync() -> bool:
    logger.info("[data sync] Manipulation phase starting")
    results: dict[str, bool] = {}

    # Step 1 — seed PortfolioCompany from company master
    try:
        results["sync_company_master_to_portfolio"] = sync_company_master_to_portfolio()
    except Exception:
        logger.exception("[data sync] sync_company_master_to_portfolio raised unexpectedly")
        results["sync_company_master_to_portfolio"] = False

    # Step 2 — contact names
    try:
        results["insert_to_contact_names"] = insert_to_contact_names()
    except Exception:
        logger.exception("[data sync] insert_to_contact_names raised unexpectedly")
        results["insert_to_contact_names"] = False

    # Step 3 — currency
    try:
        results["store_currency"] = store_currency()
    except Exception:
        logger.exception("[data sync] store_currency raised unexpectedly")
        results["store_currency"] = False

    # Step 4 — FinancialDataSnowflake from PR submission (formula-driven)
    session = get_sync_db()
    try:
        session.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))
        stats = sync_pr_submission_to_financial_data_snowflake(session)
        session.commit()
        logger.info("[data sync] sync_submission_data completed: %s", stats)
        results["sync_submission_data"] = True
    except Exception:
        logger.exception("[data sync] sync_submission_data failed")
        session.rollback()
        results["sync_submission_data"] = False
    finally:
        session.close()

    # Step 5 — investors
    try:
        results["update_company_investors"] = update_company_investors()
    except Exception:
        logger.exception("[data sync] update_company_investors raised unexpectedly")
        results["update_company_investors"] = False

    overall = all(results.values())
    logger.info("[data sync] Manipulation phase completed: %s", results)
    return overall
