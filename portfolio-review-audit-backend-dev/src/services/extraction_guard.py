"""
Deterministic post-extraction guard for audit financials.

Verifies the extracted statements against accounting identities that only hold when a single
period/entity column was read consistently — the cheap, model-agnostic detector of the
year/column mixing this pipeline exists to prevent:

  * Balance sheet:  total assets == total equity + total liabilities
  * Cash flow:      net(operating) + net(investing) + net(financing) == net change in cash
  *                 opening cash + net change == closing cash

NON-BLOCKING: this only annotates the result (``guard_status`` / ``confidence``). It never
stops the pipeline — a failure just flags the row as low-confidence for visibility. Checks that
can't find their inputs are skipped (``insufficient_data``), never failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GuardCheck:
    name: str
    ok: bool
    detail: str
    # Hard checks are exact accounting identities and gate confidence. Soft checks (e.g. the
    # cash-flow subtotal sum, which a separate FX-effect line legitimately breaks) are reported
    # but do not by themselves flip the result to "failed".
    hard: bool = True


@dataclass
class GuardResult:
    status: str = "insufficient_data"  # "reconciled" | "failed" | "insufficient_data"
    confidence: str = "unknown"  # "high" | "low" | "unknown"
    checks: List[GuardCheck] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }


def _flatten_numeric(obj, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten_numeric(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten_numeric(v, f"{prefix}[{i}]"))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix] = float(obj)
    return out


def _close(a: float, b: float, rel: float, abs_tol: float = 100.0) -> bool:
    """Tolerant equality. ``rel`` is intentionally per-check: accounting *identities*
    (assets=equity+liab; opening+Δ=closing) are exact, so they use a tight ``rel`` — a loose
    tolerance would pass year-mixing, since YoY totals often differ by well under 1%."""
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


# Exact identities (catch mixing) use a tight band; the cash-flow sub-total sum is looser because
# a separate "effect of FX on cash" line legitimately breaks op+inv+fin == net change.
_REL_IDENTITY = 0.0002  # 0.02% — rounding only; flags year/column mixing
_REL_CF_SUBTOTALS = (
    0.03  # 3% — tolerant of an FX-effect line not captured as op/inv/fin
)


def _find(
    flat: Dict[str, float], must_all: Tuple[str, ...], must_none: Tuple[str, ...] = ()
) -> Optional[float]:
    """Value of the shortest-path leaf whose lowercased path contains every ``must_all`` token
    and none of ``must_none`` — biased to the grand-total line, not a sub-section subtotal.
    """
    cands = []
    for path, val in flat.items():
        pl = path.lower()
        if all(m in pl for m in must_all) and not any(x in pl for x in must_none):
            cands.append((len(path), path, val))
    if not cands:
        return None
    cands.sort()
    return cands[0][2]


# Sub-section subtotals we must NOT mistake for the grand total.
_NOT_GRAND = ("current", "non_current", "noncurrent", "non-current")


def run_extraction_guard(extracted: dict) -> GuardResult:
    """
    Run the accounting-identity checks over an extracted/mapped financials tree (the raw
    document-structure tree works best, since it carries the statement's own total lines).
    """
    if not isinstance(extracted, dict):
        return GuardResult()
    flat = _flatten_numeric(extracted)
    checks: List[GuardCheck] = []

    # --- Balance sheet: assets = equity + liabilities ---
    total_assets = _find(flat, ("total", "asset"), _NOT_GRAND)
    total_equity = _find(flat, ("total", "equit"))
    total_liab = _find(flat, ("total", "liabilit"), _NOT_GRAND)
    if total_assets is not None and total_equity is not None and total_liab is not None:
        ok = _close(total_assets, total_equity + total_liab, rel=_REL_IDENTITY)
        checks.append(
            GuardCheck(
                "balance_sheet_identity",
                ok,
                f"assets={total_assets:,.0f} vs equity+liabilities={total_equity + total_liab:,.0f}",
            )
        )

    # --- Cash flow: net(op)+net(inv)+net(fin) = net change ---
    net_op = _find(flat, ("net", "operating"))
    net_inv = _find(flat, ("net", "investing"))
    net_fin = _find(flat, ("net", "financing"))
    net_change = (
        _find(flat, ("net", "increase", "cash"))
        or _find(flat, ("net", "decrease", "cash"))
        or _find(flat, ("net", "change", "cash"))
    )
    if None not in (net_op, net_inv, net_fin) and net_change is not None:
        total_flows = net_op + net_inv + net_fin
        ok = _close(total_flows, net_change, rel=_REL_CF_SUBTOTALS)
        checks.append(
            GuardCheck(
                "cash_flow_reconciliation",
                ok,
                f"op+inv+fin={total_flows:,.0f} vs net_change={net_change:,.0f}",
                hard=False,  # FX-effect line legitimately breaks this sum
            )
        )

    # --- Cash flow: opening + net change = closing ---
    opening = _find(flat, ("cash", "beginning")) or _find(flat, ("cash", "opening"))
    closing = _find(
        flat,
        (
            "cash",
            "end",
        ),
    ) or _find(flat, ("cash", "closing"))
    if opening is not None and closing is not None and net_change is not None:
        ok = _close(opening + net_change, closing, rel=_REL_IDENTITY)
        checks.append(
            GuardCheck(
                "cash_flow_continuity",
                ok,
                f"opening+net_change={opening + net_change:,.0f} vs closing={closing:,.0f}",
            )
        )

    hard_checks = [c for c in checks if c.hard]
    if not hard_checks:
        return GuardResult(
            status="insufficient_data", confidence="unknown", checks=checks
        )
    hard_ok = all(c.ok for c in hard_checks)
    result = GuardResult(
        status="reconciled" if hard_ok else "failed",
        confidence="high" if hard_ok else "low",
        checks=checks,
    )
    logger.info(
        "extraction guard: status=%s checks=%s",
        result.status,
        [(c.name, c.ok) for c in checks],
    )
    return result
