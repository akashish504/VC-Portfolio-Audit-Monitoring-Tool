"""
Standalone runner for the band-aware **hybrid** pass-2 mapper (no web server, no DB required).

It executes the exact production logic against a raw extracted JSON you provide:

  1. deterministic band-locked split  → confident placements (authoritative) + residual lines
  2. constrained LLM residual assignment (your configured provider; OpenAI locally)
  3. band/role re-validation + merge   → final ``mapped`` + ``unmatched``

Usage
-----
    # full hybrid (deterministic + LLM residual), prints a report and writes <input>.mapped.json
    PYTHONPATH=. .venv/bin/python scripts/run_hybrid_mapping.py --input raw.json

    # deterministic only — no LLM call at all (fully offline)
    PYTHONPATH=. .venv/bin/python scripts/run_hybrid_mapping.py --input raw.json --no-llm

    # custom canonical schema + explicit output path + production finalize shaping
    PYTHONPATH=. .venv/bin/python scripts/run_hybrid_mapping.py \
        --input raw.json --schema schema.json --out result.json --finalize

Input shape
-----------
``--input`` is the pass-1 raw extracted tree (top-level keys like ``profit_and_loss`` /
``balance_sheet`` / ``cash_flow_statement`` / ``statement_of_financial_position`` …). If you pass a
full extraction-metadata blob, the runner unwraps a ``raw_extracted`` / ``extracted`` / ``meta`` key
automatically.

Notes
-----
* Learned aliases are NOT loaded here (they live in the DB) — placement uses curated synonyms only,
  so this is a faithful but slightly conservative view of production.
* The model is whatever ``OPENAI_MODEL`` resolves to in your ``.env``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env into os.environ BEFORE importing settings-bound modules (Settings reads env at import).
REPO_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:  # pragma: no cover - dotenv optional
    pass

from src.configs.env import settings  # noqa: E402
from src.llm.config import resolve_llm_provider  # noqa: E402
from src.llm.prompts import AUDIT_FINANCIALS_SCHEMA_FALLBACK  # noqa: E402
from src.services.financial_audit_schema import finalize_audit_financials_extracted  # noqa: E402
from src.services.financial_deterministic_mapping import (  # noqa: E402
    apply_residual_assignments,
    flatten_source_lines,
    split_confident_and_residual,
)
from src.services.financial_label_matching import (  # noqa: E402
    ALIAS_SUGGEST_MIN_COUNT,
    build_aliases_index,
    build_candidate_index,
    normalize_label,
)

# Local, file-backed mirror of the production ``audit_financials_label_aliases_v1`` ConfigTable blob.
# The runner has no DB, so learned aliases live in this JSON during local testing. Same record shape
# as ``upsert_label_alias`` writes, so ``build_aliases_index`` consumes it identically to production.
DEFAULT_ALIAS_STORE = REPO_ROOT / "scripts" / ".local_label_aliases.json"


def _unwrap_raw(obj: dict) -> dict:
    """Accept either a bare raw tree or a wrapper blob and return the raw extracted tree."""
    if not isinstance(obj, dict):
        raise SystemExit("Input JSON must be an object.")
    for key in ("raw_extracted", "extracted"):
        if isinstance(obj.get(key), dict):
            return obj[key]
    meta = obj.get("meta")
    if isinstance(meta, dict):
        for key in ("raw_extracted", "extracted"):
            if isinstance(meta.get(key), dict):
                return meta[key]
    return obj


def _resolved_model() -> str:
    provider = resolve_llm_provider(settings)
    if provider == "openai":
        return f"openai:{settings.OPENAI_MODEL}"
    return f"bedrock:{settings.BEDROCK_MODEL_ID}"


def _load_alias_store(path: Path) -> dict:
    """Load the file-backed alias blob (``{"aliases": [...]}``); empty if absent."""
    if path.exists():
        try:
            blob = json.loads(path.read_text())
            if isinstance(blob, dict):
                return blob
        except Exception:
            pass
    return {"aliases": []}


def _teach_aliases_from_assignments(
    store: dict,
    residual_lines: list[dict],
    assignments: dict,
    *,
    bump_to: int,
) -> int:
    """Record confirmations for residual lines the LLM placed, mirroring ``upsert_label_alias``.

    Writes one record per (normalized label, section, target). ``bump_to`` sets ``count`` to the
    confirmation threshold so the learned alias is immediately surfaced at 0.99 on the next run —
    this simulates "a reviewer confirmed this enough times" for the demo. In production the count
    grows organically from real HITL attaches.
    """
    recs = [r for r in store.get("aliases", []) if isinstance(r, dict)]
    by_id = {ln["id"]: ln for ln in residual_lines}
    learned = 0
    for line_id, target in (assignments or {}).items():
        if not target or target == "unmatched" or "." not in target:
            continue
        line = by_id.get(line_id)
        if not line:
            continue
        parent_path, key = target.rsplit(".", 1)
        norm = normalize_label(line["label"])
        section = (line.get("band") or {}).get("statement") or "any"
        existing = next(
            (
                r
                for r in recs
                if normalize_label(r.get("label_normalized") or r.get("document_label") or "") == norm
                and str(r.get("section_hint") or "any") == section
                and r.get("parent_path") == parent_path
                and r.get("key") == key
            ),
            None,
        )
        if existing is not None:
            existing["count"] = max(int(existing.get("count") or 1), bump_to)
        else:
            recs.append(
                {
                    "label_normalized": norm,
                    "document_label": line["label"],
                    "section_hint": section,
                    "parent_path": parent_path,
                    "key": key,
                    "full_path": target,
                    "count": bump_to,
                    "examples": [line["label"]],
                    "updated_by": "run_hybrid_mapping --teach",
                }
            )
        learned += 1
    store["aliases"] = recs
    return learned


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the hybrid pass-2 mapper on a raw extracted JSON.")
    ap.add_argument("--input", required=True, help="Path to the pass-1 raw extracted JSON.")
    ap.add_argument("--schema", help="Optional canonical schema JSON (defaults to the built-in fallback).")
    ap.add_argument("--out", help="Where to write the result JSON (default: <input>.mapped.json).")
    ap.add_argument("--no-llm", action="store_true", help="Deterministic only — skip the LLM residual step.")
    ap.add_argument("--finalize", action="store_true", help="Apply finalize_audit_financials_extracted shaping.")
    ap.add_argument(
        "--aliases",
        nargs="?",
        const=str(DEFAULT_ALIAS_STORE),
        help="Use a file-backed alias store (learned label→path memory). "
        f"Bare flag uses {DEFAULT_ALIAS_STORE.name}; or pass a path.",
    )
    ap.add_argument(
        "--teach",
        action="store_true",
        help="After the LLM places residuals, record them as confirmed aliases in the --aliases store "
        "so the next run places them deterministically (count bumped to the confirm threshold).",
    )
    args = ap.parse_args()

    raw = _unwrap_raw(json.loads(Path(args.input).read_text()))
    schema = json.loads(Path(args.schema).read_text()) if args.schema else AUDIT_FINANCIALS_SCHEMA_FALLBACK

    candidate_index = build_candidate_index(schema)
    alias_store_path = Path(args.aliases) if args.aliases else None
    alias_store = _load_alias_store(alias_store_path) if alias_store_path else {"aliases": []}
    aliases_index: dict = build_aliases_index(alias_store)
    if alias_store_path:
        print(f"alias store: {alias_store_path}  ({len(aliases_index)} learned alias(es))")

    all_lines = flatten_source_lines(raw)
    mapped, placed_paths, residual = split_confident_and_residual(
        raw, schema, candidate_index, aliases_index
    )
    confident_paths = set(placed_paths)

    assignments: dict = {}
    used_llm = False
    if residual and not args.no_llm:
        from src.services.audit_document_extraction import _assign_residual_with_llm

        print(f"Calling {_resolved_model()} to assign {len(residual)} residual line(s)…", flush=True)
        assignments = asyncio.run(
            _assign_residual_with_llm(residual_lines=residual, candidate_index=candidate_index)
        )
        used_llm = True

    unmatched = apply_residual_assignments(
        mapped, placed_paths, residual, assignments, candidate_index
    )

    if args.teach:
        if not alias_store_path:
            raise SystemExit("--teach requires --aliases <path> to write to.")
        unmatched_ids = {u["id"] for u in unmatched}
        accepted = {lid: t for lid, t in assignments.items() if lid not in unmatched_ids}
        learned = _teach_aliases_from_assignments(
            alias_store, residual, accepted, bump_to=ALIAS_SUGGEST_MIN_COUNT
        )
        alias_store_path.write_text(json.dumps(alias_store, indent=2, ensure_ascii=False))
        print(f"\nTaught {learned} alias(es) → {alias_store_path}")

    if args.finalize:
        mapped = finalize_audit_financials_extracted(mapped, schema)

    # ---- report -------------------------------------------------------------------------
    unmatched_by_id = {u["id"]: u for u in unmatched}
    llm_accepted = [r for r in residual if r["id"] not in unmatched_by_id]
    reasons: dict[str, int] = {}
    for u in unmatched:
        reasons[u["source"]] = reasons.get(u["source"], 0) + 1

    print("\n" + "=" * 78)
    print("HYBRID MAPPER REPORT")
    print("=" * 78)
    print(f"provider/model         : {_resolved_model()}  (LLM residual: {'yes' if used_llm else 'no'})")
    print(f"source numeric lines   : {len(all_lines)}")
    print(f"deterministic placed   : {len(confident_paths)}  (band-locked, authoritative)")
    print(f"residual → LLM         : {len(residual)}")
    print(f"  LLM accepted (valid) : {len(llm_accepted)}")
    print(f"  unmatched (HITL)     : {len(unmatched)}  {reasons if reasons else ''}")

    if residual:
        print("\n--- residual resolution (label  [band]  value → target) ---")
        for r in residual:
            band = r.get("band") or {}
            band_str = "/".join(
                str(band.get(k) or "-") for k in ("statement", "side", "currentness", "activity")
            )
            target = assignments.get(r["id"])
            if r["id"] in unmatched_by_id:
                verdict = f"UNMATCHED ({unmatched_by_id[r['id']]['source']})"
                tgt = target or "—"
            else:
                verdict = "ACCEPTED"
                tgt = target
            print(f"  • {r['label']!r}  [{band_str}]  {r['value']}  → {tgt}   [{verdict}]")

    out_path = Path(args.out) if args.out else Path(args.input).with_suffix(".mapped.json")
    out_path.write_text(json.dumps({"mapped": mapped, "unmatched": unmatched}, indent=2, ensure_ascii=False))
    print(f"\nWrote full result → {out_path}")


if __name__ == "__main__":
    main()
