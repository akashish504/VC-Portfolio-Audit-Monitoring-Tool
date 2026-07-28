"""
Deterministic label → canonical-path matching + learned alias memory (PR1 of the
"reduce schema-mapping misses" work).

Two jobs:

1. **Suggestions** — given a document line label (e.g. ``"Net Sales"``) plus an optional
   statement hint, propose the single best canonical leaf path it likely belongs to
   (``profit_and_loss.revenue.revenue_from_operations``) with a confidence + rationale,
   so an unmatched row becomes one-click attachable in the HITL UI. Pure Python; no LLM.

2. **Alias memory (suggest tier)** — every time a reviewer attaches an unmatched line to a
   canonical path, we capture ``normalized_label → {parent_path, key}`` in a ``ConfigTable``
   blob (``audit_financials_label_aliases_v1``). Future suggestions resolve that wording
   deterministically (confidence ≈ 1.0) instead of re-guessing. Write-only for now —
   auto-apply (PR2) reads ``count`` to gate. The blob is plain JSON, editable in settings,
   mirroring ``audit_financials_schema_v1`` / ``financial_extraction_metric_mapping_v1``.

Suggestions are a *backstop*: they only ever propose. Nothing here mutates the extracted
tree or the six reconciled metrics on its own.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConfigTable
from src.llm.prompts import AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL
from src.services.financial_audit_schema import list_audit_financials_numeric_leaf_paths
from src.services.financial_field_definitions import band_for_path, synonyms_for_path

logger = logging.getLogger(__name__)

AUDIT_FINANCIALS_LABEL_ALIASES_CONFIG_KEY = "audit_financials_label_aliases_v1"

# Minimum match score before we surface a suggestion at all. Below this the reviewer
# sees the row with no pre-filled target (they still get the full parent-path picker).
SUGGESTION_FLOOR = 0.45
# At/above this a single surface-form token match is treated as a near-certain hit.
SUGGESTION_STRONG = 0.85

# A learned alias must be reviewer-confirmed at least this many times before it is surfaced
# as a confident (0.99) suggestion. Set to 1 so a single reviewer-confirmed attach immediately
# teaches the mapping (fastest learning; note: with 1, a stray/mistaken attach can also teach).
ALIAS_SUGGEST_MIN_COUNT = 1

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# Deliberately tiny stoplist: in financials "net", "total", "current", "non" are
# *meaningful* (total_assets ≠ assets; current vs non-current), so we keep them.
_STOPWORDS = frozenset({"and", "or", "the", "of", "for", "to", "in", "on", "a", "an", "from", "with"})

def normalize_label(label: Any) -> str:
    """Lowercase, collapse punctuation/whitespace to single spaces. Conservative on tokens."""
    toks = [t for t in _TOKEN_SPLIT.split(str(label or "").lower()) if t]
    return " ".join(toks)


def _tokens(label: Any) -> frozenset[str]:
    return frozenset(t for t in _TOKEN_SPLIT.split(str(label or "").lower()) if t and t not in _STOPWORDS)


# --- candidate index (canonical leaf targets) ---------------------------------------------


def build_candidate_index(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per canonical numeric leaf path, with its matchable surface-form token sets."""
    out: list[dict[str, Any]] = []
    for full_path in list_audit_financials_numeric_leaf_paths(schema):
        parts = full_path.split(".")
        if len(parts) < 2:
            continue
        key = parts[-1]
        surfaces = {key.replace("_", " ")}
        surfaces.update(synonyms_for_path(full_path))
        token_sets = [ts for ts in (_tokens(s) for s in surfaces) if ts]
        if not token_sets:
            continue
        out.append(
            {
                "full_path": full_path,
                "parent_path": ".".join(parts[:-1]),
                "key": key,
                "section": parts[0],
                "token_sets": token_sets,
                # Positional band (statement/side/currentness/activity) so callers can hard-lock
                # placement to the right band; see ``financial_field_definitions.bands_compatible``.
                "band": band_for_path(full_path),
            }
        )
    return out


def _band_conflict(row_band: Optional[dict[str, Any]], target_band: Optional[dict[str, Any]]) -> bool:
    """True when the row and target positively contradict on a *finer* band dimension — balance-sheet
    side (assets/liabilities), currentness (current/non_current), or cash-flow activity. Statement is
    handled separately by the softer wrong-statement penalty, so it is excluded here. Unknown on
    either side is permissive (never a conflict), so band-locking never turns a match into a miss for
    lack of context — it only blocks a positive cross-band contradiction."""
    if not isinstance(row_band, dict) or not isinstance(target_band, dict):
        return False
    for dim in ("side", "currentness", "activity"):
        a, b = row_band.get(dim), target_band.get(dim)
        if a and b and a != b:
            return True
    return False


def _score(
    label_tokens: frozenset[str],
    cand: dict[str, Any],
    *,
    section_hint: Optional[str],
    band: Optional[dict[str, Any]] = None,
) -> float:
    if not label_tokens:
        return 0.0
    best = 0.0
    for cs in cand["token_sets"]:
        inter = len(label_tokens & cs)
        if inter == 0:
            continue
        if label_tokens == cs:
            sub = 1.0
        else:
            jacc = inter / len(label_tokens | cs)
            contain = inter / min(len(label_tokens), len(cs))
            sub = 0.5 * jacc + 0.5 * contain
        if sub > best:
            best = sub
    # Wrong-statement penalty: a balance-sheet line should not match a P&L target etc.
    if section_hint in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL and cand["section"] != section_hint:
        best *= 0.35
    # Hard band gate: a line whose source position is (e.g.) non-current must never be suggested into
    # a current slot of the same label — the very ambiguity (borrowings, trade receivables, financial
    # assets, …) that label tokens alone cannot resolve. Mirrors the auto-mapper's band lock.
    if _band_conflict(band, cand.get("band")):
        return 0.0
    return best


# --- alias memory -------------------------------------------------------------------------


def _alias_records(stored: Any) -> list[dict[str, Any]]:
    if not isinstance(stored, dict):
        return []
    recs = stored.get("aliases")
    return [r for r in recs if isinstance(r, dict)] if isinstance(recs, list) else []


def build_aliases_index(stored: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """``(section_or_any, normalized_label) -> {parent_path, key, full_path, count}``."""
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for r in _alias_records(stored):
        norm = normalize_label(r.get("label_normalized") or r.get("document_label") or "")
        pp = r.get("parent_path")
        key = r.get("key")
        if not norm or not pp or not key:
            continue
        section = str(r.get("section_hint") or "any")
        idx[(section, norm)] = {
            "parent_path": pp,
            "key": key,
            "full_path": f"{pp}.{key}",
            "count": int(r.get("count") or 1),
        }
    return idx


def _alias_lookup(
    idx: dict[tuple[str, str], dict[str, Any]], norm: str, section_hint: Optional[str]
) -> Optional[dict[str, Any]]:
    if not norm:
        return None
    if section_hint:
        hit = idx.get((section_hint, norm))
        if hit:
            return hit
    hit = idx.get(("any", norm))
    if hit:
        return hit
    for key_tuple, v in idx.items():
        if key_tuple[1] == norm:
            return v
    return None


async def load_label_aliases(db: AsyncSession) -> dict[str, Any]:
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == AUDIT_FINANCIALS_LABEL_ALIASES_CONFIG_KEY)))
        .scalars()
        .first()
    )
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def prepare_label_matcher(
    db: AsyncSession, schema: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Load alias config + build the candidate index. Convenience for callers that enrich rows."""
    stored = await load_label_aliases(db)
    return build_candidate_index(schema), build_aliases_index(stored)


async def upsert_label_alias(
    db: AsyncSession,
    *,
    document_label: str,
    section_hint: Optional[str],
    parent_path: str,
    key: str,
    actor: Optional[str],
) -> None:
    """Capture a reviewer-confirmed ``label → parent_path.key`` mapping (suggest tier).

    Idempotent per (normalized label, section, target): bumps ``count`` and appends the raw
    example. Best-effort — callers should swallow failures so a learning write never breaks
    the user's map action.
    """
    norm = normalize_label(document_label)
    if not norm or not parent_path or not key:
        return
    section = section_hint if section_hint in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL else "any"

    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == AUDIT_FINANCIALS_LABEL_ALIASES_CONFIG_KEY)))
        .scalars()
        .first()
    )
    value = dict(row.value) if row and isinstance(row.value, dict) else {}
    recs = _alias_records(value)

    found: Optional[dict[str, Any]] = None
    for r in recs:
        if (
            normalize_label(r.get("label_normalized") or r.get("document_label") or "") == norm
            and str(r.get("section_hint") or "any") == section
            and r.get("parent_path") == parent_path
            and r.get("key") == key
        ):
            found = r
            break

    if found is not None:
        found["count"] = int(found.get("count") or 1) + 1
        examples = [e for e in (found.get("examples") or []) if isinstance(e, str)]
        if document_label and document_label not in examples:
            examples.append(document_label)
        found["examples"] = examples[-10:]
        if actor:
            found["updated_by"] = actor
    else:
        recs.append(
            {
                "label_normalized": norm,
                "document_label": document_label,
                "section_hint": section,
                "parent_path": parent_path,
                "key": key,
                "full_path": f"{parent_path}.{key}",
                "count": 1,
                "examples": [document_label] if document_label else [],
                "updated_by": actor,
            }
        )

    value["aliases"] = recs[-2000:]
    if row is None:
        row = ConfigTable(
            key=AUDIT_FINANCIALS_LABEL_ALIASES_CONFIG_KEY,
            value=value,
            description="Learned audit-financials label → canonical path aliases (HITL-confirmed; suggest tier)",
        )
        db.add(row)
    else:
        row.value = value
    await db.flush()


# --- suggestion API -----------------------------------------------------------------------


def suggest_candidates(
    document_label: str,
    *,
    section_hint: Optional[str],
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
    band: Optional[dict[str, Any]] = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Ranked canonical targets for a label. Alias hit (if any) leads; then token matches.

    ``band`` is the row's source band (side / currentness / activity); when given, a target whose
    band positively contradicts it is dropped, so an ambiguous label resolves to the right band.
    """
    norm = normalize_label(document_label)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    alias = _alias_lookup(aliases_index, norm, section_hint)
    if alias:
        # Self-learning guards:
        #  (1) require >= ALIAS_SUGGEST_MIN_COUNT reviewer confirmations before surfacing a
        #      confident hit, so a single (possibly mistaken) attach can't teach a 0.99 suggestion.
        #  (2) enforce the same wrong-statement guard the token path applies — never surface a
        #      learned alias whose target sits in a different statement than the row's section hint.
        #  (3) enforce the same finer band gate — a learned alias must not cross current/non-current
        #      (etc.) against the row's own source position.
        alias_section = str(alias["parent_path"]).split(".")[0]
        section_conflict = (
            section_hint in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL and alias_section != section_hint
        )
        band_conflict = _band_conflict(band, band_for_path(alias["full_path"]))
        if int(alias.get("count") or 1) >= ALIAS_SUGGEST_MIN_COUNT and not section_conflict and not band_conflict:
            results.append(
                {
                    "parent_path": alias["parent_path"],
                    "key": alias["key"],
                    "full_path": alias["full_path"],
                    "confidence": 0.99,
                    "source": "alias",
                    "rationale": "Previously mapped here by reviewers",
                }
            )
            seen.add(alias["full_path"])

    label_tokens = _tokens(document_label)
    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in candidate_index:
        s = _score(label_tokens, cand, section_hint=section_hint, band=band)
        if s >= SUGGESTION_FLOOR:
            scored.append((s, cand))
    scored.sort(key=lambda x: x[0], reverse=True)

    for s, cand in scored:
        if cand["full_path"] in seen:
            continue
        seen.add(cand["full_path"])
        results.append(
            {
                "parent_path": cand["parent_path"],
                "key": cand["key"],
                "full_path": cand["full_path"],
                "confidence": round(min(s, 0.95), 2),
                "source": "canonical_label" if s >= SUGGESTION_STRONG else "token_overlap",
                "rationale": f"Label tokens match canonical field “{cand['key'].replace('_', ' ')}”",
            }
        )
        if len(results) >= max(top_k, 1):
            break

    return results


def build_suggestion(
    document_label: str,
    *,
    section_hint: Optional[str],
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
    band: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Best target for a label, with the ranked alternates attached as ``candidates``."""
    cands = suggest_candidates(
        document_label,
        section_hint=section_hint,
        candidate_index=candidate_index,
        aliases_index=aliases_index,
        band=band,
    )
    if not cands:
        return None
    best = dict(cands[0])
    best["candidates"] = cands
    return best


# --- field-level roll-up (lead-schedule model) --------------------------------------------
#
# When several distinct document lines map to one canonical leaf, the financially-correct
# behaviour is to accumulate them as a signed roll-up (line = Σ components × sign), preserving
# the breakdown — never overwrite. These helpers infer sign and flag the double-counting trap
# (a stated total mapped on top of its own components).

# Labels that signal a deduction (negative contribution) within a roll-up.
_DEDUCTION_HINTS: tuple[str, ...] = (
    "less:",
    "less ",
    "net of",
    "(net of",
    "returns",
    "rebate",
    "discount",
    "deduction",
    "elimination",
    "eliminations",
    "reversal",
    "written back",
    "write back",
    "refund",
)

# Labels that signal a subtotal/total line (must not be summed with its own components).
_TOTAL_HINTS: tuple[str, ...] = (
    "total",
    "sub-total",
    "subtotal",
    "sub total",
    "aggregate",
    "grand total",
)


def infer_component_sign(label: Any) -> str:
    """Best-effort ``+``/``-`` for a roll-up component from its label. Default ``+``."""
    low = str(label or "").lower()
    return "-" if any(h in low for h in _DEDUCTION_HINTS) else "+"


def signed_components_total(components: list[Any] | None) -> float:
    """Σ ``value × sign`` over a components list (ignoring non-numeric entries)."""
    total = 0.0
    for c in components or []:
        if not isinstance(c, dict):
            continue
        v = c.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += float(v) * (-1.0 if c.get("sign") == "-" else 1.0)
    return total


def looks_like_total(
    label: Any, new_value: Any, existing_components: list[Any] | None
) -> Optional[str]:
    """Return a double-count warning when ``label``/``new_value`` looks like the TOTAL of the
    lines already mapped here (so the reviewer can choose "use as total" over "add"). Else None.
    """
    low = str(label or "").lower()
    labelled_total = any(h in low for h in _TOTAL_HINTS)
    n = len(existing_components or [])
    near = False
    if existing_components and isinstance(new_value, (int, float)) and not isinstance(new_value, bool):
        s = signed_components_total(existing_components)
        denom = max(abs(s), abs(float(new_value)), 1.0)
        near = abs(abs(float(new_value)) - abs(s)) <= 0.005 * denom  # within 0.5%
    if labelled_total and near:
        return (
            f"“{label}” is labelled as a total and ≈ the sum of the {n} line(s) already mapped "
            f"here — adding it would double-count. Use it as the total instead?"
        )
    if labelled_total:
        return (
            f"“{label}” looks like a total/subtotal line — adding it may double-count the "
            f"component(s) already mapped here."
        )
    if near:
        return (
            f"“{label}” ≈ the sum of the line(s) already mapped here — it may be their total "
            f"rather than another component."
        )
    return None


def enrich_unmatched_rows(
    rows: list[Any],
    *,
    candidate_index: list[dict[str, Any]],
    aliases_index: dict[tuple[str, str], dict[str, Any]],
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Attach a ``suggestion`` to each unmatched row (idempotent unless ``overwrite``)."""
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        if overwrite or not isinstance(row.get("suggestion"), dict):
            label = str(row.get("document_label") or row.get("key") or "")
            sh_raw = row.get("section_hint")
            sh = sh_raw if isinstance(sh_raw, str) and sh_raw in AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL else None
            # Source band: prefer the band carried from extraction; else derive it from the row's
            # source position (raw_path / dedup origin) so the suggestion can't cross current ↔
            # non-current (etc.) for a label that exists under both bands.
            row_band = row.get("band")
            if not isinstance(row_band, dict):
                rp = row.get("raw_path") or row.get("_dedup_of")
                row_band = band_for_path(rp) if isinstance(rp, str) and rp else None
            sug = build_suggestion(
                label,
                section_hint=sh,
                candidate_index=candidate_index,
                aliases_index=aliases_index,
                band=row_band,
            )
            if sug:
                row["suggestion"] = sug
        out.append(row)
    return out
