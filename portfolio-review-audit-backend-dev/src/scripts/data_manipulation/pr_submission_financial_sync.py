"""
Sync ``portfolioauditreview.pr_submission_data_raw`` → ``financial_data_snowflake``
using the audited-financial → MIS matching rules (v2).

Per (company, review-cycle):
  1. Resolve company + cycle + stage group from each PR row (unchanged).
  2. Group the company's PR rows (MIS submissions, keyed by ``reporting_date``).
  3. P&L (revenue/ebitda/pbt/pat): pick the freshest submission in the forward
     window from the audited FY-end quarter; read Year 2 when the chosen quarter
     aligns with the FY-end quarter, else Year 1 (Surge/Seed always Year 1).
  4. Cash/Debt (balances): use the submission whose reporting date equals the
     audited FY-end exactly; abstain (leave unset) when there is none.
  5. Upsert ONE FinancialDataSnowflake per (company, cycle), respecting
     ``manually_edited_metrics``.

Formulas come from the v2 ConfigTable (``snowflake_pr_financial_mapping_v2``);
the deterministic record/year selection lives in ``snowflake_pr_match``.
"""
from __future__ import annotations

import asyncio
import decimal
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.db.models import APP_SCHEMA, ConfigTable, FinancialDataSnowflake, PortfolioCompany, PRSubmissionDataRaw
from src.db.session import AsyncSessionLocal, get_sync_db
from src.scripts.data_manipulation.sync_utils import (
    normalize_fye_raw_with_date,
    resolve_portfolio_company,
    resolve_review_cycle_for_fy_end,
)
from src.services.financial_data_extraction_sync import backfill_afs_currency_for_company
from src.services.fy_end import apply_fy_end_fields, fy_end_last_day
from src.services.snowflake_pr_formula_eval import evaluate_formula, numeric_identifier_whitelist_from_pr_submission_model
from src.services.snowflake_pr_financial_mapping import (
    SNOWFLAKE_PR_FINANCIAL_METRICS,
    resolve_stage_group,
)
from src.services.snowflake_pr_financial_mapping_v2 import (
    BALANCE_METRICS,
    PNL_METRICS,
    SNOWFLAKE_PR_V2_CONFIG_KEY,
    load_snowflake_pr_v2_full_sync,
)
from src.services.snowflake_pr_match import (
    STAGE_SURGE_SEED,
    YEAR_1,
    MisRecord,
    PnlSelection,
    select_balance_record,
    select_pnl_record,
)

logger = logging.getLogger(__name__)


def _backfill_afs_currency_sync(
    portfolio_company_id: int,
    review_cycle: str,
    mis_currency: str,
) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as async_db:
            async with async_db.begin():
                await backfill_afs_currency_for_company(
                    async_db,
                    portfolio_company_id=portfolio_company_id,
                    review_cycle=review_cycle,
                    mis_currency=mis_currency,
                )

    try:
        asyncio.run(_run())
    except Exception:
        logger.warning(
            "AFS currency backfill failed for company=%s cycle=%s mis_currency=%s",
            portfolio_company_id,
            review_cycle,
            mis_currency,
            exc_info=True,
        )


def _cell_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _cid_to_company_id_str(cid: Any) -> Optional[str]:
    if cid is None:
        return None
    try:
        d = decimal.Decimal(cid) if not isinstance(cid, decimal.Decimal) else cid
    except (decimal.InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite():
        return None
    return str(int(d))


def _norm_currency(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip().upper()
        if len(s) == 3 and s.isalpha():
            return s
    return None


def _variables_from_pr_row(row: PRSubmissionDataRaw, whitelist: list[str]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for name in whitelist:
        out[name] = _cell_to_float(getattr(row, name, None))
    return out


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _most_common(values: list[Optional[str]]) -> Optional[str]:
    """Most frequent non-None value (first-seen wins ties)."""
    counts: dict[str, int] = {}
    best: Optional[str] = None
    best_n = 0
    for v in values:
        if not v:
            continue
        counts[v] = counts.get(v, 0) + 1
        if counts[v] > best_n:
            best_n = counts[v]
            best = v
    return best


def _empty_stats(pr_rows_total: int = 0, **extra: Any) -> dict[str, int]:
    base = {
        "upserted": 0,
        "skipped_no_company": 0,
        "skipped_rows_no_cid": 0,
        "skipped_rows_no_cycle": 0,
        "skipped_invalid_bucket": 0,  # retained for caller compatibility (always 0 in v2)
        "skipped_no_stage_group": 0,
        "groups_total": 0,
        "pnl_abstained": 0,
        "balance_abstained": 0,
        "skipped_no_match": 0,
        "pr_rows_total": pr_rows_total,
    }
    base.update(extra)
    return base


def sync_pr_submission_to_financial_data_snowflake(session: Session) -> dict[str, int]:
    """
    Group PR rows per (company, cycle), apply the v2 matching rules, upsert one
    FinancialDataSnowflake per group.

    Rows are skipped during resolution when:
    - ``cid`` is missing
    - ``fye`` cannot be normalised or resolved to a ReviewCycle
    - no PortfolioCompany matches (company_id, cycle)
    - ``investment_stage`` does not map to a known stage group
    """
    # Safety guard: if the v2 config has never been saved, do NOT run — an empty
    # config would null every metric. Seed it (migration / Settings) first.
    cfg_row = (
        session.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY))
        .scalars()
        .first()
    )
    if cfg_row is None:
        logger.warning(
            "[snowflake pr sync] v2 config %r absent — skipping sync to avoid nulling metrics. "
            "Save the Snowflake PR formulas in Settings (or run the seed migration) first.",
            SNOWFLAKE_PR_V2_CONFIG_KEY,
        )
        return _empty_stats(skipped_config_absent=1)

    v2_groups = load_snowflake_pr_v2_full_sync(session)["groups"]
    whitelist = numeric_identifier_whitelist_from_pr_submission_model(PRSubmissionDataRaw)

    pr_rows = session.execute(select(PRSubmissionDataRaw)).scalars().all()
    logger.info("[snowflake pr sync] fetched %d total rows from pr_submission_data_raw", len(pr_rows))

    stats = _empty_stats(len(pr_rows))

    # ---- Phase 1: resolve each row → (company, cycle, stage group) and group ----
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    for row in pr_rows:
        cid_key = _cid_to_company_id_str(row.cid)
        if not cid_key:
            stats["skipped_rows_no_cid"] += 1
            continue

        # year-less fye ("Mar FY") resolves via the row's reporting_date
        normalized_fy_end = normalize_fye_raw_with_date(row.fye, row.reporting_date)
        if not normalized_fy_end:
            stats["skipped_rows_no_cycle"] += 1
            continue

        resolved_rc = resolve_review_cycle_for_fy_end(session, row.fye, reporting_date=row.reporting_date)
        if resolved_rc is None:
            stats["skipped_rows_no_cycle"] += 1
            continue

        pc = resolve_portfolio_company(
            session, cid_key, resolved_rc.id, context=f"pr_submission_sync id={row.id}"
        )
        if pc is None:
            stats["skipped_no_company"] += 1
            continue

        group_id = resolve_stage_group(getattr(pc, "investment_stage", None))
        if group_id is None:
            stats["skipped_no_stage_group"] += 1
            continue

        key = (pc.id, resolved_rc.id)
        grp = groups.get(key)
        if grp is None:
            grp = {
                "pc": pc,
                "cycle_id": resolved_rc.id,
                "gid": group_id,
                "cid_key": cid_key,
                "rows": [],
                "fye_norms": [],
            }
            groups[key] = grp
        grp["rows"].append(row)
        grp["fye_norms"].append(normalized_fy_end)

    stats["groups_total"] = len(groups)

    # ---- Phase 2: per group, select records, compute metrics, upsert ----
    for (pc_id, cycle_id), grp in groups.items():
        pc: PortfolioCompany = grp["pc"]
        gid: str = grp["gid"]
        rows: list[PRSubmissionDataRaw] = grp["rows"]

        rep_fye = _most_common(grp["fye_norms"])
        if not rep_fye:
            continue
        try:
            afs_fy_end_date = fy_end_last_day(rep_fye)
        except Exception:
            logger.warning("[snowflake pr sync] cannot derive FY-end date from fye=%r — skipping group", rep_fye)
            continue

        # Write fy_end back to the company (preserves existing behaviour).
        try:
            new_fy_end, new_fy_end_date = apply_fy_end_fields(fy_end=rep_fye)
            pc.fy_end = new_fy_end
            pc.fy_end_date = new_fy_end_date
            session.add(pc)
        except ValueError:
            logger.warning(
                "Snowflake PR sync: could not apply fy_end fields for fye=%r — skipping fy_end update",
                rep_fye,
            )

        # Build MIS candidates and an id→row map.
        candidates: list[MisRecord] = []
        row_by_id: dict[str, PRSubmissionDataRaw] = {}
        for r in rows:
            rid = str(r.id)
            row_by_id[rid] = r
            candidates.append(
                MisRecord(
                    row_id=rid,
                    reporting_date=_to_date(r.reporting_date),
                    year_of_year_1=_int_or_none(getattr(r, "year_of_year_1", None)),
                    financial_year=_int_or_none(getattr(r, "reporting_year", None)),
                )
            )

        pnl_sel: Optional[PnlSelection] = select_pnl_record(afs_fy_end_date, gid, candidates)
        # Surge/Seed back-compat: when no dated window match exists but rows do,
        # fall back to the freshest row as Year 1 (S/S is always Year 1, and the
        # old sync processed S/S even without a reporting_date).
        if pnl_sel is None and gid == STAGE_SURGE_SEED and candidates:
            fallback = max(candidates, key=lambda c: (c.reporting_date or date.min, c.row_id))
            pnl_sel = PnlSelection(
                record=fallback,
                year_slot=YEAR_1,
                aligned=False,
                reason="surge/seed fallback — no dated window match",
            )

        bal_rec: Optional[MisRecord] = select_balance_record(afs_fy_end_date, candidates)

        # Nothing matched at all → don't create/overwrite with an empty row.
        if pnl_sel is None and bal_rec is None:
            stats["skipped_no_match"] += 1
            logger.info(
                "[snowflake pr sync] no MIS match (pc=%s cycle=%s fye=%r) — skipping upsert",
                pc.id, cycle_id, rep_fye,
            )
            continue

        gconf = v2_groups.get(gid) or {}
        if gid == STAGE_SURGE_SEED:
            pnl_slot = "pnl"
        else:
            pnl_slot = "pnl_aligned" if (pnl_sel and pnl_sel.aligned) else "pnl_lagged"

        metrics_out: dict[str, Optional[float]] = {m: None for m in SNOWFLAKE_PR_FINANCIAL_METRICS}
        selection: dict[str, Any] = {}

        if pnl_sel is not None:
            pnl_row = row_by_id[pnl_sel.record.row_id]
            vars_pnl = _variables_from_pr_row(pnl_row, whitelist)
            pnl_formulas = gconf.get(pnl_slot) or {}
            for metric in PNL_METRICS:
                expr = (pnl_formulas.get(metric) or "").strip()
                metrics_out[metric] = evaluate_formula(expr, variables=vars_pnl) if expr else None
            selection["pnl"] = {
                "row_id": pnl_sel.record.row_id,
                "slot": pnl_slot,
                "year_slot": pnl_sel.year_slot,
                "aligned": pnl_sel.aligned,
                "reason": pnl_sel.reason,
                "reporting_date": str(pnl_row.reporting_date) if pnl_row.reporting_date else None,
                "year_stamp_ok": pnl_sel.year_stamp_ok,
            }
            if pnl_sel.year_stamp_ok is False:
                logger.warning(
                    "[snowflake pr sync] year-stamp mismatch pc=%s cycle=%s row=%s slot=%s",
                    pc.id, cycle_id, pnl_sel.record.row_id, pnl_slot,
                )
        else:
            stats["pnl_abstained"] += 1
            selection["pnl"] = {"status": "no_match"}

        if bal_rec is not None:
            bal_row = row_by_id[bal_rec.row_id]
            vars_bal = _variables_from_pr_row(bal_row, whitelist)
            bal_formulas = gconf.get("balance") or {}
            for metric in BALANCE_METRICS:
                expr = (bal_formulas.get(metric) or "").strip()
                metrics_out[metric] = evaluate_formula(expr, variables=vars_bal) if expr else None
            selection["balance"] = {
                "row_id": bal_rec.row_id,
                "reporting_date": str(bal_row.reporting_date) if bal_row.reporting_date else None,
                "status": "matched",
            }
        else:
            stats["balance_abstained"] += 1
            selection["balance"] = {"status": "no_exact_quarter"}

        cur_iso = _norm_currency(pc.currency)
        primary_row_id = (
            pnl_sel.record.row_id if pnl_sel is not None
            else (bal_rec.row_id if bal_rec is not None else str(rows[0].id))
        )
        payload = {
            "source_table": "pr_submission_data_raw",
            "company_id_snowflake": grp["cid_key"],
            "review_cycle": cycle_id,
            "fy_end": rep_fye,
            "audited_fy_end_date": afs_fy_end_date.isoformat(),
            "stage_group": gid,
            "selection": selection,
        }

        existing = (
            session.execute(
                select(FinancialDataSnowflake)
                .where(
                    FinancialDataSnowflake.portfolio_company_id == pc.id,
                    FinancialDataSnowflake.entity_id.is_(None),
                    FinancialDataSnowflake.review_cycle == cycle_id,
                )
                .order_by(FinancialDataSnowflake.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

        if existing is None:
            obj = FinancialDataSnowflake(
                portfolio_company_id=pc.id,
                entity_id=None,
                review_cycle=cycle_id,
                currency=cur_iso,
                source_ref=f"pr_submission:{primary_row_id}",
                payload=payload,
            )
            for metric in SNOWFLAKE_PR_FINANCIAL_METRICS:
                setattr(obj, metric, metrics_out.get(metric))
            session.add(obj)
        else:
            existing.review_cycle = cycle_id
            existing.source_ref = f"pr_submission:{primary_row_id}"
            existing.currency = cur_iso or existing.currency
            merged_payload = dict(existing.payload or {})
            manually_edited: set[str] = set(merged_payload.get("manually_edited_metrics") or [])
            merged_payload.update(payload)
            if manually_edited:
                merged_payload["manually_edited_metrics"] = sorted(manually_edited)
            existing.payload = merged_payload
            for metric in SNOWFLAKE_PR_FINANCIAL_METRICS:
                if metric not in manually_edited:
                    setattr(existing, metric, metrics_out.get(metric))
            session.add(existing)

        session.flush()
        if cur_iso:
            _backfill_afs_currency_sync(pc.id, cycle_id, cur_iso)

        stats["upserted"] += 1
        logger.info(
            "[snowflake pr sync] upserted: cid=%s fye=%r group=%s pnl_slot=%s "
            "pnl_row=%s bal=%s pc_id=%s cycle=%s",
            grp["cid_key"], rep_fye, gid, pnl_slot,
            selection["pnl"].get("row_id"), selection["balance"].get("status"),
            pc.id, cycle_id,
        )

    logger.info(
        "[snowflake pr sync] %s",
        {k: stats[k] for k in (
            "upserted", "groups_total", "skipped_rows_no_cid", "skipped_rows_no_cycle",
            "skipped_no_company", "skipped_no_stage_group", "pnl_abstained",
            "balance_abstained", "pr_rows_total",
        )},
    )
    return stats


def reapply_snowflake_pr_financial_mapping() -> dict[str, int]:
    """Re-run the PR → FinancialDataSnowflake sync standalone after a formula save."""
    session = get_sync_db()
    try:
        session.execute(text(f'SET search_path TO "{APP_SCHEMA}", public'))
        stats = sync_pr_submission_to_financial_data_snowflake(session)
        session.commit()
        logger.info("[snowflake pr reapply] completed: %s", stats)
        return stats
    except Exception:
        logger.exception("[snowflake pr reapply] failed")
        session.rollback()
        return {}
    finally:
        session.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    stats = reapply_snowflake_pr_financial_mapping()
    print(stats)
    raise SystemExit(0 if stats else 1)
