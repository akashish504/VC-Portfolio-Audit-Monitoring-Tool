"""
Shared currency conversion for audit extraction and Snowflake MIS ingest.

Product rule: convert document currency to PortfolioCompany.currency when
available, otherwise fall back to INR.  USD and INR that already match the
target pass through unchanged.  All conversions use the historical XE rate at
the last day of the entity's fy_end (12:00 UTC).
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, time, timezone
from typing import Any, Optional

from src.services.extraction_currency_scale import scale_extracted_statement_amounts
from src.services.financial_audit_schema import apply_audit_financials_formulas, consolidate_financial_synonyms
from src.services.fx_service import FxService, FxConversionUnavailable, fetch_historical_rate

logger = logging.getLogger(__name__)

PASSTHROUGH_CURRENCIES = frozenset({"USD", "INR"})
TARGET_CURRENCY = "INR"


def normalize_currency_code(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip().upper()
        if len(s) == 3 and s.isalpha():
            return s
    return None


def needs_inr_conversion(currency: Optional[str]) -> bool:
    cur = normalize_currency_code(currency)
    return cur is not None and cur not in PASSTHROUGH_CURRENCIES


async def fetch_rate_to_inr(from_currency: str) -> tuple[float, str]:
    """Return (multiplier, fx_timestamp) to convert ``from_currency`` amounts into INR."""
    rates = await FxService.fetch_rates(from_currency)
    return FxService.conversion_rate(rates, TARGET_CURRENCY)


def fetch_rate_to_inr_sync(from_currency: str) -> tuple[float, str]:
    """Sync wrapper for scripts without a running event loop."""
    return asyncio.run(fetch_rate_to_inr(from_currency))


async def convert_metric_amounts_to_inr(
    amounts: dict[str, Any],
    *,
    from_currency: str,
    log_context: str = "",
) -> tuple[dict[str, Any], str, Optional[dict[str, Any]]]:
    """
    Scale numeric metric values to INR when ``from_currency`` is not USD/INR.

    Returns ``(amounts, currency, fx_meta_or_none)``.
    """
    cur = normalize_currency_code(from_currency)
    if not cur or not needs_inr_conversion(cur):
        return amounts, cur or (from_currency or ""), None

    try:
        rate, fx_ts = await fetch_rate_to_inr(cur)
    except Exception:
        logger.warning(
            "FX conversion failed for %s → INR%s; storing original currency",
            cur,
            f" ({log_context})" if log_context else "",
            exc_info=True,
        )
        return amounts, cur, None

    converted = {
        k: round(float(v) * rate, 4)
        for k, v in amounts.items()
        if v is not None
    }
    meta = {
        "from": cur,
        "to": TARGET_CURRENCY,
        "rate": rate,
        "fx_timestamp": fx_ts,
        "auto": True,
    }
    logger.info(
        "Converted metric amounts %s → INR (rate=%.6f ts=%s)%s",
        cur,
        rate,
        fx_ts,
        f" {log_context}" if log_context else "",
    )
    return converted, TARGET_CURRENCY, meta


def convert_metric_amounts_to_inr_sync(
    amounts: dict[str, Any],
    *,
    from_currency: str,
    log_context: str = "",
) -> tuple[dict[str, Any], str, Optional[dict[str, Any]]]:
    """Sync variant for PR submission ingest scripts."""
    cur = normalize_currency_code(from_currency)
    if not cur or not needs_inr_conversion(cur):
        return amounts, cur or (from_currency or ""), None

    try:
        rate, fx_ts = fetch_rate_to_inr_sync(cur)
    except Exception:
        logger.warning(
            "FX conversion failed for %s → INR%s; storing original currency",
            cur,
            f" ({log_context})" if log_context else "",
            exc_info=True,
        )
        return amounts, cur, None

    converted = {
        k: round(float(v) * rate, 4)
        for k, v in amounts.items()
        if v is not None
    }
    meta = {
        "from": cur,
        "to": TARGET_CURRENCY,
        "rate": rate,
        "fx_timestamp": fx_ts,
        "auto": True,
    }
    logger.info(
        "Converted metric amounts %s → INR (rate=%.6f ts=%s)%s",
        cur,
        rate,
        fx_ts,
        f" {log_context}" if log_context else "",
    )
    return converted, TARGET_CURRENCY, meta


async def maybe_auto_convert_extracted_tree_to_inr(
    extracted: dict[str, Any],
    *,
    currency: Optional[str],
    log_context: str = "",
) -> tuple[dict[str, Any], Optional[str], Optional[dict[str, Any]]]:
    """
    Scale P&L / BS / CFS numeric leaves in ``extracted`` to INR when needed.

    Returns ``(extracted, currency, fx_meta_or_none)``.
    """
    cur = normalize_currency_code(currency)
    if not isinstance(extracted, dict) or not needs_inr_conversion(cur):
        return extracted, cur, None

    try:
        rate, fx_ts = await fetch_rate_to_inr(cur)  # type: ignore[arg-type]
    except Exception:
        logger.warning(
            "FX conversion failed for extracted tree %s → INR%s",
            cur,
            f" ({log_context})" if log_context else "",
            exc_info=True,
        )
        return extracted, cur, None

    working = consolidate_financial_synonyms(copy.deepcopy(extracted))
    scaled, scaled_count = scale_extracted_statement_amounts(working, rate)
    if scaled_count < 1:
        logger.info(
            "Extracted tree %s → INR skipped: no numeric leaves%s",
            cur,
            f" ({log_context})" if log_context else "",
        )
        return extracted, cur, None

    final = apply_audit_financials_formulas(scaled)
    meta = {
        "from": cur,
        "to": TARGET_CURRENCY,
        "rate": rate,
        "fx_timestamp": fx_ts,
        "scaled_numeric_leaves": scaled_count,
        "auto": True,
    }
    logger.info(
        "Auto-converted extracted tree %s → INR (rate=%.6f ts=%s leaves=%s)%s",
        cur,
        rate,
        fx_ts,
        scaled_count,
        f" {log_context}" if log_context else "",
    )
    return final, TARGET_CURRENCY, meta


async def auto_convert_extracted_tree(
    db: Any,
    extracted: dict[str, Any],
    *,
    from_currency: str,
    target_currency: str,
    fy_end_date: Any,  # datetime.date — last day of entity fy_end
    log_context: str = "",
) -> tuple[dict[str, Any], Optional[str], Optional[dict[str, Any]]]:
    """Convert P&L / BS / CFS numeric leaves from ``from_currency`` to ``target_currency``
    using the historical XE rate at ``fy_end_date`` (12:00 UTC).

    Returns ``(extracted, new_currency, fx_meta_or_none)``.
    - No-ops when currencies already match.
    - Falls back to original tree (with a warning) if the rate cannot be fetched.
    """
    src = normalize_currency_code(from_currency)
    tgt = normalize_currency_code(target_currency)
    if not src or not tgt or src == tgt:
        return extracted, src or from_currency, None

    if not isinstance(extracted, dict):
        return extracted, src, None

    at_dt = datetime.combine(fy_end_date, time(12, 0, 0), tzinfo=timezone.utc)
    try:
        rate, fx_ts = await fetch_historical_rate(db, from_currency=src, to_currency=tgt, at=at_dt)
    except FxConversionUnavailable:
        logger.warning(
            "Historical FX rate unavailable %s → %s at %s%s; storing original currency",
            src,
            tgt,
            fy_end_date.isoformat(),
            f" ({log_context})" if log_context else "",
        )
        return extracted, src, None
    except Exception:
        logger.warning(
            "FX rate fetch failed %s → %s at %s%s; storing original currency",
            src,
            tgt,
            fy_end_date.isoformat(),
            f" ({log_context})" if log_context else "",
            exc_info=True,
        )
        return extracted, src, None

    working = consolidate_financial_synonyms(copy.deepcopy(extracted))
    scaled, scaled_count = scale_extracted_statement_amounts(working, rate)
    if scaled_count < 1:
        logger.info(
            "auto_convert_extracted_tree %s → %s: no numeric leaves%s",
            src,
            tgt,
            f" ({log_context})" if log_context else "",
        )
        return extracted, src, None

    final = apply_audit_financials_formulas(scaled)
    meta = {
        "from": src,
        "to": tgt,
        "rate": rate,
        "fx_timestamp": fx_ts,
        "fy_end_date": fy_end_date.isoformat(),
        "scaled_numeric_leaves": scaled_count,
        "auto": True,
    }
    logger.info(
        "auto_convert_extracted_tree %s → %s (rate=%.6f ts=%s leaves=%s)%s",
        src,
        tgt,
        rate,
        fx_ts,
        scaled_count,
        f" {log_context}" if log_context else "",
    )
    return final, tgt, meta
