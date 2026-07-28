"""
Second-level *section tagging* for leftover (unmatched) audit-financials lines.

The schema mapper either places a line at an exact canonical leaf or leaves it ``unmatched``. An
unmatched line today carries at best a top-level ``section_hint`` (``cash_flow_statement``) — and
the LLM mapper is allowed to emit even that as ``null``. That is why such lines render as confusing
floaters at the statement root instead of inside the section they belong to.

This module assigns every leftover row a **``placement_path``**: the *deepest section node it
confidently belongs to* (e.g. ``cash_flow_statement.cash_flows_from_operating_activities``), plus a
``placement_tier`` and ``placement_source`` for provenance. The UI uses ``placement_path`` to nest
the row inside that section (excluded from the section's Σ), instead of dumping it at the root.

Resolution is **deterministic first, LLM only for the residual** (kept in
:mod:`audit_document_extraction`, mirroring the residual-assignment pass):

  * **Tier 1** — the row already has a confident leaf *suggestion* (≥ ``SUGGESTION_STRONG``):
    ``placement_path`` = the section ancestor of that leaf; the leaf stays a one-click confirm.
  * **Tier 2** — the row's *band* (statement + side/currentness for BS, activity for CFS) maps
    deterministically to a section node. No LLM, no guessing — the document's own grouping decides.
  * **(LLM) Tier 2** — for rows the band cannot place deeper than a statement, a small-universe
    pick over that statement's second-level sections (handled by the caller).
  * **Tier 3** — neither leaf, band, nor LLM resolves a section → ``placement_path`` stays ``None``
    and the row renders in the statement/document-level "unclassified" tray.

Nothing here touches the canonical tree or the reconciled metrics — ``placement_path`` is display
routing metadata only, so a wrong section tag can only mis-file a row in a tray, never a number.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.llm.prompts import AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL
from src.services.financial_field_definitions import band_for_path, bands_compatible
from src.services.financial_label_matching import SUGGESTION_STRONG, normalize_label

logger = logging.getLogger(__name__)

# A confident leaf suggestion at/above this confidence makes a row Tier 1 (exact-leaf, confirm).
LEAF_CONFIRM_FLOOR = SUGGESTION_STRONG


def _section_nodes(schema: dict[str, Any], *, max_depth: int = 4) -> set[str]:
    """All non-leaf section node paths under the three statements, down to ``max_depth``.

    Depth is 1-based on the statement (its children are depth 2). The open ``other`` overflow bucket
    is excluded — it is not a real section a line should be *tagged* to. Used both to validate a
    derived ``placement_path`` and to find the section ancestor of a suggested leaf.
    """
    out: set[str] = set()

    def walk(node: Any, parts: list[str], depth: int) -> None:
        if not isinstance(node, dict) or depth > max_depth:
            return
        for k, v in node.items():
            if not isinstance(k, str) or k == "other" or not isinstance(v, dict):
                continue
            p = parts + [k]
            out.add(".".join(p))
            walk(v, p, depth + 1)

    for stmt, sub in schema.items():
        if stmt in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL and isinstance(sub, dict):
            walk(sub, [stmt], 2)
    return out


def llm_section_candidates(schema: dict[str, Any], statement: Optional[str]) -> list[str]:
    """Second-level (depth-2) section nodes for the LLM section pick — the small universe.

    For a known statement this is just its handful of sections (CFS → the 3 activities + cash
    equivalents; P&L → revenue/expenses/tax/OCI; BS → assets/equity/liabilities). When the statement
    is unknown, return every statement's depth-2 sections so the model can place it from scratch.
    """
    depth2 = _section_nodes(schema, max_depth=2)
    if statement in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        return sorted(p for p in depth2 if p.split(".")[0] == statement)
    return sorted(depth2)


def placement_from_band(band: dict[str, Any]) -> Optional[str]:
    """Deepest section path a line's *band* deterministically implies, or ``None``.

    Uses only positional signal (statement + the document's own grouping), never label wording:
      * CFS + activity → the matching ``cash_flows_from_*_activities`` section.
      * BS  + side(+currentness) → ``assets``/``equity``/``liabilities`` (and current/non-current).
      * P&L → no sub-dimension in the band → returns ``None`` (defer to suggestion/LLM/label).
    Statement-only results (e.g. CFS with no activity) also return ``None`` so the caller routes the
    row to the LLM pass to resolve the second level rather than stopping at the statement.
    """
    if not isinstance(band, dict):
        return None
    stmt = band.get("statement")
    if stmt == "cash_flow_statement":
        act = band.get("activity")
        mapping = {
            "operating": "cash_flows_from_operating_activities",
            "investing": "cash_flows_from_investing_activities",
            "financing": "cash_flows_from_financing_activities",
        }
        sub = mapping.get(act or "")
        return f"cash_flow_statement.{sub}" if sub else None
    if stmt == "balance_sheet":
        side = band.get("side")
        cur = band.get("currentness")
        if side == "equity":
            return "balance_sheet.equity"
        if side in ("assets", "liabilities"):
            tail = "assets" if side == "assets" else "liabilities"
            if cur in ("current", "non_current"):
                return f"balance_sheet.{side}.{cur}_{tail}"
            return f"balance_sheet.{side}"
        return None
    return None


def _section_ancestor(full_path: str, valid: set[str]) -> Optional[str]:
    """Deepest recognized section node that is an ancestor of ``full_path`` (a leaf path)."""
    parts = full_path.split(".")
    for end in range(len(parts) - 1, 0, -1):
        cand = ".".join(parts[:end])
        if cand in valid:
            return cand
    return None


def _row_band(row: dict[str, Any]) -> dict[str, Any]:
    """The row's source band: carried ``band`` → derived from ``raw_path`` → top-level statement."""
    band = row.get("band")
    if isinstance(band, dict) and band.get("statement"):
        return band
    rp = row.get("raw_path")
    if isinstance(rp, str) and rp:
        derived = band_for_path(rp)
        if derived.get("statement"):
            return derived
    sh = row.get("section_hint")
    if sh in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL:
        return {"statement": sh, "side": None, "currentness": None, "activity": None}
    return {"statement": None, "side": None, "currentness": None, "activity": None}


def section_from_memory(
    label: str,
    band: dict[str, Any],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
    valid: set[str],
) -> Optional[str]:
    """Section a previously reviewer-confirmed label was attached under, or ``None``.

    The learned alias store (``audit_financials_label_aliases_v1``) maps a normalized label to a
    canonical leaf; this derives the *section* of that leaf — i.e. uses the same memory at the
    second level. Safe to auto-apply because the resulting tag only nests the row in a (never-summed)
    section tray. Two guards mirror the leaf-suggestion path: the alias must be **band-compatible**
    with the row's source position (no current↔non-current / operating↔financing bleed), and when a
    label was attached under several sections the **most-confirmed** (highest ``count``) wins.
    """
    norm = normalize_label(label)
    if not norm or not aliases_index:
        return None
    best_path: Optional[str] = None
    best_count = -1
    for (_sec, nlabel), rec in aliases_index.items():
        if nlabel != norm:
            continue
        fp = rec.get("full_path")
        if not isinstance(fp, str) or not fp:
            continue
        if not bands_compatible(band or {}, band_for_path(fp)):
            continue
        anc = _section_ancestor(fp, valid)
        if anc is None:
            continue
        count = int(rec.get("count") or 1)
        if count > best_count:
            best_path, best_count = anc, count
    return best_path


def assign_placement_paths(
    rows: list[Any],
    schema: dict[str, Any],
    *,
    aliases_index: Optional[dict[tuple[str, str], dict[str, Any]]] = None,
    overwrite: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stamp ``placement_path``/``placement_tier``/``placement_source`` on each row deterministically.

    Returns ``(rows, needing_llm)`` where ``needing_llm`` is the subset that has no confident
    placement yet but is worth a small-universe LLM section pick (the caller runs that and applies
    :func:`apply_llm_section_assignments`). Idempotent unless ``overwrite``. Mutates rows in place
    (and returns them) so it composes with the existing enrich step.
    """
    valid = _section_nodes(schema, max_depth=4)
    needing_llm: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if not overwrite and isinstance(row.get("placement_path"), str):
            continue

        # Tier 1: a confident leaf suggestion → section ancestor of the suggested leaf.
        sug = row.get("suggestion")
        if isinstance(sug, dict):
            fp = sug.get("full_path")
            conf = float(sug.get("confidence") or 0.0)
            if isinstance(fp, str) and fp and conf >= LEAF_CONFIRM_FLOOR:
                anc = _section_ancestor(fp, valid)
                if anc:
                    row["placement_path"] = anc
                    row["placement_tier"] = 1
                    row["placement_source"] = "suggestion"
                    continue

        # Tier 2 (deterministic): the band maps to a real section node.
        band = _row_band(row)
        bp = placement_from_band(band)
        if bp and bp in valid:
            row["placement_path"] = bp
            row["placement_tier"] = 2
            row["placement_source"] = "band"
            continue

        # Tier 2 (memory): a previously reviewer-confirmed label → its learned section (band-gated).
        # Auto-applied (safe: the section tray is never summed) and saves an LLM call.
        if aliases_index:
            label = str(row.get("document_label") or row.get("key") or "")
            mp = section_from_memory(label, band, aliases_index, valid)
            if mp:
                row["placement_path"] = mp
                row["placement_tier"] = 2
                row["placement_source"] = "memory"
                continue

        # Unresolved deterministically → candidate for the LLM section pick.
        row.setdefault("placement_path", None)
        row["placement_tier"] = 3
        row["placement_source"] = None
        needing_llm.append(row)
    return rows, needing_llm


def apply_llm_section_assignments(
    rows: list[dict[str, Any]], assignments: dict[str, Optional[str]], schema: dict[str, Any]
) -> int:
    """Apply ``{row_id -> section_path}`` from the LLM pick, validating each target is a real section.

    A target is accepted only if it is a recognized section node in the schema; anything else (or a
    missing/``unmatched`` target) leaves the row Tier 3. Returns the number of rows promoted to
    Tier 2. The deterministic band gate already ran, so this only fills genuinely-ambiguous rows.
    """
    valid = _section_nodes(schema, max_depth=4)
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id") is not None}
    promoted = 0
    for rid, target in (assignments or {}).items():
        row = by_id.get(str(rid))
        if row is None or not isinstance(target, str) or target not in valid:
            continue
        row["placement_path"] = target
        row["placement_tier"] = 2
        row["placement_source"] = "llm"
        promoted += 1
    return promoted
