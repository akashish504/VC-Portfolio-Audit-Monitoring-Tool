"""
Snowflake → raw staging tables migration.

Refreshes all raw/efront tables from Snowflake so the manipulation phase has
up-to-date data to work with.  Mirrors the pattern from
portfolio-review-app-api-develop collective_migration.py / run_pr_submission_pipeline.py.

Tables refreshed (truncate-then-insert strategy, matching reference app):
  - pr_submission_data_raw           (from PR_AUDIT_SUBMISSION_DATA)
  - portfolio_review_groups_reviewer_efront  (from PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_REVIEWER_EFRONT)
  - portfolio_review_groups_users_efront     (from PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_USERS_EFRONT)
  - portfolio_review_groups_contacts_efront  (from PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_CONTACTS_EFRONT)
  - currency                         (from PR_AUDIT_CURRENCY)
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from src.configs.env import settings
from src.db.models import (
    APP_SCHEMA,
    CompanyDataRaw,
    Currency,
    PortfolioReviewGroupsContactsEfront,
    PortfolioReviewGroupsReviewerEfront,
    PortfolioReviewGroupsUsersEfront,
    PRSubmissionDataRaw,
)
from src.db.session import get_sync_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snowflake helpers
# ---------------------------------------------------------------------------

def _get_snowflake_conn():
    """Open a new Snowflake connection using env settings."""
    import snowflake.connector  # type: ignore[import]
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        load_pem_private_key,
    )

    private_key = load_pem_private_key(
        settings.SNOWFLAKE_PASSWORD.encode(),
        password=None,
        backend=default_backend(),
    )
    private_key_der = private_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )

    return snowflake.connector.connect(
        user=settings.SNOWFLAKE_USER,
        account=settings.SNOWFLAKE_ACCOUNT,
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        database=settings.SNOWFLAKE_DATABASE,
        schema=settings.SNOWFLAKE_SCHEMA,
        private_key=private_key_der,
    )


def _fetch(conn, query: str) -> tuple[list[str], list[tuple]]:
    """Execute *query* and return (columns, rows)."""
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [col[0].lower() for col in cursor.description]
    rows = cursor.fetchall()
    return columns, rows


def _row_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    return dict(zip(columns, row))


# ---------------------------------------------------------------------------
# Per-table migration helpers
# ---------------------------------------------------------------------------

def _load_cutoff_date(db: Session) -> Optional[date]:
    """Read cutoff_date from config_table; returns None if not set."""
    from src.db.models import ConfigTable
    from src.services.data_sync_schedule_config import CONFIG_KEY

    row = db.execute(select(ConfigTable).where(ConfigTable.key == CONFIG_KEY)).scalars().first()
    raw: dict[str, Any] = (row.value or {}) if row is not None else {}
    raw_cutoff = raw.get("cutoff_date")
    if not raw_cutoff:
        return None
    try:
        return date.fromisoformat(str(raw_cutoff))
    except (ValueError, TypeError):
        return None


def _migrate_pr_submission_data(conn, db: Session, cutoff_date: Optional[date] = None) -> int:
    if cutoff_date is not None:
        where_clause = f"WHERE REPORTING_DATE > '{cutoff_date.isoformat()}'"
        logger.info("[migration] pr_submission_data: applying cutoff_date filter > %s", cutoff_date)
    else:
        where_clause = ""
    query = f"""
        SELECT
            VLOOKUP_VALUE, CONCATENATE, REPORTING_DATE, STATUS, CID,
            FUND_FAMILY, COMPANY, TOTAL_OWNERSHIP, FYE,
            BOOKINGS_YR_1, BOOKINGS_YR_2, BOOKINGS_YR_3,
            BOOKINGS_Q_1, BOOKINGS_Q_2, BOOKINGS_Q_3, BOOKINGS_Q_4,
            REVENUE_YR_1, REVENUE_YR_2, REVENUE_YR_3,
            REVENUE_Q_1, REVENUE_Q_2, REVENUE_Q_3, REVENUE_Q_4,
            GPM_YR_1, GPM_YR_2, GPM_YR_3,
            GPM_Q_1, GPM_Q_2, GPM_Q_3, GPM_Q_4,
            EBITDA_YR_1, EBITDA_YR_2, EBITDA_YR_3,
            EBITDA_Q_1, EBITDA_Q_2, EBITDA_Q_3, EBITDA_Q_4,
            EBITDA_ACCEPTED_YR_1, EBITDA_ACCEPTED_YR_2, EBITDA_ACCEPTED_YR_3,
            EBITDA_ACCEPTED_Q_1, EBITDA_ACCEPTED_Q_2, EBITDA_ACCEPTED_Q_3, EBITDA_ACCEPTED_Q_4,
            PBT_YR_1, PBT_YR_2, PBT_YR_3,
            PBT_Q_1, PBT_Q_2, PBT_Q_3, PBT_Q_4,
            PAT_YR_1, PAT_YR_2, PAT_YR_3,
            PAT_Q_1, PAT_Q_2, PAT_Q_3, PAT_Q_4,
            FREE_CASH_FLOW_YR_1, FREE_CASH_FLOW_YR_2, FREE_CASH_FLOW_YR_3,
            FREE_CASH_FLOW_Q_1, FREE_CASH_FLOW_Q_2, FREE_CASH_FLOW_Q_3, FREE_CASH_FLOW_Q_4,
            CLARIFICATION, REVENUE_MODEL, DEAL_SOURCE, PAID_IN, POST_MONEY,
            LAST_FIN_YEAR, LAST_FIN_MONTH, DEBT_TOTAL, HEADCOUNT, CASH_ON_HAND,
            NEXT_FIN_MONTH, NEXT_FIN_YEAR, ROUND_SIZE, TIME_TO_EXIT, EXIT_MULTIPLE,
            EXECUTIVE_SEACHES, PARTNER_COMMENTS, ADDITIONAL_COMMENTS, INTERNAL_NOTES,
            BOD, INVESTORS, GET_REAL_1, GET_REAL_2, GET_REAL_3, GET_REAL_4, INCLUDE,
            REPORTING_YEAR, YEAR_OF_YEAR_1, YEAR,
            DEBT_TOTAL_, HEADCOUNT_, CASH_ON_HAND_, REVENUE_MODEL_, DEAL_SOURCE_,
            TIME_TO_EXIT_, EXPECTED_MULTIPLE, FYE_, MAX_DATE,
            BOOKINGS_YEAR, REVENUE_YEAR, GPM_YEAR, EBITDA_YEAR, PBT_YEAR, PAT_YEAR,
            FREE_CASH_FLOW_YEAR,
            WORKING_CAPITAL_CHANGE_YR_1, WORKING_CAPITAL_CHANGE_YR_2, WORKING_CAPITAL_CHANGE_YR_3,
            WORKING_CAPITAL_CHANGE_Q_1, WORKING_CAPITAL_CHANGE_Q_2, WORKING_CAPITAL_CHANGE_Q_3,
            WORKING_CAPITAL_CHANGE_Q_4,
            CURRENCY,
            ENDING_ARR_YR_1, ENDING_ARR_YR_2, ENDING_ARR_YR_3,
            ENDING_ARR_Q_1, ENDING_ARR_Q_2, ENDING_ARR_Q_3, ENDING_ARR_Q_4,
            IS_SAAS_COMPANY, AUDITED_EBITDA, AUDITED_REVENUE, REVIEWER_2,
            FINANCIAL_YEAR_END_DATE, WORKING_CAPITAL_FILE, GROUP_STRUCTURE_FILE,
            ESOP_YR_1, ESOP_YR_2, ESOP_YR_3,
            ESOP_Q_1, ESOP_Q_2, ESOP_Q_3, ESOP_Q_4,
            CAPEX_YR_1, CAPEX_YR_2, CAPEX_YR_3,
            CAPEX_Q_1, CAPEX_Q_2, CAPEX_Q_3, CAPEX_Q_4,
            DEPCR_YR_1, DEPCR_YR_2, DEPCR_YR_3,
            DEPCR_Q_1, DEPCR_Q_2, DEPCR_Q_3, DEPCR_Q_4,
            INT_COST_YR_1, INT_COST_YR_2, INT_COST_YR_3,
            INT_COST_Q_1, INT_COST_Q_2, INT_COST_Q_3, INT_COST_Q_4,
            ONE_OFF_YR_1, ONE_OFF_YR_2, ONE_OFF_YR_3,
            ONE_OFF_Q_1, ONE_OFF_Q_2, ONE_OFF_Q_3, ONE_OFF_Q_4,
            OTHER_NONCASH_YR_1, OTHER_NONCASH_YR_2, OTHER_NONCASH_YR_3,
            OTHER_NONCASH_Q_1, OTHER_NONCASH_Q_2, OTHER_NONCASH_Q_3, OTHER_NONCASH_Q_4,
            CLARIFICATION_PAID_IN, CLARIFICATION_POST_MONEY, AUDITED_PBT, COMMENT_FOR_ML,
            OTHER_INCOME_YR1, OTHER_INCOME_YR2, OTHER_INCOME_YR3,
            OTHER_INCOME_Q1, OTHER_INCOME_Q2, OTHER_INCOME_Q3, OTHER_INCOME_Q4,
            ACQUISITION_COST_YR1, ACQUISITION_COST_YR2, ACQUISITION_COST_YR3,
            ACQUISITION_COST_Q1, ACQUISITION_COST_Q2, ACQUISITION_COST_Q3, ACQUISITION_COST_Q4,
            BILLING_CURRENCY_1, BILLING_CURRENCY_2, BILLING_CURRENCY_3, BILLING_CURRENCY_4,
            BILLING_CURRENCY_5, BILLING_CURRENCY_6, BILLING_CURRENCY_7, BILLING_CURRENCY_8,
            BILLING_CURRENCY_9, BILLING_CURRENCY_10,
            BILLING_REVENUE_PERC1, BILLING_REVENUE_PERC2, BILLING_REVENUE_PERC3,
            BILLING_REVENUE_PERC4, BILLING_REVENUE_PERC5, BILLING_REVENUE_PERC6,
            BILLING_REVENUE_PERC7, BILLING_REVENUE_PERC8, BILLING_REVENUE_PERC9,
            BILLING_REVENUE_PERC10,
            GEO_L1, GEO_L2, SECTOR_L1, SECTOR_L2,
            FMV_COMPONDING_EXPECTATION, TIME_INVOLVEMENT, HELP_NEEDED,
            FOUNDERS_OR_COMPANY_EMPLOYEES, INVESTOR_NOMINEE_ON_BOARD,
            INDEPENDENT_DIRECTORS_ON_BOARD, NOMINEE_ON_BOARD_INTERNAL_RECORD,
            AUDIT_COMPLETED, TENTATIVE_AUDIT_COMPLETION_DATE,
            CASH_RUNWAY
        FROM PR_AUDIT_SUBMISSION_DATA
        {where_clause}
    """
    columns, rows = _fetch(conn, query)
    # db.execute(delete(PRSubmissionDataRaw))
    def _submission_key(reporting_date: Any, cid: Any) -> Optional[tuple[date, str]]:
        if reporting_date is None or cid is None:
            return None
        if isinstance(reporting_date, datetime):
            reporting_date = reporting_date.date()
        if not isinstance(reporting_date, date):
            return None
        try:
            cid_key = str(int(cid))
        except (TypeError, ValueError):
            cid_key = str(cid).strip()
        return (reporting_date, cid_key) if cid_key else None

    existing_by_key = {
        key: record
        for record in db.execute(select(PRSubmissionDataRaw)).scalars()
        if (key := _submission_key(record.reporting_date, record.cid)) is not None
    }

    def _apply_submission_values(record: PRSubmissionDataRaw, values: dict[str, Any]) -> None:
        for column in PRSubmissionDataRaw.__table__.columns:
            if column.name in {"id", "created_at"}:
                continue
            if column.name in values:
                setattr(record, column.name, values.get(column.name))

    seen_new_by_key: dict[tuple[date, str], PRSubmissionDataRaw] = {}
    records = []
    updated_existing = 0
    updated_pending = 0
    for row in rows:
        r = _row_dict(columns, row)
        if r.get("status") == "ACCEPTED":   # need to chek for correct value.
            key = _submission_key(r.get("reporting_date"), r.get("cid"))
            if key is not None:
                existing_record = existing_by_key.get(key)
                if existing_record is not None:
                    _apply_submission_values(existing_record, r)
                    updated_existing += 1
                    logger.debug("[migration] updated existing: cid=%s fye=%s", r.get("cid"), r.get("fye"))
                    continue

                pending_record = seen_new_by_key.get(key)
                if pending_record is not None:
                    _apply_submission_values(pending_record, r)
                    updated_pending += 1
                    logger.debug("[migration] updated pending: cid=%s fye=%s", r.get("cid"), r.get("fye"))
                    continue

            logger.debug("[migration] inserting new: cid=%s fye=%s", r.get("cid"), r.get("fye"))
            record = PRSubmissionDataRaw(
                id=str(uuid.uuid4()),
                vlookup_value=r.get("vlookup_value"),
                concatenate=r.get("concatenate"),
                reporting_date=r.get("reporting_date"),
                status=r.get("status"),
                cid=r.get("cid"),
                fund_family=r.get("fund_family"),
                company=r.get("company"),
                total_ownership=r.get("total_ownership"),
                fye=r.get("fye"),
                bookings_yr_1=r.get("bookings_yr_1"),
                bookings_yr_2=r.get("bookings_yr_2"),
                bookings_yr_3=r.get("bookings_yr_3"),
                bookings_q_1=r.get("bookings_q_1"),
                bookings_q_2=r.get("bookings_q_2"),
                bookings_q_3=r.get("bookings_q_3"),
                bookings_q_4=r.get("bookings_q_4"),
                currency=r.get("currency"),
                revenue_yr_1=r.get("revenue_yr_1"),
                revenue_yr_2=r.get("revenue_yr_2"),
                revenue_yr_3=r.get("revenue_yr_3"),
                revenue_q_1=r.get("revenue_q_1"),
                revenue_q_2=r.get("revenue_q_2"),
                revenue_q_3=r.get("revenue_q_3"),
                revenue_q_4=r.get("revenue_q_4"),
                gpm_yr_1=r.get("gpm_yr_1"),
                gpm_yr_2=r.get("gpm_yr_2"),
                gpm_yr_3=r.get("gpm_yr_3"),
                gpm_q_1=r.get("gpm_q_1"),
                gpm_q_2=r.get("gpm_q_2"),
                gpm_q_3=r.get("gpm_q_3"),
                gpm_q_4=r.get("gpm_q_4"),
                ebitda_yr_1=r.get("ebitda_yr_1"),
                ebitda_yr_2=r.get("ebitda_yr_2"),
                ebitda_yr_3=r.get("ebitda_yr_3"),
                ebitda_q_1=r.get("ebitda_q_1"),
                ebitda_q_2=r.get("ebitda_q_2"),
                ebitda_q_3=r.get("ebitda_q_3"),
                ebitda_q_4=r.get("ebitda_q_4"),
                pbt_yr_1=r.get("pbt_yr_1"),
                pbt_yr_2=r.get("pbt_yr_2"),
                pbt_yr_3=r.get("pbt_yr_3"),
                pbt_q_1=r.get("pbt_q_1"),
                pbt_q_2=r.get("pbt_q_2"),
                pbt_q_3=r.get("pbt_q_3"),
                pbt_q_4=r.get("pbt_q_4"),
                pat_yr_1=r.get("pat_yr_1"),
                pat_yr_2=r.get("pat_yr_2"),
                pat_yr_3=r.get("pat_yr_3"),
                pat_q_1=r.get("pat_q_1"),
                pat_q_2=r.get("pat_q_2"),
                pat_q_3=r.get("pat_q_3"),
                pat_q_4=r.get("pat_q_4"),
                free_cash_flow_yr_1=r.get("free_cash_flow_yr_1"),
                free_cash_flow_yr_2=r.get("free_cash_flow_yr_2"),
                free_cash_flow_yr_3=r.get("free_cash_flow_yr_3"),
                free_cash_flow_q_1=r.get("free_cash_flow_q_1"),
                free_cash_flow_q_2=r.get("free_cash_flow_q_2"),
                free_cash_flow_q_3=r.get("free_cash_flow_q_3"),
                free_cash_flow_q_4=r.get("free_cash_flow_q_4"),
                clarification=r.get("clarification"),
                revenue_model=r.get("revenue_model"),
                deal_source=r.get("deal_source"),
                paid_in=r.get("paid_in"),
                post_money=r.get("post_money"),
                last_fin_year=r.get("last_fin_year"),
                last_fin_month=r.get("last_fin_month"),
                debt_total=r.get("debt_total"),
                headcount=r.get("headcount"),
                cash_on_hand=r.get("cash_on_hand"),
                next_fin_month=r.get("next_fin_month"),
                next_fin_year=r.get("next_fin_year"),
                round_size=r.get("round_size"),
                time_to_exit=r.get("time_to_exit"),
                exit_multiple=r.get("exit_multiple"),
                executive_seaches=r.get("executive_seaches"),
                partner_comments=r.get("partner_comments"),
                additional_comments=r.get("additional_comments"),
                internal_notes=r.get("internal_notes"),
                bod=r.get("bod"),
                investors=r.get("investors"),
                get_real_1=r.get("get_real_1"),
                get_real_2=r.get("get_real_2"),
                get_real_3=r.get("get_real_3"),
                get_real_4=r.get("get_real_4"),
                include=r.get("include"),
                reporting_year=r.get("reporting_year"),
                year_of_year_1=r.get("year_of_year_1"),
                year=r.get("year"),
                debt_total_=r.get("debt_total_"),
                headcount_=r.get("headcount_"),
                cash_on_hand_=r.get("cash_on_hand_"),
                revenue_model_=r.get("revenue_model_"),
                deal_source_=r.get("deal_source_"),
                time_to_exit_=r.get("time_to_exit_"),
                expected_multiple=r.get("expected_multiple"),
                fye_=r.get("fye_"),
                max_date=r.get("max_date"),
                bookings_year=r.get("bookings_year"),
                revenue_year=r.get("revenue_year"),
                gpm_year=r.get("gpm_year"),
                ebitda_year=r.get("ebitda_year"),
                pbt_year=r.get("pbt_year"),
                pat_year=r.get("pat_year"),
                free_cash_flow_year=r.get("free_cash_flow_year"),
                working_capital_change_yr_1=r.get("working_capital_change_yr_1"),
                working_capital_change_yr_2=r.get("working_capital_change_yr_2"),
                working_capital_change_yr_3=r.get("working_capital_change_yr_3"),
                working_capital_change_q_1=r.get("working_capital_change_q_1"),
                working_capital_change_q_2=r.get("working_capital_change_q_2"),
                working_capital_change_q_3=r.get("working_capital_change_q_3"),
                working_capital_change_q_4=r.get("working_capital_change_q_4"),
                ending_arr_yr_1=r.get("ending_arr_yr_1"),
                ending_arr_yr_2=r.get("ending_arr_yr_2"),
                ending_arr_yr_3=r.get("ending_arr_yr_3"),
                ending_arr_q_1=r.get("ending_arr_q_1"),
                ending_arr_q_2=r.get("ending_arr_q_2"),
                ending_arr_q_3=r.get("ending_arr_q_3"),
                ending_arr_q_4=r.get("ending_arr_q_4"),
                is_saas_company=r.get("is_saas_company"),
                audited_ebitda=r.get("audited_ebitda"),
                audited_revenue=r.get("audited_revenue"),
                reviewer_2=r.get("reviewer_2"),
                financial_year_end_date=r.get("financial_year_end_date"),
                working_capital_file=r.get("working_capital_file"),
                group_structure_file=r.get("group_structure_file"),
                esop_yr_1=r.get("esop_yr_1"),
                esop_yr_2=r.get("esop_yr_2"),
                esop_yr_3=r.get("esop_yr_3"),
                esop_q_1=r.get("esop_q_1"),
                esop_q_2=r.get("esop_q_2"),
                esop_q_3=r.get("esop_q_3"),
                esop_q_4=r.get("esop_q_4"),
                capex_yr_1=r.get("capex_yr_1"),
                capex_yr_2=r.get("capex_yr_2"),
                capex_yr_3=r.get("capex_yr_3"),
                capex_q_1=r.get("capex_q_1"),
                capex_q_2=r.get("capex_q_2"),
                capex_q_3=r.get("capex_q_3"),
                capex_q_4=r.get("capex_q_4"),
                depcr_yr_1=r.get("depcr_yr_1"),
                depcr_yr_2=r.get("depcr_yr_2"),
                depcr_yr_3=r.get("depcr_yr_3"),
                depcr_q_1=r.get("depcr_q_1"),
                depcr_q_2=r.get("depcr_q_2"),
                depcr_q_3=r.get("depcr_q_3"),
                depcr_q_4=r.get("depcr_q_4"),
                int_cost_yr_1=r.get("int_cost_yr_1"),
                int_cost_yr_2=r.get("int_cost_yr_2"),
                int_cost_yr_3=r.get("int_cost_yr_3"),
                int_cost_q_1=r.get("int_cost_q_1"),
                int_cost_q_2=r.get("int_cost_q_2"),
                int_cost_q_3=r.get("int_cost_q_3"),
                int_cost_q_4=r.get("int_cost_q_4"),
                one_off_yr_1=r.get("one_off_yr_1"),
                one_off_yr_2=r.get("one_off_yr_2"),
                one_off_yr_3=r.get("one_off_yr_3"),
                one_off_q_1=r.get("one_off_q_1"),
                one_off_q_2=r.get("one_off_q_2"),
                one_off_q_3=r.get("one_off_q_3"),
                one_off_q_4=r.get("one_off_q_4"),
                other_noncash_yr_1=r.get("other_noncash_yr_1"),
                other_noncash_yr_2=r.get("other_noncash_yr_2"),
                other_noncash_yr_3=r.get("other_noncash_yr_3"),
                other_noncash_q_1=r.get("other_noncash_q_1"),
                other_noncash_q_2=r.get("other_noncash_q_2"),
                other_noncash_q_3=r.get("other_noncash_q_3"),
                other_noncash_q_4=r.get("other_noncash_q_4"),
                comment_for_ml=r.get("comment_for_ml"),
                clarification_paid_in=r.get("clarification_paid_in"),
                clarification_post_money=r.get("clarification_post_money"),
                audited_pbt=r.get("audited_pbt"),
                ebitda_accepted_yr_1=r.get("ebitda_accepted_yr_1"),
                ebitda_accepted_yr_2=r.get("ebitda_accepted_yr_2"),
                ebitda_accepted_yr_3=r.get("ebitda_accepted_yr_3"),
                ebitda_accepted_q_1=r.get("ebitda_accepted_q_1"),
                ebitda_accepted_q_2=r.get("ebitda_accepted_q_2"),
                ebitda_accepted_q_3=r.get("ebitda_accepted_q_3"),
                ebitda_accepted_q_4=r.get("ebitda_accepted_q_4"),
                acquisition_cost_yr1=r.get("acquisition_cost_yr1"),
                acquisition_cost_yr2=r.get("acquisition_cost_yr2"),
                acquisition_cost_yr3=r.get("acquisition_cost_yr3"),
                acquisition_cost_q1=r.get("acquisition_cost_q1"),
                acquisition_cost_q2=r.get("acquisition_cost_q2"),
                acquisition_cost_q3=r.get("acquisition_cost_q3"),
                acquisition_cost_q4=r.get("acquisition_cost_q4"),
                other_income_yr1=r.get("other_income_yr1"),
                other_income_yr2=r.get("other_income_yr2"),
                other_income_yr3=r.get("other_income_yr3"),
                other_income_q1=r.get("other_income_q1"),
                other_income_q2=r.get("other_income_q2"),
                other_income_q3=r.get("other_income_q3"),
                other_income_q4=r.get("other_income_q4"),
                billing_currency_1=r.get("billing_currency_1"),
                billing_currency_2=r.get("billing_currency_2"),
                billing_currency_3=r.get("billing_currency_3"),
                billing_currency_4=r.get("billing_currency_4"),
                billing_currency_5=r.get("billing_currency_5"),
                billing_currency_6=r.get("billing_currency_6"),
                billing_currency_7=r.get("billing_currency_7"),
                billing_currency_8=r.get("billing_currency_8"),
                billing_currency_9=r.get("billing_currency_9"),
                billing_currency_10=r.get("billing_currency_10"),
                billing_revenue_perc1=r.get("billing_revenue_perc1"),
                billing_revenue_perc2=r.get("billing_revenue_perc2"),
                billing_revenue_perc3=r.get("billing_revenue_perc3"),
                billing_revenue_perc4=r.get("billing_revenue_perc4"),
                billing_revenue_perc5=r.get("billing_revenue_perc5"),
                billing_revenue_perc6=r.get("billing_revenue_perc6"),
                billing_revenue_perc7=r.get("billing_revenue_perc7"),
                billing_revenue_perc8=r.get("billing_revenue_perc8"),
                billing_revenue_perc9=r.get("billing_revenue_perc9"),
                billing_revenue_perc10=r.get("billing_revenue_perc10"),
                geo_l1=r.get("geo_l1"),
                geo_l2=r.get("geo_l2"),
                sector_l1=r.get("sector_l1"),
                sector_l2=r.get("sector_l2"),
                fmv_componding_expectation=r.get("fmv_componding_expectation"),
                time_involvement=r.get("time_involvement"),
                help_needed=r.get("help_needed"),
                founders_or_company_employees=r.get("founders_or_company_employees"),
                investor_nominee_on_board=r.get("investor_nominee_on_board"),
                independent_directors_on_board=r.get("independent_directors_on_board"),
                nominee_on_board_internal_record=r.get("nominee_on_board_internal_record"),
                audit_completed=r.get("audit_completed"),
                tentative_audit_completion_date=r.get("tentative_audit_completion_date"),
                cash_runway=r.get("cash_runway"),
                created_at=datetime.now(),
            )
            records.append(record)
            if key is not None:
                seen_new_by_key[key] = record
    db.add_all(records)
    logger.info(
        "[migration] pr_submission_data_raw: inserted %d rows, updated %d existing rows, updated %d pending rows",
        len(records),
        updated_existing,
        updated_pending,
    )
    return len(records)


def _migrate_reviewer_efront(conn, db: Session) -> int:
    query = """
        SELECT ENTITY, SUBMITTER, SUPERVISOR, REVIEWER, REVIEWER_2,
               ACCEPTOR, ADMINISTRATOR, ENTITY_TYPE, LOAD_TIME, CATEGORY
        FROM PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_REVIEWER_EFRONT
    """
    columns, rows = _fetch(conn, query)
    db.execute(delete(PortfolioReviewGroupsReviewerEfront))
    records = []
    for row in rows:
        r = _row_dict(columns, row)
        records.append(PortfolioReviewGroupsReviewerEfront(
            id=str(uuid.uuid4()),
            entity=r.get("entity"),
            submitter=r.get("submitter"),
            supervisor=r.get("supervisor"),
            reviewer=r.get("reviewer"),
            reviewer_2=r.get("reviewer_2"),
            acceptor=r.get("acceptor"),
            administrator=r.get("administrator"),
            entity_type=r.get("entity_type"),
            load_time=r.get("load_time"),
            category=r.get("category"),
        ))
    db.add_all(records)
    logger.info("[migration] portfolio_review_groups_reviewer_efront: inserted %d rows", len(records))
    return len(records)


def _migrate_users_efront(conn, db: Session) -> int:
    query = """
        SELECT GROUP_NAME, USER_IDENTIFIER, FIRST_NAME, LAST_NAME, EMAIL, LOCKED, LOAD_TIME
        FROM PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_USERS_EFRONT
    """
    columns, rows = _fetch(conn, query)
    db.execute(delete(PortfolioReviewGroupsUsersEfront))
    records = []
    for row in rows:
        r = _row_dict(columns, row)
        records.append(PortfolioReviewGroupsUsersEfront(
            id=str(uuid.uuid4()),
            group_name=r.get("group_name"),
            user_identifier=r.get("user_identifier"),
            first_name=r.get("first_name"),
            last_name=r.get("last_name"),
            email=r.get("email"),
            locked=r.get("locked") not in (None, "N", "n", False, 0),
            load_time=r.get("load_time"),
        ))
    db.add_all(records)
    logger.info("[migration] portfolio_review_groups_users_efront: inserted %d rows", len(records))
    return len(records)


def _migrate_contacts_efront(conn, db: Session) -> int:
    query = """
        SELECT GROUP_NAME, FIRST_NAME, LAST_NAME, EMAIL, CONTACT_TYPE
        FROM PR_AUDIT_PORTFOLIO_REVIEW_GROUPS_CONTACTS_EFRONT
    """
    columns, rows = _fetch(conn, query)
    db.execute(delete(PortfolioReviewGroupsContactsEfront))
    records = []
    for row in rows:
        r = _row_dict(columns, row)
        records.append(PortfolioReviewGroupsContactsEfront(
            id=str(uuid.uuid4()),
            group_name=r.get("group_name"),
            first_name=r.get("first_name"),
            last_name=r.get("last_name"),
            email=r.get("email"),
            contact_type=r.get("contact_type"),
        ))
    db.add_all(records)
    logger.info("[migration] portfolio_review_groups_contacts_efront: inserted %d rows", len(records))
    return len(records)


def _migrate_currency(conn, db: Session) -> int:
    query = "SELECT CID, CURRENCY FROM PR_AUDIT_CURRENCY"
    columns, rows = _fetch(conn, query)
    db.execute(delete(Currency))
    records = []
    for row in rows:
        r = _row_dict(columns, row)
        cid = r.get("cid")
        if cid is None:
            continue
        records.append(Currency(
            cid=cid,
            currency=r.get("currency"),
        ))
    db.add_all(records)
    logger.info("[migration] currency: inserted %d rows", len(records))
    return len(records)


def _migrate_company_data_raw(conn, db: Session) -> int:
    query = """
        SELECT ID, NAME, ADMIN_COMPANY_ID, SHORT_DESCRIPTION,
               IS_VENTURE, IS_SEED, IS_GROWTH, DISPLAY_NAME
        FROM PEAKXV_WISDOM.PR_APP.PR_AUDIT_PULSE_COMPANY_MASTER
    """
    columns, rows = _fetch(conn, query)
    db.execute(delete(CompanyDataRaw))
    records = []
    for row in rows:
        r = _row_dict(columns, row)
        company_id = r.get("id")
        if company_id is None:
            continue
        records.append(CompanyDataRaw(
            id=company_id,
            cid=r.get("admin_company_id"),
            name=r.get("name"),
            short_description=r.get("short_description"),
            is_venture=r.get("is_venture"),
            is_seed=r.get("is_seed"),
            is_growth=r.get("is_growth"),
            display_name=r.get("display_name"),
        ))
    db.add_all(records)
    logger.info("[migration] company_data_raw: inserted %d rows", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def audit_raw_from_snowflake_migration() -> bool:
    """
    Refresh all raw/efront staging tables from Snowflake.
    Called by DataService as the migration phase.
    Returns True on success, False on failure.
    """
    logger.info("[data sync] Migration phase: Snowflake → raw tables")

    required = [
        settings.SNOWFLAKE_USER,
        settings.SNOWFLAKE_ACCOUNT,
        settings.SNOWFLAKE_DATABASE,
    ]
    if not all(required):
        logger.warning(
            "[migration] Snowflake credentials not configured — skipping migration phase. "
            "Set SNOWFLAKE_USER, SNOWFLAKE_ACCOUNT, SNOWFLAKE_DATABASE in env."
        )
        return True  # Non-fatal: manipulation phase can still run on existing raw data.

    conn = None
    db: Optional[Session] = None
    try:
        conn = _get_snowflake_conn()
        db = get_sync_db()
        db.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))

        cutoff_date = _load_cutoff_date(db)
        _migrate_pr_submission_data(conn, db, cutoff_date=cutoff_date)
        _migrate_reviewer_efront(conn, db)
        _migrate_users_efront(conn, db)
        _migrate_contacts_efront(conn, db)
        _migrate_currency(conn, db)
        _migrate_company_data_raw(conn, db)

        db.commit()
        logger.info("[data sync] Migration phase completed successfully")
        return True

    except Exception:
        logger.exception("[data sync] Migration phase failed")
        if db:
            db.rollback()
        return False
    finally:
        if db:
            db.close()
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    success = audit_raw_from_snowflake_migration()
    raise SystemExit(0 if success else 1)
