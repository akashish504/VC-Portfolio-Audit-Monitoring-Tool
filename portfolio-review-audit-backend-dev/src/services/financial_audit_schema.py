"""
Canonical audit financials schema loading and path helpers for HITL review (map unmatched lines).
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConfigTable
from src.llm.prompts import AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL, AUDIT_FINANCIALS_SCHEMA_FALLBACK

AUDIT_FINANCIALS_SCHEMA_CONFIG_KEY = "audit_financials_schema_v1"

# Canonical balance sheet: only these keys at mapped.balance_sheet root.
BALANCE_SHEET_TOP_LEVEL_KEYS = ("assets", "equity", "liabilities")


def _deep_merge_schema_defaults(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` onto ``base`` while preserving ``base`` (fallback) key ORDER.

    Canonical keys come first, in ``base`` order (recursing into dict values); any
    extra keys the overlay adds that are not in ``base`` are appended afterwards in
    their original order. Value resolution is unchanged — overlay values win for
    scalars, dict+dict nodes recurse, and ``base`` fills any gaps; only the key
    order differs from a plain overlay merge.

    Base-first ordering is intentional: the served/UI order must ALWAYS follow
    ``AUDIT_FINANCIALS_SCHEMA_FALLBACK`` regardless of whether a DB overlay
    (``audit_financials_schema_v1``) exists or how its keys are ordered.
    """
    result: dict[str, Any] = {}
    # 1) base keys first, in fallback order.
    for k, v in base.items():
        if k not in overlay:
            result[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(overlay[k], dict):
            result[k] = _deep_merge_schema_defaults(v, overlay[k])
        else:
            result[k] = copy.deepcopy(overlay[k])
    # 2) overlay-only keys (schema extensions) appended afterwards, in overlay order.
    for k, ov in overlay.items():
        if k not in result:
            result[k] = copy.deepcopy(ov)
    return result


async def load_audit_financials_schema(db: AsyncSession) -> dict[str, Any]:
    base = copy.deepcopy(AUDIT_FINANCIALS_SCHEMA_FALLBACK)
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == AUDIT_FINANCIALS_SCHEMA_CONFIG_KEY)))
        .scalars()
        .first()
    )
    if not row or not isinstance(row.value, dict):
        return base
    overlay = dict(row.value or {})
    merged = _deep_merge_schema_defaults(base, overlay)
    for k in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        if k not in merged:
            merged[k] = copy.deepcopy(base.get(k) or {})
        elif isinstance(base.get(k), dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_schema_defaults(base[k], merged[k])
    return merged


def slug_snake_label(label: str) -> str:
    """Default child key from a document line label."""
    t = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return t or "line_item"


def list_assignable_parent_paths(schema: dict[str, Any], prefix: str = "") -> list[str]:
    """
    Paths to dict nodes where a new child key can be placed (containers).
    Includes every nested dict path under the three statements.
    Skips ``None`` scalar slots in the schema.
    """
    out: list[str] = []
    for k, v in schema.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.append(p)
            if v:
                out.extend(list_assignable_parent_paths(v, p))
    return out


def _infer_balance_sheet_section(key: str) -> str:
    """
    Route a stray line-item key to assets / equity / liabilities for top-level cleanup.
    Heuristic only — prefers nesting under sections over a fourth BS root key.
    """
    k = (key or "").lower()
    if not k:
        return "assets"
    # Totals that span both sides → liabilities subtree in our schema
    if "total_equity_and_liabilities" in k:
        return "liabilities"
    # Equity first (avoid "share" matching something else)
    if any(x in k for x in ("equity", "share_capital", "other_equity", "reserve", "retained", "oci", "minority")):
        return "equity"
    # Liabilities
    if any(
        x in k
        for x in (
            "liabilit",
            "borrow",
            "payable",
            "debt",
            "provision",
            "deferred_tax_liabilit",
            "lease_liability",
            "trade_payable",
        )
    ):
        if "asset" in k and "liabilit" not in k:
            pass
        else:
            return "liabilities"
    if "lease" in k and ("liabilit" in k or "payable" in k):
        return "liabilities"
    # Assets (default for cash, receivables, PPE, etc.)
    if any(
        x in k
        for x in (
            "asset",
            "cash",
            "bank",
            "receivable",
            "inventory",
            "ppe",
            "goodwill",
            "intangible",
            "investment",
            "property",
            "plant",
            "biological",
        )
    ):
        return "assets"
    if "cash" in k or "bank" in k:
        return "assets"
    if "loan" in k and "receivable" not in k and "received" not in k:
        return "liabilities"
    return "assets"


def _ensure_other_bucket(section: dict[str, Any]) -> dict[str, Any]:
    o = section.get("other")
    if not isinstance(o, dict):
        section["other"] = {}
    return section["other"]


def _put_under_other_unique(other: dict[str, Any], key: str, value: Any) -> None:
    """Assign into ``other`` without overwriting: suffix ``_2``, ``_3``, … if needed."""
    nk = key
    i = 2
    while nk in other:
        nk = f"{key}_{i}"
        i += 1
    other[nk] = value


def normalize_balance_sheet_top_level(bs: Any) -> Any:
    """
    Enforce exactly three top-level keys under balance_sheet: assets, equity, liabilities.

    Any other top-level keys (e.g. cash, debt, other, borrowings) are merged into the
    nested ``other`` bucket of the inferred section so the UI shows only three roots.
    """
    if not isinstance(bs, dict):
        return bs
    allowed = frozenset(BALANCE_SHEET_TOP_LEVEL_KEYS)
    out_bs = copy.deepcopy(bs)

    strays = {k: v for k, v in out_bs.items() if k not in allowed}

    result: dict[str, Any] = {}
    for name in BALANCE_SHEET_TOP_LEVEL_KEYS:
        v = out_bs.get(name)
        if isinstance(v, dict):
            result[name] = v
        elif v is None:
            result[name] = {}
        else:
            result[name] = {}
            _put_under_other_unique(
                _ensure_other_bucket(result[name]),
                "_legacy_top_level_scalar",
                v,
            )

    def merge_stray(key: str, value: Any) -> None:
        if key == "other" and isinstance(value, dict):
            for ck, cv in value.items():
                merge_stray(ck, cv)
            return
        target = _infer_balance_sheet_section(key)
        section = result[target]
        other = _ensure_other_bucket(section)
        _put_under_other_unique(other, key, value)

    for sk, sv in strays.items():
        merge_stray(sk, sv)

    return result


def _delete_dotted_path(tree: dict[str, Any], path: str) -> None:
    """Remove ``path`` from ``tree`` if present; no-op when missing."""
    parts = [p for p in (path or "").split(".") if p]
    if not parts:
        return
    cur: Any = tree
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


# Sibling groups (first key is canonical).
_PROFIT_AND_LOSS_ROOT_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("profit_loss_before_tax", "profit_before_tax", "pbt"),
    (
        "profit_loss_for_the_period",
        "profit_after_tax",
        "pat",
        "profit_for_the_period",
        "net_profit",
        "profit_loss_for_the_year",
    ),
    (
        "profit_loss_before_exceptional_items_or_tax",
        "profit_before_exceptional_items_and_tax",
        "profit_before_exceptional_items_or_tax",
    ),
    ("total_comprehensive_income_for_the_period", "total_comprehensive_income", "tci"),
)

_REVENUE_BUCKET_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("revenue_from_operations", "operating_revenue", "sales", "turnover", "revenues"),
    ("other_income", "other_revenue"),
)

_EXPENSES_BUCKET_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("finance_costs", "finance_cost", "interest_expense", "borrowing_costs"),
    (
        "depreciation_and_amortization_expense",
        "depreciation_and_amortisation_expense",
        "depreciation_and_amortization",
        "depreciation_and_amortisation",
    ),
    ("cost_of_material_consumed", "cost_of_materials_consumed"),
    ("employee_benefit_expense", "employee_benefits_expense", "staff_costs"),
)

_TAX_EXPENSE_BUCKET_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("total_tax_expense", "tax_expense_total", "total_tax"),
    ("current_tax", "current_tax_expense"),
    ("deferred_tax", "deferred_tax_expense"),
)

_FINANCIAL_PATH_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("profit_and_loss.expenses.finance_costs", "profit_and_loss.finance_costs", "profit_and_loss.finance_cost"),
    (
        "profit_and_loss.expenses.depreciation_and_amortization_expense",
        "profit_and_loss.depreciation_and_amortization_expense",
        "profit_and_loss.depreciation_and_amortisation_expense",
    ),
    ("profit_and_loss.expenses.total_expenses", "profit_and_loss.total_expenses"),
    ("profit_and_loss.revenue.total_income", "profit_and_loss.total_income"),
    # The statement-level key is canonical; LLMs sometimes also emit the same cash
    # reconciliation line *nested* inside operating activities, producing a duplicate
    # UI row. List the nested path as a synonym so it is folded into the top-level key
    # and the duplicate is dropped. (No metric impact: cash/debt map only to balance_sheet.)
    (
        "cash_flow_statement.net_increase_decrease_in_cash_and_cash_equivalents",
        "cash_flow_statement.net_change_in_cash_and_cash_equivalents",
        "cash_flow_statement.net_cash_flow",
        "cash_flow_statement.cash_flows_from_operating_activities.net_increase_decrease_in_cash_and_cash_equivalents",
    ),
    (
        "cash_flow_statement.cash_and_cash_equivalents_at_end_of_period",
        "cash_flow_statement.cash_at_end_of_period",
        "cash_flow_statement.closing_cash_and_cash_equivalents",
        "cash_flow_statement.cash_flows_from_operating_activities.cash_and_cash_equivalents_at_end_of_period",
    ),
    (
        "cash_flow_statement.cash_and_cash_equivalents_at_beginning_of_period",
        "cash_flow_statement.cash_at_beginning_of_period",
        "cash_flow_statement.opening_cash_and_cash_equivalents",
        "cash_flow_statement.cash_flows_from_operating_activities.cash_and_cash_equivalents_at_beginning_of_period",
    ),
)


def _leaf_numeric_or_none(node: Any) -> Optional[float]:
    if node is None:
        return None
    return sum_numeric_leaves_in_audit_subtree(node)


def _consolidate_sibling_synonyms(parent: dict[str, Any], keys: tuple[str, ...]) -> None:
    """Keep one canonical sibling key; drop alias keys that duplicate the same metric."""
    if not isinstance(parent, dict) or len(keys) < 2:
        return
    canonical = keys[0]
    chosen: Optional[float] = None
    for key in keys:
        if key not in parent:
            continue
        n = _leaf_numeric_or_none(parent[key])
        if n is not None:
            chosen = n
            break
    for key in keys[1:]:
        parent.pop(key, None)
    if chosen is not None:
        parent[canonical] = chosen


def _consolidate_path_synonyms(tree: dict[str, Any], paths: tuple[str, ...]) -> None:
    if len(paths) < 2:
        return
    canonical = paths[0]
    chosen: Optional[float] = None
    for path in paths:
        n = _leaf_numeric_or_none(_get_value_at_dotted_path(tree, path))
        if n is not None:
            chosen = n
            break
    for path in paths[1:]:
        _delete_dotted_path(tree, path)
    if chosen is not None:
        set_value_at_dotted_path(tree, canonical, chosen)


def consolidate_financial_synonyms(extracted: dict[str, Any]) -> dict[str, Any]:
    """
    Collapse short-form / legacy alias keys into one canonical field per metric.

    Prevents duplicate UI rows such as ``pbt`` and ``profit_before_tax`` under P&L.
    """
    if not isinstance(extracted, dict):
        return extracted
    out = copy.deepcopy(extracted)

    pl = out.get("profit_and_loss")
    if isinstance(pl, dict):
        for group in _PROFIT_AND_LOSS_ROOT_SYNONYMS:
            _consolidate_sibling_synonyms(pl, group)
        revenue = pl.get("revenue")
        if isinstance(revenue, dict):
            for group in _REVENUE_BUCKET_SYNONYMS:
                _consolidate_sibling_synonyms(revenue, group)
        expenses = pl.get("expenses")
        if isinstance(expenses, dict):
            for group in _EXPENSES_BUCKET_SYNONYMS:
                _consolidate_sibling_synonyms(expenses, group)
        tax_expense = pl.get("tax_expense")
        if isinstance(tax_expense, dict):
            for group in _TAX_EXPENSE_BUCKET_SYNONYMS:
                _consolidate_sibling_synonyms(tax_expense, group)
        if _leaf_numeric_or_none(pl.get("tax")) is not None:
            pl.pop("tax", None)

    for group in _FINANCIAL_PATH_SYNONYMS:
        _consolidate_path_synonyms(out, group)

    if isinstance(out.get("balance_sheet"), dict):
        out["balance_sheet"] = normalize_balance_sheet_top_level(out["balance_sheet"])
    return out


def apply_schema_defaults_to_extracted(tree: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``tree`` with all schema scalar slots and bucket keys present for the three statements."""

    out = dict(tree)

    def merge_section(node: dict[str, Any], sch: dict[str, Any]) -> dict[str, Any]:
        result = dict(node)
        for k, sch_v in sch.items():
            if sch_v is None:
                cur = result.get(k)
                if k not in result or cur is None:
                    result[k] = None
                elif isinstance(cur, dict) and len(cur) == 0:
                    result[k] = None
            elif isinstance(sch_v, dict):
                cur = result.get(k)
                # An open bucket (empty schema dict {}) with a scalar value already placed
                # there (e.g. user mapped a number directly into right_of_use_assets: {})
                # must be preserved — don't reset it to {}.
                if not isinstance(cur, dict) and not sch_v:
                    pass  # keep the scalar cur as-is
                else:
                    if not isinstance(cur, dict):
                        cur = {}
                    result[k] = merge_section(cur, sch_v)
        return result

    for section in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        sch_sec = schema.get(section)
        if not isinstance(sch_sec, dict):
            continue
        sec_tree = out.get(section)
        if not isinstance(sec_tree, dict):
            sec_tree = {}
        out[section] = merge_section(sec_tree, sch_sec)

    if isinstance(out.get("balance_sheet"), dict):
        out["balance_sheet"] = normalize_balance_sheet_top_level(out["balance_sheet"])
    return out


def list_audit_financials_numeric_leaf_paths(schema: dict[str, Any]) -> list[str]:
    """
    Canonical dotted paths usable as metric formula terms.

    Includes:
    - Scalar numeric leaves (schema slot == ``None``)
    - Open bucket dicts (schema slot == ``{}``) — runtime values are summed via
      ``sum_numeric_leaves_in_audit_subtree``, so selecting the bucket path aggregates
      all children (e.g. ``*.other`` collects every overflow field placed there).
    """
    out: list[str] = []

    def walk(parts: list[str], node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key, sch_v in node.items():
            next_parts = parts + [key]
            dotted = ".".join(next_parts)
            if sch_v is None:
                # Scalar leaf — directly numeric.
                out.append(dotted)
            elif isinstance(sch_v, dict):
                if not sch_v:
                    # Open bucket ({}): selectable as an aggregate term.
                    out.append(dotted)
                else:
                    walk(next_parts, sch_v)

    for section in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        root = schema.get(section)
        if isinstance(root, dict):
            walk([section], root)
    out.sort()
    return out


def validate_audit_financials_numeric_leaf_path_strict(schema: dict[str, Any], path: str) -> None:
    """Raise ``ValueError`` unless ``path`` resolves to a valid formula term in ``schema``.

    Valid targets are:
    - A scalar numeric leaf (slot == ``None``)
    - An open bucket dict (slot == ``{}``) — runtime values are summed via
      ``sum_numeric_leaves_in_audit_subtree``, so the bucket acts as a compound aggregate.
    """
    path = (path or "").strip()
    parts = [p for p in path.split(".") if p]
    if len(parts) < 2:
        raise ValueError("path must include a statement section and a field")
    section = parts[0]
    if section not in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        raise ValueError("unknown statement section")

    sch: Any = schema.get(section)
    if not isinstance(sch, dict):
        raise ValueError("invalid schema")

    cur: Any = sch
    for pk in parts[1:-1]:
        if not isinstance(cur, dict):
            raise ValueError("invalid path")
        if pk not in cur:
            raise ValueError("unknown path segment")
        nxt = cur[pk]
        if nxt is None:
            raise ValueError("path crosses a scalar slot")
        if not isinstance(nxt, dict):
            raise ValueError("invalid path segment type")
        cur = nxt

    leaf = parts[-1]
    if not isinstance(cur, dict) or leaf not in cur:
        raise ValueError("unknown leaf key")
    slot = cur[leaf]
    if slot is None:
        return  # scalar numeric leaf — valid
    if isinstance(slot, dict) and not slot:
        return  # open bucket ({}) — valid aggregate target
    raise ValueError("path must point to a numeric leaf slot or an open bucket, not a non-empty container")


def validate_extracted_value_path(
    schema: dict[str, Any], path: str, extracted: Optional[dict[str, Any]] = None
) -> None:
    """Raise ``ValueError`` if ``path`` is not a valid leaf path for a numeric patch under ``extracted``.

    When ``extracted`` (the file's live tree) is supplied it takes precedence over the
    schema for the final leaf/container decision: a global-schema overlay may mark a
    field as a bucket while a given file extracted it as a plain scalar leaf. A
    value/null patch is only rejected when the *actual* node at ``path`` is a populated
    container — overwriting that would silently drop its breakdown.
    """
    path = (path or "").strip()
    if not path or ".." in path:
        raise ValueError("invalid path")
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError("path must include a statement and a field")
    section = parts[0]
    if section not in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        raise ValueError("unknown statement section")
    sch_root = schema.get(section)
    if not isinstance(sch_root, dict):
        raise ValueError("invalid schema")

    parent_keys = parts[1:-1]
    leaf = parts[-1]
    sch: Any = sch_root
    for pk in parent_keys:
        if not isinstance(sch, dict):
            raise ValueError("invalid path")
        if pk not in sch:
            sch = {}
            continue
        nxt = sch[pk]
        if nxt is None:
            # Schema marks this as a scalar slot, but the caller may be placing a leaf under
            # a field that was explicitly converted to a composite in the extracted tree.
            # Treat it as an open bucket so the patch can proceed.
            sch = {}
            continue
        if not isinstance(nxt, dict):
            raise ValueError("invalid path")
        sch = nxt

    # Prefer the file's ACTUAL extracted node when available: the schema can mark a
    # field as a bucket while this file holds a scalar there (or vice-versa). Only a
    # populated container is a real "set a child key instead" case.
    if extracted is not None:
        node = _get_value_at_dotted_path(extracted, path)
        if isinstance(node, dict) and node:
            raise ValueError("path points to a container; set a child key (e.g. tax.current_tax) instead")
        return

    if not isinstance(sch, dict):
        raise ValueError("invalid path")
    if leaf not in sch:
        return
    slot = sch[leaf]
    if isinstance(slot, dict):
        raise ValueError("path points to a container; set a child key (e.g. tax.current_tax) instead")
    return


def set_value_at_dotted_path(tree: dict[str, Any], path: str, value: Any) -> None:
    """Set ``tree[path with dots] = value``, creating parent dicts as needed."""
    parts = path.split(".")
    cur: dict[str, Any] = tree
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _get_value_at_dotted_path(tree: dict[str, Any], path: str) -> Any:
    cur: Any = tree
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


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


def sum_numeric_leaves_in_audit_subtree(node: Any) -> Optional[float]:
    """
    Sum every numeric leaf under ``node``. Used when the LLM nests a scalar one level deeper
    (e.g. ``cash_and_cash_equivalents: { cash_and_cash_equivalents: 123 }``) instead of storing
    the number directly at the path tail.
    """
    if node is None:
        return None
    n = _to_number(node)
    if n is not None:
        return n
    if isinstance(node, dict):
        total = 0.0
        found = False
        for v in node.values():
            sub = sum_numeric_leaves_in_audit_subtree(v)
            if sub is not None:
                total += sub
                found = True
        return total if found else None
    return None


# --- Duplicate-leaf dedupe (double-counting guard) --------------------------------
#
# The pass-2 mapper occasionally emits the SAME source line twice: once correctly nested
# inside a sub-group (e.g. ``current_assets.financial_assets.cash_and_cash_equivalents``)
# and again as a flat sibling of that group (``current_assets.cash_and_cash_equivalents``).
# Both copies survive ``reorder_extracted_to_schema`` (which never drops data), so the UI
# sums both and the parent total double-counts.
#
# Detection is deliberately conservative and uses the document's own math as the decider —
# NOT terminology synonyms (terms vary by auditor) and NOT raw value equality alone (two
# distinct lines can share a value):
#   * scope is per-statement and per-container — a flat leaf of a node is only ever compared
#     against leaves nested in that SAME node's sub-buckets, so cross-statement repeats
#     (e.g. depreciation in P&L and in the cash-flow add-back) and current-vs-non-current
#     same-value lines (different containers) are never compared;
#   * a flat leaf is a duplicate candidate when its term+value matches a nested leaf
#     (high confidence — terminology is consistent WITHIN one document), or its value matches
#     a nested leaf/sub-total while the flat key is non-canonical (the canonicalised-rename
#     case, e.g. "short_term_investments" vs the schema's "investments");
#   * a candidate is only removed when the container has a stated ``total_*`` AND removing it
#     moves the children's sum toward that total without undershooting (the subtotal vetoes
#     legitimately-equal distinct lines). With no stated total we trust only exact term+value
#     repeats. Removed lines are returned to the caller to surface in the unmatched panel
#     (recoverable) — nothing is silently lost.

_DEDUPE_EPS = 0.02  # absolute tolerance for value comparison (rounding / scale jitter)


def _values_close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_DEDUPE_EPS, 1e-4 * max(abs(a), abs(b)))


# Canonical roll-up slot keys that are subtotals/totals yet do NOT start with ``total_``. Two tiers:
#   * bucket-final totals    → the container's OWN total (e.g. the cash-flow activity nets); these
#     stand in for the bucket's reported total in reconciliation.
#   * intermediate subtotals → running roll-ups *inside* a bucket whose own total is a different,
#     outer line — excluded from component sums, but never treated as the bucket total.
# Both tiers must be excluded from a parent's component sum so a subtotal is not double-counted
# alongside the very lines it summarises.
_BUCKET_TOTAL_KEYS: frozenset = frozenset(
    {
        "net_cash_from_operating_activities",
        "net_cash_provided_by_used_in_operating_activities",
        "net_cash_provided_by_used_in_investing_activities",
        "net_cash_provided_by_used_in_financing_activities",
    }
)
_INTERMEDIATE_SUBTOTAL_KEYS: frozenset = frozenset(
    {
        "gross_profit",
        "operating_profit_before_working_capital_changes",
        "cash_generated_from_operations",
        "net_operating_income",
    }
)
_SUBTOTAL_KEYS: frozenset = _BUCKET_TOTAL_KEYS | _INTERMEDIATE_SUBTOTAL_KEYS


def _is_total_key(key: str) -> bool:
    """A subtotal/total leaf to EXCLUDE from a parent's component sum (avoids double-counting)."""
    k = (key or "").lower().lstrip()
    return k.startswith("total") or k in _SUBTOTAL_KEYS


def _norm_term(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (key or "").lower())


def _schema_has_path(schema: Any, dotted: str) -> bool:
    """True when ``dotted`` resolves to a key that exists in ``schema`` (i.e. canonical)."""
    cur = schema
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


def _sum_components(node: Any) -> Optional[float]:
    """Sum numeric leaves under ``node`` EXCLUDING ``total_*`` keys (subtotals of siblings)."""
    n = _to_number(node)
    if n is not None:
        return n
    if isinstance(node, dict):
        total = 0.0
        found = False
        for k, v in node.items():
            if _is_total_key(k):
                continue
            s = _sum_components(v)
            if s is not None:
                total += s
                found = True
        return total if found else None
    return None


def _flat_leaf_value(key: str, v: Any) -> Optional[float]:
    """Value when ``v`` is a scalar or a SELF-wrapped scalar (``{key: number}`` where the inner
    key matches ``key``, e.g. ``cash: {cash: 123}``); None for a real sub-bucket — including a
    single-child group whose child has a different key (``financial_assets: {trade_receivables: …}``)."""
    n = _to_number(v)
    if n is not None:
        return n
    if isinstance(v, dict) and len(v) == 1:
        (ck, cv) = next(iter(v.items()))
        cn = _to_number(cv)
        if cn is not None and _norm_term(ck) == _norm_term(key):
            return cn
    return None


def _walk_leaves(node: dict[str, Any]):
    """Yield (norm_term, value) for every numeric leaf under ``node``, skipping total_* keys."""
    for k, v in node.items():
        if _is_total_key(k):
            continue
        if isinstance(v, dict):
            yield from _walk_leaves(v)
        else:
            x = _to_number(v)
            if x is not None:
                yield (_norm_term(k), x)


def _stated_total(node: dict[str, Any]) -> Optional[float]:
    """The container's reported total: an explicit ``total_*`` child, else a bucket-final subtotal
    (e.g. ``net_cash_*_activities``). Intermediate subtotals (gross_profit, cash_generated_from_
    operations, …) are running roll-ups, NOT the bucket total, so they never stand in here."""
    for k, v in node.items():
        if (k or "").lower().lstrip().startswith("total"):
            t = _sum_components(v) if isinstance(v, dict) else _to_number(v)
            if t is not None:
                return t
    # No explicit total_*: a bucket-final subtotal acts as the reported total. Take the outermost
    # (last in canonical order) so a leading intermediate subtotal can't masquerade as the total.
    last: Optional[float] = None
    for k, v in node.items():
        if k in _BUCKET_TOTAL_KEYS:
            t = _sum_components(v) if isinstance(v, dict) else _to_number(v)
            if t is not None:
                last = t
    return last


def _dedupe_node(node: dict[str, Any], node_path: str, schema: Any, removed: list[dict[str, Any]]) -> None:
    if not isinstance(node, dict):
        return
    # Bottom-up: clean sub-buckets first so parent sums are accurate.
    for k, v in list(node.items()):
        if isinstance(v, dict):
            _dedupe_node(v, f"{node_path}.{k}", schema, removed)

    # Leaves nested inside REAL sub-buckets (multi-child dicts), plus each sub-bucket's subtotal.
    # A wrapped-scalar / scalar sibling is a candidate, never a nested source (avoids self-match).
    nested: list[tuple[str, float]] = []
    for k, v in node.items():
        if _is_total_key(k) or not isinstance(v, dict) or _flat_leaf_value(k, v) is not None:
            continue
        nested.extend(_walk_leaves(v))
        sub = _sum_components(v)
        if sub is not None:
            nested.append((_norm_term(k), sub))
    if not nested:
        return

    # Direct flat leaves of this node (scalars / single-wrapped scalars), excluding totals.
    term_dups: list[tuple[str, float]] = []
    val_dups: list[tuple[str, float]] = []
    for k, v in node.items():
        if _is_total_key(k):
            continue
        fv = _flat_leaf_value(k, v)
        if fv is None or abs(fv) <= _DEDUPE_EPS:
            continue
        nt = _norm_term(k)
        if any(t == nt and _values_close(fv, nv) for t, nv in nested):
            term_dups.append((k, fv))  # same term + value → duplicate (within one document)
        elif any(_values_close(fv, nv) for _, nv in nested) and not _schema_has_path(
            schema, f"{node_path}.{k}"
        ):
            val_dups.append((k, fv))  # renamed copy of a nested leaf → needs reconciliation gate
    if not term_dups and not val_dups:
        return

    stated = _stated_total(node)
    to_remove: list[tuple[str, float]] = []
    if stated is not None:
        over = (_sum_components(node) or 0.0) - stated
        # Only act when children currently OVER-count the reported total; remove high-confidence
        # term dups first, then renamed dups, never removing more than the over-count (the subtotal
        # vetoes legitimately-equal distinct lines).
        for fk, fv in sorted(term_dups, key=lambda x: -x[1]) + sorted(val_dups, key=lambda x: -x[1]):
            if over <= _DEDUPE_EPS:
                break
            if fv <= over + _DEDUPE_EPS:
                to_remove.append((fk, fv))
                over -= fv
    else:
        # No stated total to verify against → trust only exact (term + value) repeats.
        to_remove = list(term_dups)

    for fk, fv in to_remove:
        full_path = f"{node_path}.{fk}"
        node.pop(fk, None)
        removed.append(
            {
                "id": "dedup-" + re.sub(r"[^a-z0-9]+", "-", full_path.lower()).strip("-"),
                "document_label": fk.replace("_", " "),
                "value": fv,
                "section_hint": node_path.split(".")[0],
                # A removed subtotal stays flagged so re-attaching it can't re-inflate the bucket;
                # a removed component duplicate keeps is_total false so it sums normally if re-added.
                "is_total": _is_total_key(fk),
                "_dedup_of": full_path,
            }
        )


def dedupe_duplicate_leaves(
    tree: dict[str, Any], schema: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove double-counted duplicate leaves; return ``(cleaned_tree, removed_rows)``.

    ``removed_rows`` are unmatched-panel shaped (``id`` / ``document_label`` / ``value`` /
    ``section_hint``) so the caller can append them to ``audit_financials_unmatched`` — the
    duplicate is recoverable (re-attachable), never silently dropped. See the module note above
    for the (per-container, reconciliation-gated) detection rules.
    """
    if not isinstance(tree, dict):
        return tree, []
    out = copy.deepcopy(tree)
    removed: list[dict[str, Any]] = []
    for stmt in ("profit_and_loss", "balance_sheet", "cash_flow_statement"):
        node = out.get(stmt)
        if isinstance(node, dict):
            _dedupe_node(node, stmt, schema, removed)
    return out, removed


def pick_first_numeric_deep(tree: dict[str, Any], paths: list[str]) -> Optional[float]:
    """Prefer a scalar at each dotted path; if the resolved value is a dict, sum its numeric subtree."""
    for p in paths:
        raw = _get_value_at_dotted_path(tree, p)
        if raw is None:
            continue
        n = sum_numeric_leaves_in_audit_subtree(raw)
        if n is not None:
            return n
    return None


# Resolution-order constants for the EBITDA derivation formula. Kept module-level so
# :func:`apply_audit_financials_formulas` and :func:`collect_audit_financials_derived_components`
# stay in lockstep on which paths are tried.
_EBITDA_PBT_PATHS: tuple[str, ...] = (
    "profit_and_loss.profit_loss_before_tax",
    "profit_and_loss.profit_before_tax",
    "profit_and_loss.pbt",
)
_EBITDA_FINANCE_COSTS_PATHS: tuple[str, ...] = (
    "profit_and_loss.expenses.finance_costs",
    "profit_and_loss.finance_costs",
)
_EBITDA_DEPRECIATION_PRIMARY_PATHS: tuple[str, ...] = (
    "profit_and_loss.expenses.depreciation_and_amortization_expense",
    "profit_and_loss.depreciation_and_amortization_expense",
    "profit_and_loss.operating_expenses.depreciation_and_amortization_expense",
    "profit_and_loss.operating_expenses.depreciation_and_amortisation",
)
_EBITDA_DEPRECIATION_FALLBACK_PATHS: tuple[str, ...] = (
    "cash_flow_statement.cash_flows_from_operating_activities.adjustments.depreciation_and_amortization",
    "cash_flow_statement.cash_flows_from_operating_activities.adjustments.depreciation_and_amortisation",
)


def _resolve_first_numeric_with_path(
    tree: dict[str, Any],
    paths: tuple[str, ...] | list[str],
) -> tuple[Optional[str], Optional[float]]:
    """Same resolution as :func:`pick_first_numeric_deep` but also return the matched path."""
    for p in paths:
        raw = _get_value_at_dotted_path(tree, p)
        if raw is None:
            continue
        n = sum_numeric_leaves_in_audit_subtree(raw)
        if n is not None:
            return p, n
    return None, None


def apply_audit_financials_formulas(extracted: dict[str, Any]) -> dict[str, Any]:
    """Apply derived-field formulas (currently EBITDA) in-place and return the tree."""
    explicit_e_raw = _get_value_at_dotted_path(extracted, "profit_and_loss.ebitda")
    explicit_ebitda = (
        pick_first_numeric_deep(extracted, ["profit_and_loss.ebitda"])
        if isinstance(explicit_e_raw, dict)
        else _to_number(explicit_e_raw)
    )
    if explicit_ebitda is not None:
        set_value_at_dotted_path(extracted, "profit_and_loss.ebitda", explicit_ebitda)
        return extracted

    pbt = pick_first_numeric_deep(extracted, list(_EBITDA_PBT_PATHS))
    finance_costs = pick_first_numeric_deep(extracted, list(_EBITDA_FINANCE_COSTS_PATHS))

    depreciation = pick_first_numeric_deep(extracted, list(_EBITDA_DEPRECIATION_PRIMARY_PATHS))
    if depreciation is None:
        depreciation = pick_first_numeric_deep(extracted, list(_EBITDA_DEPRECIATION_FALLBACK_PATHS))

    if pbt is not None and depreciation is not None:
        fc_component = finance_costs if finance_costs is not None else 0.0
        set_value_at_dotted_path(extracted, "profit_and_loss.ebitda", pbt - (fc_component + depreciation))
    return extracted


def collect_audit_financials_derived_components(extracted: dict[str, Any]) -> dict[str, Any]:
    """Return source values used by derived formulas (non-mutating).

    Mirrors the path-resolution order used by :func:`apply_audit_financials_formulas` so
    the captured paths and values are exactly what the formula consumed. Currently covers
    only EBITDA; extend here when new derived metrics are added.

    Returned shape::

        {
          "ebitda": {
            "computed": bool,                # True when EBITDA was derived from components (vs explicit / unavailable)
            "formula": "pbt - (finance_costs + depreciation)",
            "components": {
              "pbt":           {"path": str|None, "value": float|None},
              "finance_costs": {"path": str|None, "value": float|None},
              "depreciation":  {"path": str|None, "value": float|None},
            },
          },
        }
    """
    if not isinstance(extracted, dict):
        return {}

    explicit_e_raw = _get_value_at_dotted_path(extracted, "profit_and_loss.ebitda")
    explicit_ebitda = (
        pick_first_numeric_deep(extracted, ["profit_and_loss.ebitda"])
        if isinstance(explicit_e_raw, dict)
        else _to_number(explicit_e_raw)
    )

    pbt_path, pbt = _resolve_first_numeric_with_path(extracted, _EBITDA_PBT_PATHS)
    fc_path, finance_costs = _resolve_first_numeric_with_path(extracted, _EBITDA_FINANCE_COSTS_PATHS)
    dep_path, depreciation = _resolve_first_numeric_with_path(extracted, _EBITDA_DEPRECIATION_PRIMARY_PATHS)
    if depreciation is None:
        dep_path, depreciation = _resolve_first_numeric_with_path(extracted, _EBITDA_DEPRECIATION_FALLBACK_PATHS)

    # "computed" is True only when EBITDA was NOT explicitly extracted AND the formula
    # actually had enough inputs to fire (PBT + depreciation, finance_costs optional).
    computed = explicit_ebitda is None and pbt is not None and depreciation is not None

    return {
        "ebitda": {
            "computed": bool(computed),
            "formula": "pbt - (finance_costs + depreciation)",
            "components": {
                "pbt": {"path": pbt_path, "value": pbt},
                "finance_costs": {"path": fc_path, "value": finance_costs},
                "depreciation": {"path": dep_path, "value": depreciation},
            },
        },
    }


def reorder_extracted_to_schema(tree: Any, schema: Any) -> Any:
    """Return ``tree`` with keys ordered to match ``schema``.

    Canonical keys (those present in the schema) come first, in schema order;
    any extra keys the document/LLM added that are not in the schema are appended
    afterwards in their original relative order. Recurses into dict nodes that have
    a corresponding dict node in the schema.

    Pure reordering: never adds, drops, or changes a value — so the served tree
    matches ``AUDIT_FINANCIALS_SCHEMA_FALLBACK`` order without any data loss. This is
    the single source of truth for field order (the UI renders in received order).
    """
    if not isinstance(tree, dict) or not isinstance(schema, dict):
        return tree
    out: dict[str, Any] = {}
    for k, sch_v in schema.items():
        if k in tree:
            tv = tree[k]
            if isinstance(tv, dict) and isinstance(sch_v, dict) and sch_v:
                out[k] = reorder_extracted_to_schema(tv, sch_v)
            else:
                out[k] = tv
    for k, tv in tree.items():
        if k not in out:
            out[k] = tv
    return out


def build_audit_financials_mapping_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON Schema for the pass-2 mapping output ``{mapped, unmatched}``.

    Used as a structured-output contract (OpenAI ``response_format`` / Bedrock tool
    ``inputSchema``) so the model emits the canonical structure at the source instead of
    inventing sibling keys (the cause of duplicate / mis-ordered lines).

    Design — enforce structure WITHOUT losing document-specific data:
    - ``None`` schema slot  → scalar ``{number, null}``.
    - canonical bucket      → object with its canonical children and ``additionalProperties:false``
                              so no stray sibling can appear at a canonical position …
    - … EXCEPT the ``other`` child (and empty ``{}`` buckets), which stay **open**
      (``additionalProperties`` allowed) so overflow is still captured with dotted paths
      (keeps it attachable and locatable for page refs).
    - top-level ``unmatched`` array for lines that fit nowhere canonical.

    Note: kept deliberately NOT hard-``strict`` (open ``other`` is incompatible with OpenAI
    strict mode). ``finalize_audit_financials_extracted`` remains the deterministic backstop.
    """

    def node(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"type": ["number", "null"]}
        if len(value) == 0:
            # ``{}`` bucket = "add any semantically appropriate children" → open overflow.
            return {"type": "object", "additionalProperties": True}
        props: dict[str, Any] = {}
        for k, v in value.items():
            if k == "other":
                props[k] = {"type": "object", "additionalProperties": True}
            else:
                props[k] = node(v)
        # Closed at canonical positions; overflow must go to the open ``other`` child or ``unmatched``.
        return {"type": "object", "properties": props, "additionalProperties": False}

    mapped = {
        "type": "object",
        "properties": {k: node(v) for k, v in schema.items()},
        "required": list(schema.keys()),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["mapped", "unmatched"],
        "properties": {
            "mapped": mapped,
            "unmatched": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "document_label": {"type": "string"},
                        "value": {"type": ["number", "string", "null"]},
                        "section_hint": {"type": ["string", "null"]},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": False,
    }


def finalize_audit_financials_extracted(tree: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Schema defaults → synonym consolidation → derived formulas → canonical ordering."""
    out = apply_schema_defaults_to_extracted(tree, schema)
    out = consolidate_financial_synonyms(out)
    out = apply_audit_financials_formulas(out)
    return reorder_extracted_to_schema(out, schema)


def ensure_nested_dict(d: dict[str, Any], path: str) -> dict[str, Any]:
    parts = path.split(".")
    cur: dict[str, Any] = d
    for p in parts:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        nxt = cur[p]
        assert isinstance(nxt, dict)
        cur = nxt
    return cur


def set_child_value(
    tree: dict[str, Any],
    parent_path: str,
    child_key: str,
    value: Any,
) -> None:
    """Set tree[parent_path...][child_key] = value, creating parent dicts as needed."""
    parts = parent_path.split(".")
    cur: dict[str, Any] = tree
    for p in parts:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        nxt = cur[p]
        assert isinstance(nxt, dict)
        cur = nxt
    cur[child_key] = value
