"""
Scale numeric amounts under audit-financials statement subtrees for currency conversion.

Only ``profit_and_loss``, ``balance_sheet``, and ``cash_flow_statement`` are touched;
other top-level extraction keys are left unchanged (e.g. diagnostics, unmatched stay external).
"""
from __future__ import annotations

import copy
import math
from typing import Any, Optional

AUDIT_FINANCIAL_AMOUNT_ROOT_KEYS = frozenset({"profit_and_loss", "balance_sheet", "cash_flow_statement"})


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


def _scale_walk(node: Any, rate: float, *, decimals: int) -> tuple[Any, int]:
    """Return (scaled_copy, scaled_leaf_count)."""
    if node is None:
        return node, 0

    parsed = _to_number(node)
    if parsed is not None:
        scaled = round(float(parsed) * rate, decimals)
        return scaled, 1

    if isinstance(node, dict):
        out: dict[str, Any] = {}
        total = 0
        for k, v in node.items():
            nv, n = _scale_walk(v, rate, decimals=decimals)
            out[k] = nv
            total += n
        return out, total

    if isinstance(node, list):
        out_list: list[Any] = []
        total = 0
        for item in node:
            nv, n = _scale_walk(item, rate, decimals=decimals)
            out_list.append(nv)
            total += n
        return out_list, total

    return copy.deepcopy(node), 0


def scale_extracted_statement_amounts(tree: dict[str, Any], rate: float, *, decimals: int = 6) -> tuple[dict[str, Any], int]:
    """
    Deep-copy ``tree`` and multiply every numeric leaf under the three canonical statement roots.

    Non-numeric scalars are preserved unchanged.
    Returns (new_tree, total_numeric_leaves_scaled).
    """
    if not isinstance(tree, dict):
        raise ValueError("extracted must be a mapping")
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("rate must be a positive finite float")

    out = copy.deepcopy(tree)
    scaled = 0
    for root in AUDIT_FINANCIAL_AMOUNT_ROOT_KEYS:
        if root not in out:
            continue
        branch, n = _scale_walk(out[root], rate, decimals=decimals)
        out[root] = branch
        scaled += n
    return out, scaled


def scale_unmatched_amounts(
    unmatched: Any, rate: float, *, decimals: int = 6
) -> tuple[Any, int]:
    """Scale the numeric ``value`` on each ``audit_financials_unmatched`` row by ``rate``.

    The sibling of :func:`scale_extracted_statement_amounts` for the unmatched panel: a currency
    conversion scales the statement tree, so the unmatched lines must scale by the SAME rate too —
    otherwise they stay in the old currency and re-attaching one injects a wrong-currency number.
    Returns ``(new_rows, scaled_count)``; non-list input and non-numeric values pass through.
    """
    if not isinstance(unmatched, list):
        return unmatched, 0
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("rate must be a positive finite float")
    out: list[Any] = []
    scaled = 0
    for row in unmatched:
        if not isinstance(row, dict):
            out.append(row)
            continue
        new_row = dict(row)
        n = _to_number(new_row.get("value"))
        if n is not None:
            new_row["value"] = round(n * rate, decimals)
            scaled += 1
        out.append(new_row)
    return out, scaled
