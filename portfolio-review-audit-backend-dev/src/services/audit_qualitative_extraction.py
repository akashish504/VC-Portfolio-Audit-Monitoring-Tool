"""
Normalize and validate audit qualitative LLM output (schema v2).

Designed for a single combined pass (opinion + reporting scope + CARO + IFC).
Gold PDF examples can be appended to the prompt later via ``QUALITATIVE_FEW_SHOT_APPENDIX``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_OPINION_TYPES = frozenset({"unmodified", "qualified", "adverse", "disclaimer_of_opinion", "unknown"})
_OPINION_CONFIDENCE = frozenset({"high", "medium", "low"})
_STATEMENT_BASIS = frozenset({"consolidated", "standalone", "both_in_document", "unknown"})
_FINANCIALS_BASIS = frozenset({"consolidated", "standalone", "unclear"})
_ENTITY_COVERAGE = frozenset({"single_entity", "multiple_entities", "group_with_components", "unknown"})
_ENTITY_ROLES = frozenset({"holding", "parent", "subsidiary", "associate", "joint_venture"})
_CARO_ASSESSMENT = frozenset({"clean", "has_highlights", "not_available"})
_IFC_ASSESSMENT = frozenset({"effective", "has_weaknesses", "not_available"})

_QUALITATIVE_SUMMARY_MAX_LEN = 12_000
_PARAGRAPH_MAX_LEN = 16_000
_EXCERPT_MAX_LEN = 2_000
_MAX_CARO_FLAGS = 40
_MAX_IFC_FLAGS = 20

_AUDITOR_TIER_IDS = frozenset({"big_4", "big_6", "big_10", "non_big_10", "unknown"})
_AUDITOR_TIER_LABELS: dict[str, str] = {
    "big_4": "BIG 4",
    "big_6": "BIG 6",
    "big_10": "BIG 10",
    "non_big_10": "Non Big 10",
    "unknown": "Unknown",
}

# Substrings / tokens matched against normalized firm name (first matching tier wins).
_AUDITOR_TIER_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "big_4",
        (
            "ernst and young",
            "ernst young",
            "deloitte",
            "pricewaterhousecoopers",
            "pricewaterhouse",
            "price waterhouse",
            "pwc",
            "kpmg",
            "batliboi",
            "sr batliboi",
            "s r batliboi",
            "bsr and associates",
            "bsr and co",
            "bsr ",
            " ey",
            "ey ",
            " ey ",
        ),
    ),
    (
        "big_6",
        (
            "grant thornton",
            " rsm",
            "rsm ",
            " rsm ",
            " bdo",
            "bdo ",
            " bdo ",
            " gt ",
        ),
    ),
    (
        "big_10",
        (
            "mazars",
            "knav",
            "baker tilly",
            "crowe",
            "nexia",
        ),
    ),
]

# Severity order for reconciling keyword hits vs model label
_OPINION_SEVERITY = {
    "disclaimer_of_opinion": 4,
    "adverse": 3,
    "qualified": 2,
    "unmodified": 1,
    "unknown": 0,
}

# Classify from Opinion + Basis for Opinion only (case-insensitive substring match)
_OPINION_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "disclaimer_of_opinion",
        [
            "we do not express an opinion",
            "unable to obtain sufficient appropriate audit evidence",
            "we were unable to obtain sufficient appropriate audit evidence",
            "disclaimer of opinion",
        ],
    ),
    (
        "adverse",
        [
            "do not present fairly",
            "do not give a true and fair view",
            "does not present fairly",
            "does not give a true and fair view",
            "adverse opinion",
        ],
    ),
    (
        "qualified",
        [
            "qualified opinion",
            "except for",
            "with the exception of",
            "subject to",
        ],
    ),
    (
        "unmodified",
        [
            "unmodified opinion",
            "unqualified opinion",
            "not qualified",
            "clean opinion",
            "present fairly",
            "true and fair view",
            "in accordance with ind as",
            "in accordance with ifrs",
            "in accordance with gaap",
            "in accordance with indian accounting standards",
        ],
    ),
]

_CARO_RED_FLAG_KEYWORDS: list[tuple[str, list[str]]] = [
    ("except_for_discrepancy", ["except for", "material discrepanc"]),
    ("prejudicial", ["prejudicial to the interest"]),
    ("not_regular", ["not regular"]),
    ("overdue", ["overdue", "more than 90 days"]),
    ("diversion_of_funds", ["diversion of funds"]),
    ("in_arrears", ["in arrears", "more than 6 months"]),
    ("adverse_remarks", ["adverse remark"]),
    ("cash_losses", ["cash loss", "cash losses"]),
    ("material_uncertainty", ["material uncertainty"]),
]

_IFC_RED_FLAG_KEYWORDS: list[tuple[str, list[str]]] = [
    ("material_weakness", ["material weakness"]),
    ("significant_deficiency", ["significant deficien"]),
    ("ineffective", ["did not operate effectively", "not operating effectively"]),
    ("inadequate", ["inadequate"]),
    ("override", ["override of controls"]),
    ("misstatement_risk", ["reasonable possibility"]),
]


def _enum_or(raw: Any, allowed: frozenset[str], default: str = "unknown") -> str:
    if not isinstance(raw, str):
        return default
    s = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return s if s in allowed else default


def _string_list(raw: Any, *, max_items: int = 50) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:500])
        if len(out) >= max_items:
            break
    return out


def _trim_text(raw: Any, max_len: int) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s[:max_len] if s else None


def _normalize_firm_for_match(firm: str) -> str:
    s = firm.lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return f" {s} "


def _firm_name_matches_pattern(normalized_firm: str, pattern: str) -> bool:
    p = pattern.lower().replace("&", " and ").strip()
    if not p:
        return False
    if " " in p:
        return p in normalized_firm
    return re.search(rf"(?<!\w){re.escape(p)}(?!\w)", normalized_firm) is not None


def classify_auditor_firm_tier(firm: Optional[str]) -> dict[str, Any]:
    """
    Classify auditor firm into BIG 4 / BIG 6 / BIG 10 / Non Big 10 using product rules.
    Returns auditor_tier, auditor_tier_label, and auditor_tier_match (pattern or firm name).
    """
    if not firm or not firm.strip():
        return {
            "auditor_tier": "unknown",
            "auditor_tier_label": _AUDITOR_TIER_LABELS["unknown"],
            "auditor_tier_match": None,
        }
    normalized = _normalize_firm_for_match(firm)
    for tier_id, patterns in _AUDITOR_TIER_PATTERNS:
        for pattern in patterns:
            if _firm_name_matches_pattern(normalized, pattern):
                return {
                    "auditor_tier": tier_id,
                    "auditor_tier_label": _AUDITOR_TIER_LABELS[tier_id],
                    "auditor_tier_match": pattern.strip(),
                }
    return {
        "auditor_tier": "non_big_10",
        "auditor_tier_label": _AUDITOR_TIER_LABELS["non_big_10"],
        "auditor_tier_match": firm.strip()[:120],
    }


def _find_keyword_phrases(text: str, keyword_groups: list[tuple[str, list[str]]]) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for _theme, phrases in keyword_groups:
        for p in phrases:
            if p in lower and p not in found:
                found.append(p)
    return found[:20]


def _infer_opinion_type_from_text(*parts: Optional[str]) -> tuple[str, list[str]]:
    combined = " ".join(p for p in parts if isinstance(p, str) and p.strip()).lower()
    if not combined.strip():
        return "unknown", []
    hits: list[tuple[str, str]] = []
    for otype, phrases in _OPINION_KEYWORDS:
        for p in phrases:
            if p in combined:
                hits.append((otype, p))
    if not hits:
        return "unknown", []
    # Most severe type wins
    best = max(hits, key=lambda h: _OPINION_SEVERITY.get(h[0], 0))
    phrases = sorted({h[1] for h in hits if h[0] == best[0]})
    return best[0], phrases


def _reconcile_opinion_type(model_type: str, inferred: str, phrases: list[str]) -> tuple[str, str]:
    """Return (final_type, confidence). Downgrade confidence when model and keywords disagree."""
    if model_type == "unknown" and inferred != "unknown":
        return inferred, "medium"
    if inferred == "unknown":
        return model_type, "high" if model_type != "unknown" else "low"
    if model_type == inferred:
        return model_type, "high"
    # Keyword more severe than model → prefer keyword, lower confidence
    if _OPINION_SEVERITY.get(inferred, 0) > _OPINION_SEVERITY.get(model_type, 0):
        return inferred, "low"
    # Model more severe
    if _OPINION_SEVERITY.get(model_type, 0) > _OPINION_SEVERITY.get(inferred, 0):
        return model_type, "medium"
    return model_type, "medium"


def _normalize_report_paragraph(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"present": False, "text": None}
    text = _trim_text(raw.get("text"), _PARAGRAPH_MAX_LEN)
    present = raw.get("present") is True or bool(text)
    return {"present": present, "text": text if present else None}


def _normalize_other_report_paragraphs(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "emphasis_of_matter": _normalize_report_paragraph(raw.get("emphasis_of_matter")),
        "other_matters": _normalize_report_paragraph(raw.get("other_matters")),
        "going_concern_material_uncertainty": _normalize_report_paragraph(
            raw.get("going_concern_material_uncertainty")
        ),
    }


def normalize_auditor_opinion(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    if raw.get("opinion_available") is False or raw.get("available") is False:
        return None

    opinion_para = _trim_text(raw.get("opinion_paragraph"), _PARAGRAPH_MAX_LEN)
    legacy_text = _trim_text(raw.get("text"), _PARAGRAPH_MAX_LEN)
    text = opinion_para or legacy_text
    if not text:
        return None

    basis = _trim_text(raw.get("basis_for_opinion"), _PARAGRAPH_MAX_LEN)
    model_type = _enum_or(raw.get("opinion_type"), _OPINION_TYPES, "unknown")
    model_phrases = _string_list(raw.get("classification_phrases"))
    inferred_type, inferred_phrases = _infer_opinion_type_from_text(text, basis)
    all_phrases = list(dict.fromkeys(model_phrases + inferred_phrases))
    final_type, confidence = _reconcile_opinion_type(model_type, inferred_type, all_phrases)

    lang = raw.get("language")
    language = "en" if isinstance(lang, str) and lang.strip().lower().startswith("en") else "en"

    return {
        "version": 2,
        "language": language,
        "opinion_type": final_type,
        "opinion_type_confidence": _enum_or(raw.get("opinion_type_confidence"), _OPINION_CONFIDENCE, confidence),
        "classification_phrases": all_phrases[:20],
        "entities_mentioned": _string_list(raw.get("entities_mentioned")),
        "text": text,
        "basis_for_opinion": basis,
        "other_report_paragraphs": _normalize_other_report_paragraphs(raw.get("other_report_paragraphs")),
    }


def _normalize_red_flags(raw: Any, *, max_flags: int, keyword_groups: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:max_flags]:
        if not isinstance(item, dict):
            continue
        theme_raw = item.get("theme")
        theme = (
            theme_raw.strip()[:64]
            if isinstance(theme_raw, str) and theme_raw.strip()
            else "other"
        )
        excerpt = _trim_text(item.get("excerpt"), _EXCERPT_MAX_LEN)
        matched = _string_list(item.get("matched_keywords"), max_items=10)
        if excerpt and not matched:
            matched = _find_keyword_phrases(excerpt, keyword_groups)[:10]
        clause_ref = _trim_text(item.get("clause_ref"), 64)
        entry: dict[str, Any] = {"theme": theme, "matched_keywords": matched, "excerpt": excerpt}
        if clause_ref:
            entry["clause_ref"] = clause_ref
        if excerpt or matched:
            out.append(entry)
    return out


def _normalize_caro(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"available": False, "overall_assessment": "not_available", "red_flags": []}
    available = raw.get("available") is True
    if not available:
        return {"available": False, "overall_assessment": "not_available", "red_flags": []}

    red_flags = _normalize_red_flags(raw.get("red_flags"), max_flags=_MAX_CARO_FLAGS, keyword_groups=_CARO_RED_FLAG_KEYWORDS)
    assessment = _enum_or(raw.get("overall_assessment"), _CARO_ASSESSMENT, "clean")
    if red_flags and assessment == "clean":
        assessment = "has_highlights"
    if not red_flags and assessment == "has_highlights":
        summary = _trim_text(raw.get("summary"), 2000) or ""
        if _find_keyword_phrases(summary, _CARO_RED_FLAG_KEYWORDS):
            assessment = "has_highlights"
        else:
            assessment = "clean"

    result: dict[str, Any] = {
        "available": True,
        "overall_assessment": assessment,
        "red_flags": red_flags,
    }
    title = _trim_text(raw.get("title"), 500)
    if title:
        result["title"] = title
    summary = _trim_text(raw.get("summary"), 4000)
    if summary:
        result["summary"] = summary
    return result


def _normalize_ifc(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"available": False, "overall_assessment": "not_available", "red_flags": []}
    available = raw.get("available") is True
    if not available:
        return {"available": False, "overall_assessment": "not_available", "red_flags": []}

    red_flags = _normalize_red_flags(raw.get("red_flags"), max_flags=_MAX_IFC_FLAGS, keyword_groups=_IFC_RED_FLAG_KEYWORDS)
    assessment = _enum_or(raw.get("overall_assessment"), _IFC_ASSESSMENT, "effective")
    if red_flags and assessment == "effective":
        assessment = "has_weaknesses"
    if not red_flags and assessment == "has_weaknesses":
        summary = _trim_text(raw.get("summary"), 2000) or ""
        if _find_keyword_phrases(summary, _IFC_RED_FLAG_KEYWORDS):
            assessment = "has_weaknesses"
        else:
            assessment = "effective"

    result: dict[str, Any] = {
        "available": True,
        "overall_assessment": assessment,
        "red_flags": red_flags,
    }
    title = _trim_text(raw.get("title"), 500)
    if title:
        result["title"] = title
    summary = _trim_text(raw.get("summary"), 4000)
    if summary:
        result["summary"] = summary
    return result


def normalize_reporting_scope(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    roles: list[str] = []
    for r in _string_list(raw.get("entity_roles")):
        norm = r.lower().replace(" ", "_").replace("-", "_")
        if norm in _ENTITY_ROLES:
            roles.append(norm)
    notes = _trim_text(raw.get("evidence_notes"), 1000)
    return {
        "statement_basis": _enum_or(raw.get("statement_basis"), _STATEMENT_BASIS),
        "financials_basis": _enum_or(raw.get("financials_basis"), _FINANCIALS_BASIS, "unclear"),
        "entity_coverage": _enum_or(raw.get("entity_coverage"), _ENTITY_COVERAGE),
        "entity_roles": roles,
        "entities_named": _string_list(raw.get("entities_named")),
        "evidence_notes": notes,
    }


def _normalize_signing_date(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return None


def normalize_auditor_engagement(raw: Any, *, caro: dict[str, Any], ifc: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        raw = {}
    summary = _trim_text(raw.get("summary"), _QUALITATIVE_SUMMARY_MAX_LEN)
    firm = _trim_text(raw.get("auditor_firm"), 500)
    signing_partner_or_team = _trim_text(raw.get("signing_partner_or_team"), 500)
    signing_date = _normalize_signing_date(raw.get("signing_date"))
    signing_date_raw = _trim_text(raw.get("signing_date_raw"), 128)

    sp = raw.get("sections_present")
    sections = {
        "independent_auditors_report": False,
        "caro": caro.get("available") is True,
        "ifc": ifc.get("available") is True,
    }
    if isinstance(sp, dict):
        for key in ("independent_auditors_report", "caro", "ifc"):
            if sp.get(key) is True:
                sections[key] = True

    if (
        not summary
        and not firm
        and not signing_partner_or_team
        and not signing_date
        and not signing_date_raw
        and not any(sections.values())
    ):
        return None

    if not summary:
        parts: list[str] = []
        if sections["independent_auditors_report"]:
            parts.append("Independent Auditor's Report present.")
        if sections["caro"]:
            parts.append(f"CARO present ({caro.get('overall_assessment', 'unknown')}).")
        if sections["ifc"]:
            parts.append(f"IFC report present ({ifc.get('overall_assessment', 'unknown')}).")
        if firm:
            tier_lbl = classify_auditor_firm_tier(firm)["auditor_tier_label"]
            parts.append(f"Auditor: {firm} ({tier_lbl}).")
        if signing_partner_or_team:
            parts.append(f"Signing partner/team: {signing_partner_or_team}.")
        if signing_date or signing_date_raw:
            parts.append(f"Signed: {signing_date or signing_date_raw}.")
        summary = " ".join(parts)[:_QUALITATIVE_SUMMARY_MAX_LEN] if parts else None

    if not summary and not (firm or signing_partner_or_team or signing_date or signing_date_raw):
        return None

    tier_info = classify_auditor_firm_tier(firm)
    result: dict[str, Any] = {
        "available": True,
        "summary": summary,
        "auditor_firm": firm,
        "signing_partner_or_team": signing_partner_or_team,
        "signing_date": signing_date,
        "signing_date_raw": signing_date_raw,
        "sections_present": sections,
        **tier_info,
    }
    return result


def split_combined_qualitative_response(raw: Any) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return None, None

    opinion_src = raw.get("opinion")
    if isinstance(opinion_src, dict) and opinion_src.get("available") is False:
        opinion = None
    elif isinstance(opinion_src, dict):
        opinion = normalize_auditor_opinion(opinion_src)
    else:
        opinion = normalize_auditor_opinion(raw)

    reporting_scope = normalize_reporting_scope(raw.get("reporting_scope"))
    caro = _normalize_caro(raw.get("caro"))
    ifc = _normalize_ifc(raw.get("ifc"))
    engagement = normalize_auditor_engagement(raw.get("auditor_engagement"), caro=caro, ifc=ifc)

    has_scope = any(
        reporting_scope.get(k) not in (None, [], "unknown", "unclear")
        for k in ("entities_named", "entity_roles", "evidence_notes")
    ) or reporting_scope.get("statement_basis") != "unknown" or reporting_scope.get("entity_coverage") != "unknown"

    has_caro_ifc = caro.get("available") or ifc.get("available")
    has_engagement = engagement is not None

    if not (has_scope or has_caro_ifc or has_engagement):
        return opinion, None

    qualitative: dict[str, Any] = {
        "version": 2,
        "reporting_scope": reporting_scope,
        "caro": caro,
        "ifc": ifc,
    }
    if engagement:
        qualitative["auditor_engagement"] = engagement
    return opinion, qualitative
