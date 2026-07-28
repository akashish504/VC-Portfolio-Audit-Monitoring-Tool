"""
Push canonical audit-financials extraction metrics into ``FinancialMetricReconciliation``
(tall rows: one per metric key per entity / review cycle).

Rules (product):
- Six rows per (portfolio_company, entity, review_cycle) after ensure + sync.
- ``review_cycle`` from :func:`resolve_financial_review_cycle`.
- MIS amounts are resolved live from ``FinancialDataSnowflake`` when reconciliation rows are read.
- Merge: only overwrite ``afs_amount`` when newly computed value is not ``None``; ``afs_currency`` set when non-empty ISO.
- Cash/debt: same conditional as before (requires balance-sheet numeric subtree).
- Currency alignment: after AFS amounts are written, if a FinancialDataSnowflake row exists for the
  same (company, review_cycle) the AFS currency is converted to match the MIS currency (INR takes
  precedence per product rule).  Scenario B (Snowflake arrives after audit file) is handled by
  :func:`backfill_afs_currency_for_company`, called from the PR-submission sync script.

Metric formulas: Settings ``financial_extraction_metric_mapping_v1``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Entity,
    File,
    FileOCRMetadata,
    FileUploadStatus,
    FinancialDataSnowflake,
    FinancialMetricReconciliation,
    PortfolioCompany,
)
from src.db.session import AsyncSessionLocal
from src.services.financial_audit_schema import (
    apply_audit_financials_formulas,
    collect_audit_financials_derived_components,
    consolidate_financial_synonyms,
    normalize_balance_sheet_top_level,
    sum_numeric_leaves_in_audit_subtree,
)
from src.services.financial_metric_mapping import (
    FINANCIAL_METRIC_COLUMNS,
    evaluate_metric_with_breakdown,
    load_financial_metric_mapping_config,
)
from src.services.fx_service import FxConversionUnavailable, FxService, fetch_historical_rate
from src.services.fy_end import normalize_fy_end, fy_end_last_day

logger = logging.getLogger(__name__)

_AUDIT_KINDS = frozenset({"audit_financials", "financials", "audit-financials"})
_MAX_CONVERSION_HISTORY = 20


def _to_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        if not s:
            return None
        if s.startswith("(") and s.endswith(")"):
            s = f"-{s[1:-1]}"
        try:
            x = float(s)
        except ValueError:
            return None
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    return None


def _balance_sheet_has_numeric_data(bs: Any) -> bool:
    if not isinstance(bs, dict):
        return False

    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            return any(walk(v) for v in node.values())
        if isinstance(node, bool):
            return False
        if _to_number(node) is not None:
            return True
        return False

    return walk(bs)


def _config_signature(mapping: dict[str, list[dict[str, str]]]) -> str:
    """Stable short hash of the effective mapping (order-sensitive per metric)."""
    try:
        payload = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        payload = str(mapping)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _compute_metric_updates(
    extracted: dict[str, Any],
    mapping: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, Optional[float]], dict[str, dict[str, Any]], bool]:
    """Evaluate every configured metric and return totals + breakdown per metric.

    Returns:
      - totals: metric -> total float (always present for any metric with configured
        terms, possibly ``0.0``; ``None`` only when cash/debt is suppressed by the
        balance-sheet guard).
      - breakdowns: metric -> breakdown dict (from evaluate_metric_with_breakdown,
        with ``derived_components`` attached on metrics that use a derived formula).
      - apply_cd: whether cash/debt should be applied (i.e. balance sheet has numeric data).
    """
    totals: dict[str, Optional[float]] = {}
    breakdowns: dict[str, dict[str, Any]] = {}

    bs = extracted.get("balance_sheet")
    apply_cd = _balance_sheet_has_numeric_data(bs)

    derived = collect_audit_financials_derived_components(extracted)

    for metric in FINANCIAL_METRIC_COLUMNS:
        terms = mapping.get(metric) or []
        if metric in ("cash", "debt") and not apply_cd:
            # Suppress cash/debt entirely when no balance-sheet data was extracted.
            continue
        if not terms:
            # Metric has no configured terms — skip (no value, no breakdown).
            continue
        bd = evaluate_metric_with_breakdown(extracted, terms)
        if metric in derived:
            # Keep the schema/UI shape: ``derived_components`` is a {key -> derived} map
            # (MappingBreakdown.derived_components: dict[str, MappingBreakdownDerived], and the
            # UI does Object.entries on it). ``derived[metric]`` is a single derived object, so
            # wrap it keyed by the metric — otherwise the breakdown fails validation on read and
            # the whole row silently falls back to "values not computed".
            bd["derived_components"] = {metric: derived[metric]}
        totals[metric] = float(bd["total"])
        breakdowns[metric] = bd

    return totals, breakdowns, apply_cd


def _prepare_extracted_for_metrics(extracted: Any) -> Optional[dict[str, Any]]:
    if not isinstance(extracted, dict):
        return None
    tree = copy.deepcopy(extracted)
    if isinstance(tree.get("balance_sheet"), dict):
        tree["balance_sheet"] = normalize_balance_sheet_top_level(tree["balance_sheet"])
    tree = consolidate_financial_synonyms(tree)
    return apply_audit_financials_formulas(tree)


async def resolve_financial_review_cycle(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    entity_id: int,
) -> Optional[str]:
    entity = (await db.execute(select(Entity).where(Entity.id == entity_id))).scalar_one_or_none()
    if entity is None:
        return None
    if entity.portfolio_company_id != portfolio_company_id:
        return None

    pc = await db.get(PortfolioCompany, portfolio_company_id)
    pc_rc = ""
    if pc is not None and pc.review_cycle_id is not None:
        pc_rc = str(pc.review_cycle_id).strip()
    if pc_rc:
        return pc_rc[:128]

    ent_rc = (entity.review_cycle or "").strip()
    return ent_rc[:128] if ent_rc else None


def _normalize_currency_optional(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip().upper()
        if len(s) == 3 and s.isalpha():
            return s
    return None


async def _ensure_reconciliation_stub(
    db: AsyncSession,
    *,
    pc_id: int,
    eid: int,
    review_cycle: str,
    metric_key: str,
    frequency_val: Optional[str] = None,
) -> Optional[FinancialMetricReconciliation]:
    row: Optional[FinancialMetricReconciliation] = (
        (
            await db.execute(
                select(FinancialMetricReconciliation).where(
                    FinancialMetricReconciliation.portfolio_company_id == pc_id,
                    FinancialMetricReconciliation.entity_id == eid,
                    FinancialMetricReconciliation.review_cycle == review_cycle,
                    FinancialMetricReconciliation.metric_key == metric_key,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        row = FinancialMetricReconciliation(
            portfolio_company_id=pc_id,
            entity_id=eid,
            review_cycle=review_cycle,
            metric_key=metric_key,
            category="financial",
            type=metric_key,
            frequency=frequency_val,
            extra_data={},
        )
        db.add(row)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            row = (
                (
                    await db.execute(
                        select(FinancialMetricReconciliation).where(
                            FinancialMetricReconciliation.portfolio_company_id == pc_id,
                            FinancialMetricReconciliation.entity_id == eid,
                            FinancialMetricReconciliation.review_cycle == review_cycle,
                            FinancialMetricReconciliation.metric_key == metric_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                logger.warning(
                    "financial reconciliation sync lost row after integrity conflict metric=%s",
                    metric_key,
                )
                return None

    row.review_cycle = review_cycle
    if frequency_val is not None:
        row.frequency = frequency_val
    return row


async def _convert_recon_rows_afs_currency(
    db: AsyncSession,
    rows: list[FinancialMetricReconciliation],
    from_currency: str,
    to_currency: str,
    log_context: str = "",
) -> None:
    """Convert afs_amount on each row from ``from_currency`` to ``to_currency``.

    Uses the historical FX rate at the last day of the row's entity fy_end (12:00 UTC).
    Rows whose entity has no fy_end are skipped with a warning. Records each conversion in
    ``extra_data["afs_currency_conversion_history"]`` (capped at 20 entries).
    No-ops if from_currency already matches to_currency.
    """
    src = _normalize_currency_optional(from_currency)
    tgt = _normalize_currency_optional(to_currency)
    if not src or not tgt or src == tgt:
        return

    rows_with_amount = [r for r in rows if r.afs_amount is not None]
    if not rows_with_amount:
        return

    from datetime import date, time, timezone as _tz

    # Cache entity fy_end resolution to avoid repeated DB hits.
    entity_fy_date_cache: dict[int, Optional[date]] = {}

    async def _get_entity_fy_date(entity_id: int) -> Optional[date]:
        if entity_id in entity_fy_date_cache:
            return entity_fy_date_cache[entity_id]
        ent = await db.get(Entity, entity_id)
        fy_end = normalize_fy_end(ent.fy_end) if ent else None
        fy_date: Optional[date] = None
        if fy_end:
            try:
                fy_date = fy_end_last_day(fy_end)
            except ValueError:
                fy_date = None
        entity_fy_date_cache[entity_id] = fy_date
        return fy_date

    ts_now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    converted = 0

    for row in rows_with_amount:
        fy_date = await _get_entity_fy_date(row.entity_id)
        if fy_date is None:
            logger.warning(
                "AFS currency conversion %s → %s skipped for entity_id=%s: no fy_end%s",
                src,
                tgt,
                row.entity_id,
                f" ({log_context})" if log_context else "",
            )
            continue

        at_dt = datetime.combine(fy_date, time(12, 0, 0), tzinfo=_tz.utc)
        try:
            rate, fx_ts = await fetch_historical_rate(db, from_currency=src, to_currency=tgt, at=at_dt)
        except FxConversionUnavailable:
            logger.warning(
                "AFS currency conversion %s → %s historical rate unavailable at %s for entity_id=%s%s; skipping row",
                src,
                tgt,
                fy_date.isoformat(),
                row.entity_id,
                f" ({log_context})" if log_context else "",
            )
            continue

        row.afs_amount = round(float(row.afs_amount) * rate, 4)
        row.afs_currency = tgt

        history_entry = {
            "from": src,
            "to": tgt,
            "rate": rate,
            "fx_timestamp": fx_ts,
            "applied_at": ts_now,
            "reason": "auto_mis_currency_match",
        }
        extra = dict(row.extra_data or {})
        history: list[dict] = list(extra.get("afs_currency_conversion_history") or [])
        history.append(history_entry)
        if len(history) > _MAX_CONVERSION_HISTORY:
            history = history[-_MAX_CONVERSION_HISTORY:]
        extra["afs_currency_conversion_history"] = history
        row.extra_data = extra

        db.add(row)
        converted += 1

    logger.info(
        "Auto-converted %s/%s AFS recon rows %s → %s%s",
        converted,
        len(rows_with_amount),
        src,
        tgt,
        f" {log_context}" if log_context else "",
    )


async def _fetch_snowflake_currency_for_company(
    db: AsyncSession,
    portfolio_company_id: int,
    review_cycle: str,
) -> Optional[str]:
    """Return the MIS currency for a (company, review_cycle) pair, or None if no row exists."""
    sf = (
        await db.execute(
            select(FinancialDataSnowflake).where(
                FinancialDataSnowflake.portfolio_company_id == portfolio_company_id,
                FinancialDataSnowflake.review_cycle == review_cycle,
            ).limit(1)
        )
    ).scalars().first()

    if sf is None:
        return None
    return _normalize_currency_optional(sf.currency)


async def auto_convert_afs_to_mis_currency(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
    log_context: str = "",
) -> None:
    """Scenario A: Snowflake data already exists when an audit file is synced.

    Fetches the MIS currency for this (company, review_cycle). If any reconciliation
    rows for the company have a different afs_currency, converts those AFS amounts to
    the Snowflake currency using a live FX rate. Snowflake currency is the authority.

    Called at the end of sync_financial_data_from_audit_extraction().
    """
    mis_currency = await _fetch_snowflake_currency_for_company(db, portfolio_company_id, review_cycle)
    if not mis_currency:
        # No Snowflake row yet — nothing to align against.
        return

    # Find all recon rows for this company/cycle where afs_currency differs from MIS currency.
    rows: list[FinancialMetricReconciliation] = (
        await db.execute(
            select(FinancialMetricReconciliation).where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.review_cycle == review_cycle,
                FinancialMetricReconciliation.afs_currency != mis_currency,
                FinancialMetricReconciliation.afs_currency.isnot(None),
            )
        )
    ).scalars().all()

    if not rows:
        return

    # Group by afs_currency in case multiple files with different currencies contributed.
    by_currency: dict[str, list[FinancialMetricReconciliation]] = {}
    for row in rows:
        cur = row.afs_currency or ""
        by_currency.setdefault(cur, []).append(row)

    for cur, cur_rows in by_currency.items():
        await _convert_recon_rows_afs_currency(
            db,
            cur_rows,
            from_currency=cur,
            to_currency=mis_currency,
            log_context=log_context or f"company={portfolio_company_id} cycle={review_cycle}",
        )


async def backfill_afs_currency_for_company(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    review_cycle: str,
    mis_currency: str,
) -> None:
    """Scenario B: Snowflake data arrives after audit files have already been synced.

    Called from the PR-submission sync script after a FinancialDataSnowflake row is
    upserted. Converts all AFS amounts for the company/cycle to match mis_currency
    (the Snowflake currency, which is authoritative) if there is any mismatch.
    """
    target = _normalize_currency_optional(mis_currency)
    if not target:
        return

    rows: list[FinancialMetricReconciliation] = (
        await db.execute(
            select(FinancialMetricReconciliation).where(
                FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                FinancialMetricReconciliation.review_cycle == review_cycle,
                FinancialMetricReconciliation.afs_amount.isnot(None),
                FinancialMetricReconciliation.afs_currency != target,
                FinancialMetricReconciliation.afs_currency.isnot(None),
            )
        )
    ).scalars().all()

    if not rows:
        return

    by_currency: dict[str, list[FinancialMetricReconciliation]] = {}
    for row in rows:
        cur = row.afs_currency or ""
        by_currency.setdefault(cur, []).append(row)

    for cur, cur_rows in by_currency.items():
        await _convert_recon_rows_afs_currency(
            db,
            cur_rows,
            from_currency=cur,
            to_currency=target,
            log_context=f"snowflake_backfill company={portfolio_company_id} cycle={review_cycle}",
        )

    from src.services.reconciliation_service import (
        reconcile_all_metrics_for_company_cycle,
    )

    await reconcile_all_metrics_for_company_cycle(
        db,
        portfolio_company_id=portfolio_company_id,
        review_cycle=review_cycle,
    )


async def resolve_primary_afs_file(
    db: AsyncSession,
    *,
    entity_id: int,
    review_cycle: Optional[str] = None,
) -> Optional[File]:
    """The authoritative audited-financials file for an entity (optionally scoped to a review
    cycle) — the one whose extraction drives reconciliation, breakdowns and query emails.

    Selection order:
      1. the file the user explicitly flagged ``is_reconciliation_source``, else
      2. the most-recently-updated file whose extraction has any numeric value, else
      3. the most-recent completed audited-financials file (even if empty).

    Returns ``None`` when the entity has no completed audited-financials file. Every consumer
    (the sync, the source-ref/preview endpoint, the on-read breakdown) routes through this one
    selector, so they all agree on which file is shown and used — which also removes the
    multi-file "last-writer-wins" race in the sync.
    """
    stmt = select(File).where(
        File.entity_id == entity_id,
        File.status != FileUploadStatus.DELETED,
    )
    if review_cycle:
        stmt = stmt.where(File.review_cycle_id == review_cycle)
    # "Latest" = most recently uploaded (created_at / id), not most recently *updated* — a
    # re-extraction bumps updated_at and would otherwise make an older file look newest.
    stmt = stmt.order_by(File.created_at.desc(), File.id.desc())

    afs: list[tuple[File, dict]] = []
    for f in (await db.execute(stmt)).scalars().all():
        ocr = f.ocr_metadata  # eager (lazy="selectin")
        oj = dict(ocr.ocr_json) if ocr and isinstance(ocr.ocr_json, dict) else {}
        if str(oj.get("kind") or "").strip().lower() not in _AUDIT_KINDS:
            continue
        if str(oj.get("status") or "").strip().lower() != "completed":
            continue
        afs.append((f, oj))
    if not afs:
        return None
    for f, _oj in afs:
        if f.is_reconciliation_source:
            return f
    for f, oj in afs:  # already newest-first
        if sum_numeric_leaves_in_audit_subtree(oj.get("extracted")) is not None:
            return f
    return afs[0][0]


async def sync_financial_data_from_audit_extraction(
    db: AsyncSession,
    *,
    file_row: File,
    extracted: Any,
    currency: Optional[str],
    is_audit_financials: bool,
) -> None:
    if not is_audit_financials:
        return

    # Always persist the breakdown on the file itself, regardless of whether it is the
    # primary AFS source.  This is the source of truth the reconciliation push uses.
    await persist_file_metric_breakdown(db, file_row=file_row, extracted=extracted, currency=currency)

    if file_row.entity_id is None:
        logger.debug("financial reconciliation sync skipped file_id=%s no entity_id", file_row.id)
        return

    review_cycle = await resolve_financial_review_cycle(
        db,
        portfolio_company_id=file_row.portfolio_company_id,
        entity_id=file_row.entity_id,
    )
    if not review_cycle:
        logger.info(
            "financial reconciliation sync skipped file_id=%s entity_id=%s portfolio_company_id=%s",
            file_row.id,
            file_row.entity_id,
            file_row.portfolio_company_id,
        )
        return

    # Only the authoritative AFS file for this (entity, cycle) writes the reconciliation rows.
    # When an entity has several AFS files, this stops every file from overwriting the same row
    # (the last-writer-wins flicker). If the resolver can't decide (no match), we fall through
    # and behave as before — so this never regresses single-file entities.
    primary = await resolve_primary_afs_file(
        db, entity_id=file_row.entity_id, review_cycle=review_cycle
    )
    if primary is not None and primary.id != file_row.id:
        logger.info(
            "financial reconciliation sync skipped file_id=%s: not the primary AFS source for "
            "entity_id=%s cycle=%s (primary file_id=%s)",
            file_row.id,
            file_row.entity_id,
            review_cycle,
            primary.id,
        )
        return

    tree = _prepare_extracted_for_metrics(extracted)
    if tree is None:
        return

    metric_mapping = await load_financial_metric_mapping_config(db)
    updates, breakdowns, apply_cash_debt = _compute_metric_updates(tree, metric_mapping)
    if not apply_cash_debt:
        updates.pop("cash", None)
        updates.pop("debt", None)
        breakdowns.pop("cash", None)
        breakdowns.pop("debt", None)

    pc_id = file_row.portfolio_company_id
    eid = file_row.entity_id

    frequency_val: Optional[str] = None
    if isinstance(extracted, dict):
        hz = extracted.get("reporting_horizon_label") or extracted.get("frequency")
        if isinstance(hz, str) and hz.strip():
            frequency_val = hz.strip()[:32]

    # The extracted tree has already been converted to the correct target currency
    # (PortfolioCompany.currency or INR) at the entity fy_end historical rate before
    # this function is called.  No further currency conversion is applied here.
    cur = _normalize_currency_optional(currency)

    cfg_sig = _config_signature(metric_mapping)
    computed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for metric in FINANCIAL_METRIC_COLUMNS:
        row = await _ensure_reconciliation_stub(
            db,
            pc_id=pc_id,
            eid=eid,
            review_cycle=review_cycle,
            metric_key=metric,
            frequency_val=frequency_val,
        )
        if row is None:
            continue

        if cur:
            row.afs_currency = cur

        if metric in updates:
            val = updates[metric]
            if val is not None:
                # Configured metric with at least one term: always overwrite (incl. 0.0).
                row.afs_amount = val

        # Persist or clear per-metric breakdown so the row always reflects the
        # current mapping configuration. Use a shallow merge on extra_data to
        # preserve currency-conversion history and other ancillary keys.
        bd = breakdowns.get(metric)
        extra = dict(row.extra_data or {})
        all_bd = dict(extra.get("mapping_breakdown") or {})
        if bd is not None:
            entry: dict[str, Any] = {
                "computed_at": computed_at,
                "config_signature": cfg_sig,
                "currency": cur,
                "total": float(bd.get("total") or 0.0),
                "terms": list(bd.get("terms") or []),
                "missing_paths": list(bd.get("missing_paths") or []),
            }
            if "derived_components" in bd:
                entry["derived_components"] = bd["derived_components"]
            all_bd[metric] = entry
        else:
            # No configured terms for this metric (or suppressed by cash/debt guard):
            # drop any stale breakdown so the read API never shows outdated data.
            all_bd.pop(metric, None)
        if all_bd:
            extra["mapping_breakdown"] = all_bd
        else:
            extra.pop("mapping_breakdown", None)
        row.extra_data = extra

        db.add(row)

    # Scenario A: if a Snowflake MIS row already exists for this company/cycle, align
    # any remaining currency mismatch now that AFS amounts have been written.
    await auto_convert_afs_to_mis_currency(
        db,
        portfolio_company_id=pc_id,
        review_cycle=review_cycle,
        log_context=f"file_id={file_row.id}",
    )

    # Recompute normalized values + comparison for every metric on this (entity, cycle).
    from src.services.reconciliation_service import reconcile_all_metrics_for_entity_cycle

    await reconcile_all_metrics_for_entity_cycle(
        db,
        portfolio_company_id=pc_id,
        entity_id=eid,
        review_cycle=review_cycle,
        frequency=frequency_val,
    )


async def sync_financial_data_for_file_id(db: AsyncSession, file_id: int) -> None:
    """Load file + OCR metadata and run :func:`sync_financial_data_from_audit_extraction`."""
    file_row = (await db.execute(select(File).where(File.id == file_id))).scalar_one_or_none()
    if not file_row:
        return
    meta = (
        (await db.execute(select(FileOCRMetadata).where(FileOCRMetadata.file_id == file_id)))
        .scalars()
        .first()
    )
    if meta is None or not isinstance(meta.ocr_json, dict):
        return
    oj = meta.ocr_json
    if str(oj.get("status") or "").lower() != "completed":
        return
    kind = str(oj.get("kind") or "").strip().lower()
    is_af = kind in _AUDIT_KINDS
    extracted = oj.get("extracted")
    currency = _normalize_currency_optional(oj.get("currency"))
    await sync_financial_data_from_audit_extraction(
        db,
        file_row=file_row,
        extracted=extracted,
        currency=currency,
        is_audit_financials=is_af,
    )


def build_mapping_breakdowns_for_read(
    extracted: Any,
    mapping: dict[str, list[dict[str, str]]],
    *,
    currency: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """Reproduce the per-metric ``mapping_breakdown`` entries the sync persists — for on-read
    backfill when a reconciliation row has no stored breakdown yet (e.g. its file was synced
    before the breakdown was written).

    Reuses the exact builders the write path uses (:func:`_prepare_extracted_for_metrics` +
    :func:`_compute_metric_updates`) and formats each entry identically to
    :func:`sync_financial_data_from_audit_extraction`, so a value shown on a not-yet-synced
    row matches a freshly-synced one. The breakdown is evaluated in the file's own currency
    (the sync stores it the same way — no conversion is applied at sync time). Pure /
    read-only — never writes to the DB.
    """
    tree = _prepare_extracted_for_metrics(extracted)
    if tree is None:
        return {}
    # _compute_metric_updates already skips cash/debt when the balance sheet has no numeric
    # data, so suppressed metrics are absent from ``breakdowns`` (matching the write path).
    _totals, breakdowns, _apply_cd = _compute_metric_updates(tree, mapping)
    cur = _normalize_currency_optional(currency)
    cfg_sig = _config_signature(mapping)
    out: dict[str, dict[str, Any]] = {}
    for metric, bd in breakdowns.items():
        entry: dict[str, Any] = {
            "computed_at": None,
            "config_signature": cfg_sig,
            "currency": cur,
            "total": float(bd.get("total") or 0.0),
            "terms": list(bd.get("terms") or []),
            "missing_paths": list(bd.get("missing_paths") or []),
        }
        if "derived_components" in bd:
            entry["derived_components"] = bd["derived_components"]
        out[metric] = entry
    return out


async def persist_file_metric_breakdown(
    db: AsyncSession,
    *,
    file_row: File,
    extracted: Any,
    currency: Optional[str],
) -> None:
    """Compute and store the metric breakdown on the file's own OCR metadata row.

    Called unconditionally on every OCR sync and manual edit — regardless of whether
    this file is the primary AFS source.  This means the breakdown on the file always
    reflects the latest extracted values and mapping config for that specific file.

    The stored shape mirrors what :func:`build_mapping_breakdowns_for_read` produces so
    the file-detail page and the push-to-reconciliation path see the same structure.
    """
    meta = file_row.ocr_metadata
    if meta is None:
        return
    metric_mapping = await load_financial_metric_mapping_config(db)
    breakdown = build_mapping_breakdowns_for_read(extracted, metric_mapping, currency=currency)
    meta.metric_breakdown = breakdown or None
    db.add(meta)


async def push_file_breakdown_to_reconciliation(
    db: AsyncSession,
    *,
    file_row: File,
) -> None:
    """Push the breakdown already stored on a file into FinancialMetricReconciliation.

    Called when a file is attached to an entity or promoted to primary AFS source.
    Reading from the pre-computed ``metric_breakdown`` avoids re-running the full OCR
    extraction pipeline — the file already knows its numbers.

    Falls back to a full re-sync via :func:`sync_financial_data_for_file_id` when the
    stored breakdown is absent (e.g. the file was synced before this column existed).
    """
    meta = file_row.ocr_metadata
    stored_breakdown: Optional[dict[str, Any]] = getattr(meta, "metric_breakdown", None) if meta else None

    if not stored_breakdown or not isinstance(stored_breakdown, dict):
        # No stored breakdown yet — fall back to full re-sync which recomputes everything.
        await sync_financial_data_for_file_id(db, file_row.id)
        return

    if file_row.entity_id is None:
        return

    review_cycle = await resolve_financial_review_cycle(
        db,
        portfolio_company_id=file_row.portfolio_company_id,
        entity_id=file_row.entity_id,
    )
    if not review_cycle:
        return

    pc_id = file_row.portfolio_company_id
    eid = file_row.entity_id

    meta_ocr = meta.ocr_json if meta and isinstance(meta.ocr_json, dict) else {}
    frequency_val: Optional[str] = None
    extracted_raw = meta_ocr.get("extracted")
    if isinstance(extracted_raw, dict):
        hz = extracted_raw.get("reporting_horizon_label") or extracted_raw.get("frequency")
        if isinstance(hz, str) and hz.strip():
            frequency_val = hz.strip()[:32]

    for metric in FINANCIAL_METRIC_COLUMNS:
        bd = stored_breakdown.get(metric)
        if bd is None:
            continue
        row = await _ensure_reconciliation_stub(
            db,
            pc_id=pc_id,
            eid=eid,
            review_cycle=review_cycle,
            metric_key=metric,
            frequency_val=frequency_val,
        )
        if row is None:
            continue

        cur = bd.get("currency")
        if cur:
            row.afs_currency = cur

        total = bd.get("total")
        if total is not None:
            row.afs_amount = float(total)

        extra = dict(row.extra_data or {})
        all_bd = dict(extra.get("mapping_breakdown") or {})
        all_bd[metric] = bd
        extra["mapping_breakdown"] = all_bd
        row.extra_data = extra
        db.add(row)

    from src.services.reconciliation_service import reconcile_all_metrics_for_entity_cycle

    await reconcile_all_metrics_for_entity_cycle(
        db,
        portfolio_company_id=pc_id,
        entity_id=eid,
        review_cycle=review_cycle,
        frequency=frequency_val,
    )


async def reapply_financial_metric_mapping_to_all_files(
    *,
    batch_size: int = 25,
) -> dict[str, int]:
    """Re-evaluate every completed audit-financials file against the current mapping config.

    Designed to run as a background task after the Financial Extraction Mapping is saved.
    Opens its own ``AsyncSessionLocal()`` so it never holds the originating PUT request's
    session. Pages file ids in small batches and commits per file so a long iteration
    cannot starve the connection pool or hold locks on ``config_table`` /
    ``financial_metric_reconciliation``.

    Returns a small counter dict for logging: ``{"affected", "failed", "scanned"}``.
    """
    started_at = datetime.now(timezone.utc)
    logger.info(
        "[financial_metric_mapping reapply] START batch_size=%s audit_kinds=%s",
        batch_size,
        sorted(_AUDIT_KINDS),
    )
    scanned = 0
    affected = 0
    failed = 0
    last_id = 0
    page_index = 0

    while True:
        page_index += 1
        async with AsyncSessionLocal() as page_session:
            try:
                stmt = (
                    select(FileOCRMetadata.file_id)
                    .where(
                        FileOCRMetadata.file_id > last_id,
                        FileOCRMetadata.ocr_json["status"].as_string() == "completed",
                        FileOCRMetadata.ocr_json["kind"].as_string().in_(list(_AUDIT_KINDS)),
                    )
                    .order_by(FileOCRMetadata.file_id.asc())
                    .limit(batch_size)
                )
                rows = (await page_session.execute(stmt)).scalars().all()
            except Exception:
                logger.exception(
                    "[financial_metric_mapping reapply] failed to page file ids; aborting page=%s last_id=%s",
                    page_index,
                    last_id,
                )
                break
        logger.info(
            "[financial_metric_mapping reapply] page=%s last_id=%s found=%s",
            page_index,
            last_id,
            len(rows),
        )
        if not rows:
            break

        for file_id in rows:
            scanned += 1
            last_id = max(last_id, int(file_id))
            try:
                async with AsyncSessionLocal() as file_session:
                    async with file_session.begin():
                        await sync_financial_data_for_file_id(file_session, int(file_id))
                affected += 1
                logger.info(
                    "[financial_metric_mapping reapply] resynced file_id=%s (affected=%s)",
                    file_id,
                    affected,
                )
            except Exception:
                failed += 1
                logger.warning(
                    "[financial_metric_mapping reapply] sync failed file_id=%s",
                    file_id,
                    exc_info=True,
                )

    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    logger.info(
        "[financial_metric_mapping reapply] DONE scanned=%s affected=%s failed=%s elapsed_ms=%s",
        scanned,
        affected,
        failed,
        elapsed_ms,
    )
    return {"scanned": scanned, "affected": affected, "failed": failed}
