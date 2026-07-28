"""
Band-aware **deterministic** pass-2 mapper (raw extracted tree → canonical ``{mapped, unmatched}``).

Why this exists
---------------
The LLM mapper (:func:`src.llm.prompts.audit_financials_schema_mapping_system_prompt`) re-classifies
every line by *meaning* ("loose semantic matching"). Meaning alone cannot locate a line whose label
is shared across positions — "Borrowings", "Trade receivables", "Provisions", "Lease liabilities",
"Investments", "Loans" all appear under **both** current and non-current; "Depreciation" appears in
both the P&L and the cash-flow add-backs; financing-activity lines look like working-capital lines.
What disambiguates them is **position**, not wording — and pass-1 already captured that position as
the chain of section headers above each line.

This module maps using two grounded, statement-agnostic signals (both present in every statement):

1. **Band** — the chain of source headers above a line (statement; balance-sheet side +
   currentness; cash-flow activity), from :mod:`src.services.financial_field_definitions`. A
   candidate canonical leaf is admissible only if its band is *compatible* with the source line's
   band. This is a HARD filter (not the soft wrong-statement penalty used for HITL suggestions), so
   e.g. a financing line can never land in operating working capital, and a "non-current borrowings"
   line can never land in the current-borrowings slot.
2. **Arithmetic role** — totals vs components. A line explicitly labelled a total may only occupy a
   ``total_*`` / known-subtotal slot; a component line may never fill a ``total_*`` slot.

Token/alias scoring is delegated to :mod:`src.services.financial_label_matching` (curated synonyms +
reviewer-learned aliases); this module adds the band/role gating and the deterministic placement.
Lines that don't clear the auto-map confidence floor become ``unmatched`` rows (retaining
``raw_path`` so the UI can still show them in their source position and HITL can one-click attach) —
never force-fit.

Pure Python; no LLM, no DB, no I/O. The caller supplies the candidate/alias indexes (built once via
:func:`src.services.financial_label_matching.prepare_label_matcher`).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from src.llm.prompts import AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL
from src.services.financial_audit_schema import set_value_at_dotted_path
from src.services.financial_field_definitions import (
    band_for_path,
    band_for_source_headers,
    bands_compatible,
    detect_statement,
)
from src.services.financial_label_matching import suggest_candidates

logger = logging.getLogger(__name__)

# Minimum confidence (from the label matcher) to auto-place a line at a canonical leaf. Below this
# the line is left unmatched (displayed in source position + HITL) rather than force-fit. Aliases
# that the matcher returns at ~0.99 clear this comfortably; curated/token matches need a strong
# single-surface-form hit (matcher's SUGGESTION_STRONG is 0.85).
AUTO_MAP_CONFIDENCE_FLOOR = 0.82

# Labels/keys that denote a total or subtotal line (arithmetic-role gate). Includes subtotal
# phrasings that never contain the word "total" but are *unambiguously* roll-up lines wherever
# they appear (so a component line is never mis-flagged). Deliberately EXCLUDES context-dependent
# phrases like "profit before tax" / "profit for the period", which are genuine *component* inputs
# inside the indirect-method operating cash flow — flagging those would bounce them out of their
# (component) cash-flow slot. Those are handled by canonical-key/position downstream, not here.
_TOTAL_WORDS: tuple[str, ...] = (
    "total",
    "subtotal",
    "sub_total",
    "aggregate",
    "grand_total",
    "net_cash",  # "net cash from / used in / provided by … activities"
    "attributable_to",  # "(profit|equity) attributable to owners / equity holders / members"
    "gross_profit",
    "operating_profit",  # incl. "operating profit before working capital changes"
    "net_operating_income",
    "comprehensive_income",
    "cash_generated_from_operations",
)

# Canonical subtotal leaves that are NOT prefixed ``total_`` but are still roll-up lines, so a
# document line explicitly labelled a "total/subtotal" is allowed to map onto them.
_KNOWN_SUBTOTAL_KEYS: frozenset = frozenset(
    {
        "gross_profit",
        "operating_profit_before_working_capital_changes",
        "cash_generated_from_operations",
        "net_cash_from_operating_activities",
        "net_cash_provided_by_used_in_operating_activities",
        "net_cash_provided_by_used_in_investing_activities",
        "net_cash_provided_by_used_in_financing_activities",
    }
)


def _to_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
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
        return None if (math.isnan(x) or math.isinf(x)) else x
    return None


def _norm(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


# --- arithmetic-role gate ----------------------------------------------------------------


def is_total_label(label: Any) -> bool:
    """True when a document label/key explicitly denotes a total or subtotal line."""
    n = _norm(str(label or ""))
    if not n:
        return False
    # A canonical subtotal slot key (e.g. ``net_cash_from_operating_activities``) is a roll-up
    # line even though it contains no "total" word.
    if n in _KNOWN_SUBTOTAL_KEYS:
        return True
    return any(w in n for w in _TOTAL_WORDS)


def _is_total_canonical_key(key: str) -> bool:
    k = (key or "").lower()
    return k.startswith("total") or k in _KNOWN_SUBTOTAL_KEYS


def _role_compatible(line_is_total: bool, canonical_key: str) -> bool:
    """A total line → only total/subtotal slots; a component line → never a total slot."""
    canon_is_total = _is_total_canonical_key(canonical_key)
    return canon_is_total if line_is_total else (not canon_is_total)


# --- "other" catch-all visibility --------------------------------------------------------
#
# A line auto-tagged into an ``other_*`` canonical SCALAR slot (e.g. ``other_current_liabilities``)
# would otherwise become an anonymous number — the reviewer can't see *what* was classified there
# (the user-reported "share application money refundable → other current liability" case). We retain
# its original document label as a signed roll-up component so the UI surfaces it (reusing the
# existing ``audit_financials_field_components`` breakdown). This is distinct from the open ``other``
# overflow bucket, which already keeps each line's key/label.


def _is_other_slot_key(key: str) -> bool:
    """True for an ``other_*`` catch-all canonical scalar slot (not the open ``other`` bucket)."""
    return (key or "").lower().startswith("other_")


def _record_other_component(
    other_components: Optional[dict[str, list]], full_path: str, line: dict[str, Any]
) -> None:
    """Retain ``line``'s original label as a breakdown component when it lands in an ``other_*`` slot.
    No-op when ``other_components`` is None, the slot isn't an ``other_*`` catch-all, or the value
    isn't numeric. The component value is stored as-is (sign ``+``) so Σ components == the leaf value.
    """
    if other_components is None:
        return
    if not _is_other_slot_key(full_path.rsplit(".", 1)[-1]):
        return
    val = line.get("value")
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return
    label = str(line.get("label") or full_path.rsplit(".", 1)[-1].replace("_", " ")).strip()
    slug = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-") or "line"
    other_components.setdefault(full_path, []).append(
        {
            "id": f"other-{slug}"[:48],
            "label": label,
            "value": float(val),
            "sign": "+",
            "source": "auto_other",
        }
    )


# --- source-line flattening --------------------------------------------------------------


def flatten_source_lines(raw_tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Numeric leaves under the three canonical statements, each tagged with band + role.

    Only descends into subtrees that resolve to one of the three statements (skips
    ``report_metadata``, ``currency``, SOCE, notes, etc.). ``label`` is the document's own wording
    (humanized leaf key); ``raw_path`` is retained so unmatched rows can be shown in place.
    """
    lines: list[dict[str, Any]] = []
    if not isinstance(raw_tree, dict):
        return lines

    def walk(node: Any, path_parts: list[str]) -> None:
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            next_parts = path_parts + [k]
            if isinstance(v, dict):
                walk(v, next_parts)
                continue
            if isinstance(v, bool):
                continue
            num = _to_number(v)
            if num is None:
                continue
            statement = detect_statement(next_parts)
            if statement is None:
                continue  # outside the three reconciled statements → out of scope
            ancestors = next_parts[:-1]
            leaf_key = next_parts[-1]
            lines.append(
                {
                    "raw_path": ".".join(next_parts),
                    "label": leaf_key.replace("_", " ").strip(),
                    "leaf_key": leaf_key,
                    "value": num,
                    "statement": statement,
                    "band": band_for_source_headers(ancestors, statement),
                    "is_total": is_total_label(leaf_key),
                }
            )

    for top_key, sub in raw_tree.items():
        if isinstance(sub, dict):
            walk(sub, [top_key])
    return lines


# --- mapping -----------------------------------------------------------------------------


def _dotted_get(tree: dict[str, Any], path: str) -> Any:
    cur: Any = tree
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _best_band_locked_target(
    line: dict[str, Any],
    *,
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Best canonical leaf for a line after band + role gating, or ``None`` if none clears the floor."""
    band = line["band"]
    is_total = bool(line["is_total"])
    # Pre-filter the candidate universe to band + role compatible leaves, so the matcher can only
    # ever return a placement that is positionally valid. Prefer the candidate's precomputed band
    # (attached by build_candidate_index) and fall back to deriving it from the path.
    filtered = [
        c
        for c in candidate_index
        if _role_compatible(is_total, c["key"])
        and bands_compatible(band, c.get("band") or band_for_path(c["full_path"]))
    ]
    if not filtered:
        return None

    results = suggest_candidates(
        line["label"],
        section_hint=line["statement"],
        candidate_index=filtered,
        aliases_index=aliases_index,
        top_k=3,
    )
    for r in results:
        fp = r.get("full_path")
        if not isinstance(fp, str) or not fp:
            continue
        # Alias hits bypass the pre-filter (the matcher looks them up independently), so re-check
        # band + role on the final pick as well.
        if not _role_compatible(is_total, fp.rsplit(".", 1)[-1]):
            continue
        if not bands_compatible(band, band_for_path(fp)):
            continue
        if float(r.get("confidence") or 0.0) >= AUTO_MAP_CONFIDENCE_FLOOR:
            return r
    return None


def _residual_to_unmatched_row(line: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Shape a residual/unplaced source line as a HITL ``unmatched`` row (retaining ``raw_path``)."""
    return {
        "id": line["id"],
        "document_label": line["label"],
        "value": line["value"],
        "section_hint": line["statement"],
        "raw_path": line["raw_path"],
        # Carried through so an untagged subtotal shown in source position is excluded from its
        # bucket total (and reconciliation) instead of being double-counted with its components.
        "is_total": bool(line.get("is_total")),
        # Source band (side / currentness / activity) so the HITL suggestion can hard-gate to the
        # right band — e.g. a non-current line never suggests a current slot. See _score().
        "band": line.get("band"),
        "source": reason,
    }


def split_confident_and_residual(
    raw_tree: dict[str, Any],
    schema: dict[str, Any],
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
    other_components: Optional[dict[str, list]] = None,
) -> tuple[dict[str, Any], set[str], list[dict[str, Any]]]:
    """Place only the lines the deterministic gate is confident about; return the rest as residual.

    Returns ``(mapped, placed_paths, residual_lines)``:
    - ``mapped`` — canonical tree with the **authoritative** band-locked placements (always carries
      the three required top-level keys).
    - ``placed_paths`` — set of canonical full paths already filled (so the residual step can't
      collide with them).
    - ``residual_lines`` — source lines (full dicts incl. ``band``/``is_total``/``raw_path`` and a
      stable ``id``) that did not clear the confidence floor, or whose target slot was already taken.
      These are the inputs for the LLM residual-assignment step (or, in deterministic-only mode,
      become ``unmatched`` rows directly).
    """
    mapped: dict[str, Any] = {k: {} for k in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL}
    placed_paths: set[str] = set()
    residual: list[dict[str, Any]] = []

    for idx, line in enumerate(flatten_source_lines(raw_tree)):
        line = {**line, "id": f"det-{idx}"}
        target = _best_band_locked_target(
            line, candidate_index=candidate_index, aliases_index=aliases_index
        )
        if target is None:
            residual.append({**line, "reason": "no_confident_target"})
            continue
        full_path = target["full_path"]
        if full_path in placed_paths or _dotted_get(mapped, full_path) is not None:
            # Slot already taken by an earlier (equal-or-higher-confidence) line — keep this one
            # recoverable instead of overwriting / double-counting.
            residual.append({**line, "reason": "slot_taken"})
            continue
        set_value_at_dotted_path(mapped, full_path, line["value"])
        placed_paths.add(full_path)
        _record_other_component(other_components, full_path, line)

    return mapped, placed_paths, residual


def map_financials_deterministic(
    raw_tree: dict[str, Any],
    schema: dict[str, Any],
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Deterministic-only mapping → ``(mapped, unmatched)`` (no LLM).

    Confident band-locked placements go into ``mapped``; everything else becomes an ``unmatched``
    row. For the hybrid (deterministic-first + LLM-residual) flow use
    :func:`split_confident_and_residual` + :func:`apply_residual_assignments` instead.
    """
    mapped, _placed, residual = split_confident_and_residual(
        raw_tree, schema, candidate_index, aliases_index
    )
    unmatched = [
        _residual_to_unmatched_row(line, reason=f"deterministic_{line['reason']}")
        for line in residual
    ]
    return mapped, unmatched


def build_leaf_catalog(candidate_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat catalog of assignable canonical leaves for the LLM residual-assignment prompt.

    Each entry is ``{path, statement, side, currentness, activity, is_total}`` — ``path`` is the
    canonical full path the LLM must pick verbatim (it doubles as the assignment target id). The
    band fields let the prompt show the model the position of each option; our validation in
    :func:`apply_residual_assignments` is the hard backstop regardless of what the model returns.
    """
    catalog: list[dict[str, Any]] = []
    for c in candidate_index:
        band = c.get("band") or band_for_path(c["full_path"])
        catalog.append(
            {
                "path": c["full_path"],
                "statement": band.get("statement"),
                "side": band.get("side"),
                "currentness": band.get("currentness"),
                "activity": band.get("activity"),
                "is_total": _is_total_canonical_key(c["key"]),
            }
        )
    return catalog


def candidates_for_line(
    line: dict[str, Any], candidate_index: list[dict[str, Any]]
) -> list[str]:
    """Canonical leaf paths admissible for ONE source line, applying the same band + role gating
    the deterministic mapper uses to pre-filter (see :func:`_best_band_locked_target`).

    Lets the LLM residual pass choose from a *squeezed*, positionally-valid universe (e.g. just the
    handful of non-current-asset leaves) instead of the entire schema — the ambiguity that label
    tokens alone cannot resolve. Order follows ``candidate_index``. ``apply_residual_assignments``
    re-validates every pick regardless, so this is a precision aid, not a trust boundary.
    """
    band = line.get("band") or {}
    is_total = bool(line.get("is_total"))
    out: list[str] = []
    for c in candidate_index:
        if not _role_compatible(is_total, c["key"]):
            continue
        if not bands_compatible(band, c.get("band") or band_for_path(c["full_path"])):
            continue
        out.append(c["full_path"])
    return out


def apply_residual_assignments(
    mapped: dict[str, Any],
    placed_paths: set[str],
    residual_lines: list[dict[str, Any]],
    assignments: dict[str, Optional[str]],
    candidate_index: list[dict[str, Any]],
    other_components: Optional[dict[str, list]] = None,
) -> list[dict[str, Any]]:
    """Merge LLM residual assignments into ``mapped`` after **hard** band/role/slot validation.

    ``assignments`` maps ``line_id -> canonical_full_path`` (or ``None`` / ``"unmatched"``). Every
    proposed target is re-checked the same way the deterministic gate checks its own placements —
    the target must (a) exist in the catalog, (b) be band-compatible with the *source* line, (c) be
    role-compatible (total vs component), and (d) point at a still-empty slot. Anything that fails,
    or was left unassigned, becomes an ``unmatched`` row. This guarantees the LLM cannot reintroduce
    a cross-band placement: its freedom is reduced to *which admissible leaf*, never *which band*.

    Mutates ``mapped`` / ``placed_paths`` in place; returns the ``unmatched`` rows.
    """
    valid_leaves = {c["full_path"] for c in candidate_index}
    unmatched: list[dict[str, Any]] = []

    for line in residual_lines:
        target = (assignments or {}).get(line["id"])
        reason: Optional[str] = None
        if not target or target == "unmatched":
            reason = "llm_unmatched"
        elif target not in valid_leaves:
            reason = "llm_target_unknown"
        elif target in placed_paths or _dotted_get(mapped, target) is not None:
            reason = "llm_slot_taken"
        elif not _role_compatible(bool(line.get("is_total")), target.rsplit(".", 1)[-1]):
            reason = "llm_role_conflict"
        elif not bands_compatible(line["band"], band_for_path(target)):
            reason = "llm_band_conflict"

        if reason is None:
            set_value_at_dotted_path(mapped, target, line["value"])
            placed_paths.add(target)
            _record_other_component(other_components, target, line)
        else:
            unmatched.append(_residual_to_unmatched_row(line, reason=reason))

    return unmatched
