"""
Global config: map audit-financials extraction paths → ``FinancialData`` scalar columns.

Stored in ``ConfigTable`` under :data:`FINANCIAL_METRIC_MAPPING_CONFIG_KEY`. Each metric is an ordered
list of ``{path, sign}`` where ``sign`` is ``+`` or ``-``. Values use the same subtree summation helper
as the legacy sync code (handles extra nesting).

Validation uses the merged audit-financials schema (:func:`src.services.financial_audit_schema.load_audit_financials_schema`).
"""
from __future__ import annotations

import copy
import json
from typing import Any, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConfigTable
from src.services.financial_audit_schema import (
    sum_numeric_leaves_in_audit_subtree,
    validate_audit_financials_numeric_leaf_path_strict,
)

FINANCIAL_METRIC_MAPPING_CONFIG_KEY = "financial_extraction_metric_mapping_v1"

FINANCIAL_METRIC_COLUMNS: tuple[str, ...] = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")

AUDIT_METRIC_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "pbt": "PBT",
    "pat": "PAT",
    "cash": "Cash",
    "debt": "Debt",
}

SignLiteral = Literal["+", "-"]

TermTuple = tuple[str, SignLiteral, bool]  # (path, sign, use_abs)

# Defaults mirror pre-config sync heuristics while matching scalar slots present in AUDIT_FINANCIALS_SCHEMA_FALLBACK.
DEFAULT_METRIC_MAPPING: dict[str, list[dict[str, str]]] = {
    "revenue": [
        {"path": "profit_and_loss.revenue.revenue_from_operations", "sign": "+"},
    ],
    "ebitda": [
        {"path": "profit_and_loss.ebitda", "sign": "+"},
    ],
    "pbt": [
        {"path": "profit_and_loss.profit_loss_before_tax", "sign": "+"},
    ],
    "pat": [
        {"path": "profit_and_loss.profit_loss_for_the_period", "sign": "+"},
    ],
    "cash": [
        {"path": "balance_sheet.assets.current_assets.financial_assets.cash_and_cash_equivalents", "sign": "+"},
        {
            "path": "balance_sheet.assets.current_assets.financial_assets.bank_balances_other_than_cash_and_cash_equivalents",
            "sign": "+",
        },
    ],
    "debt": [
        {
            "path": "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings",
            "sign": "+",
        },
        {
            "path": "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings",
            "sign": "+",
        },
        {
            "path": "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.lease_liabilities",
            "sign": "+",
        },
        {
            "path": "balance_sheet.liabilities.current_liabilities.financial_liabilities.lease_liabilities",
            "sign": "+",
        },
    ],
}


def _get_value_at_dotted_path(tree: dict[str, Any], path: str) -> Any:
    cur: Any = tree
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def normalize_sign(v: Any) -> SignLiteral | None:
    if v is True or v is False:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        if v == 1:
            return "+"
        if v == -1:
            return "-"
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "+" or s == "plus":
            return "+"
        if s == "-" or s == "minus":
            return "-"
    return None


def coerce_metric_terms(raw_terms: Any) -> list[TermTuple]:
    if not isinstance(raw_terms, list):
        return []
    out: list[TermTuple] = []
    for item in raw_terms:
        if not isinstance(item, dict):
            continue
        path_raw = item.get("path")
        path = path_raw.strip() if isinstance(path_raw, str) else ""
        sg = normalize_sign(item.get("sign"))
        if not path or not sg:
            continue
        use_abs = bool(item.get("abs", False))
        out.append((path, sg, use_abs))
    return out


def validated_mapping_from_payload(
    payload_metrics: dict[str, Any],
    *,
    schema: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Validate and return storable metric mapping (canonical six keys only). Raises ``ValueError``."""
    out: dict[str, list[dict[str, str]]] = {}

    for col in FINANCIAL_METRIC_COLUMNS:
        rows = coerce_metric_terms(payload_metrics.get(col))
        if len(rows) > 512:
            raise ValueError(f"too many terms for metric {col}")
        checked: list[dict[str, Any]] = []
        for path, sign, use_abs in rows:
            if path.count("..") > 0 or "\n" in path or "\r" in path:
                raise ValueError(f"invalid path for {col}: {path!r}")
            if len(path) > 512:
                raise ValueError(f"path too long for {col}")
            try:
                validate_audit_financials_numeric_leaf_path_strict(schema, path)
            except ValueError as exc:
                raise ValueError(f"{col}: path {path}: {exc.args[0] if exc.args else exc}") from exc
            checked.append({"path": path, "sign": sign, "abs": use_abs})

        out[col] = checked

    return out


def merge_with_defaults(stored: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    merged: dict[str, list[dict[str, str]]] = {}
    stored = stored if isinstance(stored, dict) else {}
    metrics_raw = stored.get("metrics") if isinstance(stored.get("metrics"), dict) else stored

    for col in FINANCIAL_METRIC_COLUMNS:
        raw_for_col = metrics_raw.get(col) if isinstance(metrics_raw, dict) else None

        # Explicit metric key in persisted config (possibly empty → skip sync from extraction).
        if isinstance(raw_for_col, list):
            rows = coerce_metric_terms(raw_for_col)
            merged[col] = [{"path": p, "sign": s, "abs": a} for p, s, a in rows]
            continue

        fb = DEFAULT_METRIC_MAPPING.get(col)
        merged[col] = copy.deepcopy(fb) if fb is not None else []

    return merged


async def load_financial_metric_mapping_config(db: AsyncSession) -> dict[str, list[dict[str, str]]]:
    """Effective mapping merged with baked-in defaults for missing metrics."""
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == FINANCIAL_METRIC_MAPPING_CONFIG_KEY)))
        .scalars()
        .first()
    )
    payload = dict(row.value) if row and isinstance(row.value, dict) else {}
    return merge_with_defaults(payload)


async def persist_financial_metric_mapping(
    db: AsyncSession,
    *,
    mapping: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """Upsert packed JSON value ``{metrics: {...}}``. Returns persisted value blob."""
    body: dict[str, Any] = {"metrics": mapping}
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == FINANCIAL_METRIC_MAPPING_CONFIG_KEY)))
        .scalars()
        .first()
    )
    if row is None:
        row = ConfigTable(
            key=FINANCIAL_METRIC_MAPPING_CONFIG_KEY,
            value=body,
            description="Financial metric extraction mapping (FinancialData revenue/ebitda/pbt/pat/cash/debt)",
        )
        db.add(row)
    else:
        row.value = body
    await db.flush()
    await db.refresh(row)
    return dict(row.value) if isinstance(row.value, dict) else body


def evaluate_metric_with_breakdown(
    extracted: dict[str, Any],
    terms: list[dict[str, str]],
) -> dict[str, Any]:
    """Evaluate ``terms`` against ``extracted`` and return total + per-term breakdown.

    Every configured term always appears in ``terms`` of the returned dict. When the
    path is missing or non-numeric in ``extracted`` the term contributes ``0`` and is
    tagged ``source = "default_zero"`` (also accumulated in ``missing_paths``); otherwise
    it contributes the signed subtree sum and is tagged ``source = "ocr"``.

    Returned shape::

        {
          "total": float,                         # always a number; 0.0 when every term defaults
          "has_any_ocr_value": bool,              # True if at least one term resolved from OCR
          "terms": [
              {"path": str, "sign": "+"|"-", "raw_value": float|None,
               "contribution": float, "source": "ocr"|"default_zero"},
              ...
          ],
          "missing_paths": [str, ...],
        }
    """
    mult = {"+": 1.0, "-": -1.0}
    out_terms: list[dict[str, Any]] = []
    missing: list[str] = []
    total = 0.0
    has_any_ocr = False
    for t in terms:
        path = str(t.get("path") or "").strip()
        sg = normalize_sign(t.get("sign"))
        use_abs = bool(t.get("abs", False))
        if not path or not sg:
            continue
        raw = _get_value_at_dotted_path(extracted, path)
        chunk = sum_numeric_leaves_in_audit_subtree(raw)
        if chunk is None:
            contribution = 0.0
            source = "default_zero"
            raw_value: Optional[float] = None
            missing.append(path)
        else:
            effective = abs(chunk) if use_abs else chunk
            contribution = effective * mult[sg]
            source = "ocr"
            raw_value = float(chunk)
            has_any_ocr = True
        total += contribution
        out_terms.append(
            {
                "path": path,
                "sign": sg,
                "abs": use_abs,
                "raw_value": raw_value,
                "contribution": float(contribution),
                "source": source,
            }
        )
    return {
        "total": float(total),
        "has_any_ocr_value": has_any_ocr,
        "terms": out_terms,
        "missing_paths": missing,
    }


def evaluate_metric_from_terms(extracted: dict[str, Any], terms: list[dict[str, str]]) -> Optional[float]:
    """Sum ``sign * subtree_sum(path)``. ``None`` if no numeric contribution from any term.

    Kept for backward compatibility with exports / email rendering that expect ``None``
    to indicate "no data" (the breakdown-aware sync path uses :func:`evaluate_metric_with_breakdown`
    directly to get zero-defaulted totals + per-term breakdown).
    """
    bd = evaluate_metric_with_breakdown(extracted, terms)
    if not bd.get("has_any_ocr_value"):
        return None
    return float(bd["total"])


def format_mapping_summary(metrics: dict[str, list[dict[str, str]]]) -> str:
    """Stable JSON for audit meta (truncate-friendly)."""
    try:
        s = json.dumps(metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        s = str(metrics)
    if len(s) > 16000:
        return s[:16000] + "…"
    return s


def _stable_terms_repr(terms: list[dict[str, Any]]) -> str:
    """Order-sensitive signature for equality (after normalizing sign/path/abs)."""
    coerced = [{"path": p, "sign": s, "abs": a} for p, s, a in coerce_metric_terms(terms)]
    return json.dumps(coerced, separators=(",", ":"), ensure_ascii=False)


def format_formula_for_audit(terms: list[dict[str, Any]]) -> str:
    """Human-readable sum of path terms for audit logs."""
    coerced = coerce_metric_terms(terms)
    if not coerced:
        return "(none)"
    parts: list[str] = []
    for path, sign, use_abs in coerced:
        sym = "+" if sign == "+" else "−"
        suffix = " [abs]" if use_abs else ""
        parts.append(f"{sym} {path}{suffix}")
    return " ".join(parts)


def financial_metric_mapping_audit_diff_lines(
    before: dict[str, list[dict[str, str]]],
    after: dict[str, list[dict[str, str]]],
) -> list[str]:
    """One line per metric whose formula changed (before → after)."""
    lines: list[str] = []
    for col in FINANCIAL_METRIC_COLUMNS:
        bterms = before.get(col) or []
        aterms = after.get(col) or []
        if _stable_terms_repr(bterms) == _stable_terms_repr(aterms):
            continue
        label = AUDIT_METRIC_LABELS.get(col, col)
        bf = format_formula_for_audit(bterms)
        af = format_formula_for_audit(aterms)
        lines.append(f"{label}: «{bf}» → «{af}»")
    return lines


def financial_metric_mapping_audit_user_summary(
    *,
    change_lines: list[str],
) -> str:
    """Multi-line text shown as ``meta.summary`` in settings audit views."""
    if not change_lines:
        return (
            "Financial extraction metric mapping saved.\n• No formulas changed "
            "(compared to the configuration prior to this save)."
        )
    body = "\n".join(f"• {line}" for line in change_lines)
    return f"Financial extraction metric mapping updated\n\n{body}"
