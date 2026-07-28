"""
Pure-unit tests (no DB, no LLM) for the band-aware deterministic pass-2 mapper.

Each test encodes one of the real mis-mapping failure modes the LLM mapper exhibits and asserts the
deterministic mapper places (or refuses to place) the line correctly via band + arithmetic-role
gating:

  * cash-flow: a financing-activity line never lands under operating activities (the borrowings
    screenshot);
  * balance sheet: same-label "borrowings"/"trade receivables" routed to current vs non-current by
    the source band, not by wording;
  * arithmetic role: a "Total ..." line only fills a total/subtotal slot, and a component never
    fills a total slot;
  * P&L: synonym wordings resolve to the canonical leaf, and statement-locking holds.

Run:
    PYTHONPATH=. pytest tests/test_financial_deterministic_mapping.py -v
"""
from __future__ import annotations

from src.llm.prompts import AUDIT_FINANCIALS_SCHEMA_FALLBACK
from src.services.financial_label_matching import build_candidate_index
from src.services.financial_field_definitions import (
    band_for_path as canonical_leaf_band,
    band_for_source_headers as source_line_band,
    bands_compatible as band_compatible,
    detect_statement,
)
from src.services.financial_deterministic_mapping import (
    apply_residual_assignments,
    build_leaf_catalog,
    candidates_for_line,
    flatten_source_lines,
    is_total_label,
    map_financials_deterministic,
    split_confident_and_residual,
)

SCHEMA = AUDIT_FINANCIALS_SCHEMA_FALLBACK
CAND = build_candidate_index(SCHEMA)
ALIASES: dict = {}


def _map(raw: dict):
    return map_financials_deterministic(raw, SCHEMA, CAND, ALIASES)


def _dotted(tree: dict, path: str):
    cur = tree
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _all_numeric_leaves(node, prefix="") -> dict:
    out: dict = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_all_numeric_leaves(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = float(node)
    return out


# --- band-derivation unit tests ----------------------------------------------------------


class TestBandDerivation:
    def test_detect_statement_from_headers(self):
        assert detect_statement(["balance_sheet", "non_current_liabilities", "borrowings"]) == "balance_sheet"
        assert detect_statement(["cash_flow_statement", "cash_flows_from_financing_activities"]) == "cash_flow_statement"
        assert detect_statement(["profit_and_loss", "revenue_from_operations"]) == "profit_and_loss"
        assert detect_statement(["statement_of_financial_position", "current_assets", "cash"]) == "balance_sheet"
        # Out of scope (SOCE / unknown).
        assert detect_statement(["statement_of_changes_in_equity", "balance"]) is None

    def test_soce_child_keys_with_profit_words_are_out_of_scope(self):
        """SOCE movement rows carry words like 'profit_loss'/'comprehensive_income' in CHILD keys.

        The root-header guard must keep them out of scope so they never mis-tag as profit_and_loss
        (this is the leak that sent ~25 equity-movement rows to the LLM on a real report). General
        across reports — not tied to any one statement's wording.
        """
        assert (
            detect_statement(
                ["statement_of_changes_in_equity", "profit_loss_for_the_year_fy2025", "total_attributable_to_owners"]
            )
            is None
        )
        assert (
            detect_statement(
                ["statement_of_changes_in_equity", "total_comprehensive_income_fy2025", "non_controlling_interests"]
            )
            is None
        )
        assert (
            detect_statement(["consolidated_statement_of_changes_in_equity", "closing_balance", "share_capital"])
            is None
        )
        assert detect_statement(["notes", "note_14_profit_before_tax", "depreciation"]) is None
        # ...but a genuine P&L header still resolves.
        assert detect_statement(["statement_of_comprehensive_income", "revenue"]) == "profit_and_loss"

    def test_source_band_balance_sheet_currentness(self):
        b = source_line_band(["balance_sheet", "non_current_liabilities"], "balance_sheet")
        assert b["side"] == "liabilities" and b["currentness"] == "non_current"
        b2 = source_line_band(["balance_sheet", "current_assets"], "balance_sheet")
        assert b2["side"] == "assets" and b2["currentness"] == "current"

    def test_source_band_cash_flow_activity(self):
        b = source_line_band(["cash_flow_statement", "cash_flows_from_financing_activities"], "cash_flow_statement")
        assert b["activity"] == "financing"

    def test_canonical_leaf_band(self):
        nc = canonical_leaf_band(
            "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
        )
        assert nc["side"] == "liabilities" and nc["currentness"] == "non_current"
        cur = canonical_leaf_band(
            "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings"
        )
        assert cur["currentness"] == "current"

    def test_band_compatible_only_rejects_positive_conflict(self):
        cur = {"statement": "balance_sheet", "side": "liabilities", "currentness": "current", "activity": None}
        nc = {"statement": "balance_sheet", "side": "liabilities", "currentness": "non_current", "activity": None}
        unknown = {"statement": "balance_sheet", "side": "liabilities", "currentness": None, "activity": None}
        assert band_compatible(cur, cur) is True
        assert band_compatible(cur, nc) is False  # current vs non-current conflict
        assert band_compatible(unknown, nc) is True  # unknown is permissive

    def test_is_total_label(self):
        assert is_total_label("Total assets")
        assert is_total_label("total_current_liabilities")
        assert is_total_label("Sub-total")
        assert not is_total_label("Borrowings")
        assert not is_total_label("Profit before tax")


# --- mapping behaviour -------------------------------------------------------------------


class TestDeterministicMapping:
    def test_balance_sheet_borrowings_routed_by_band_not_wording(self):
        """Identical 'borrowings' wording goes to current vs non-current purely from source band."""
        raw = {
            "balance_sheet": {
                "non_current_liabilities": {"borrowings": 500},
                "current_liabilities": {"borrowings": 200},
            }
        }
        mapped, unmatched = _map(raw)
        assert _dotted(
            mapped, "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
        ) == 500
        assert _dotted(
            mapped, "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings"
        ) == 200

    def test_cash_flow_financing_line_never_lands_in_operating(self):
        """The borrowings screenshot: a financing-activity line must not appear under operating."""
        raw = {
            "cash_flow_statement": {
                "cash_flows_from_financing_activities": {
                    "proceeds_from_borrowings": 700,
                    "repayments_of_debt": -300,
                }
            }
        }
        mapped, unmatched = _map(raw)
        operating = mapped.get("cash_flow_statement", {}).get("cash_flows_from_operating_activities", {})
        op_values = set(_all_numeric_leaves(operating).values())
        assert 700 not in op_values
        assert -300 not in op_values
        # Whatever happens, the lines stay scoped to the cash-flow statement.
        for row in unmatched:
            if row["value"] in (700, -300):
                assert row["section_hint"] == "cash_flow_statement"

    def test_total_line_only_fills_total_slot(self):
        raw = {
            "balance_sheet": {
                "assets": {
                    "current_assets": {"cash_and_cash_equivalents": 300},
                    "total_assets": 1000,
                }
            }
        }
        mapped, _ = _map(raw)
        assert _dotted(mapped, "balance_sheet.assets.total_assets") == 1000
        assert _dotted(
            mapped, "balance_sheet.assets.current_assets.financial_assets.cash_and_cash_equivalents"
        ) == 300
        # The component must not have landed in the total slot and vice versa.
        assert _dotted(mapped, "balance_sheet.assets.total_assets") != 300

    def test_component_never_fills_a_total_slot(self):
        """A plain component line must never be auto-placed onto a ``total_*`` canonical leaf."""
        raw = {"balance_sheet": {"current_assets": {"inventories": 450}}}
        mapped, _ = _map(raw)
        leaves = _all_numeric_leaves(mapped)
        for path, val in leaves.items():
            if val == 450:
                assert "total" not in path.split(".")[-1].lower()

    def test_profit_and_loss_synonyms_and_statement_lock(self):
        raw = {
            "profit_and_loss": {
                "revenue_from_operations": 5000,
                "profit_before_tax": 1000,
            }
        }
        mapped, _ = _map(raw)
        assert _dotted(mapped, "profit_and_loss.revenue.revenue_from_operations") == 5000
        assert _dotted(mapped, "profit_and_loss.profit_loss_before_tax") == 1000

    def test_required_top_level_always_present(self):
        mapped, _ = _map({"profit_and_loss": {"revenue_from_operations": 1}})
        assert set(["profit_and_loss", "balance_sheet", "cash_flow_statement"]).issubset(mapped.keys())

    def test_first_writer_wins_no_double_count(self):
        """Two lines resolving to the same canonical leaf: one maps, the other stays recoverable."""
        raw = {
            "profit_and_loss": {
                "revenue_from_operations": 5000,
                "operating_revenue": 5000,  # synonym → same canonical leaf
            }
        }
        mapped, unmatched = _map(raw)
        assert _dotted(mapped, "profit_and_loss.revenue.revenue_from_operations") == 5000
        assert any(
            r.get("source") == "deterministic_slot_taken" and r.get("value") == 5000 for r in unmatched
        )

    def test_out_of_scope_statement_skipped(self):
        """Lines outside the three reconciled statements are not invented into mapped."""
        raw = {"statement_of_changes_in_equity": {"balance_at_year_end": 999}}
        mapped, unmatched = _map(raw)
        assert _all_numeric_leaves(mapped) == {}
        assert all(r.get("value") != 999 for r in unmatched)


class TestCuratedSynonymsAreBounded:
    """Curated synonyms must stay a *closed* statutory vocabulary, not grow per report.

    Company-specific wording belongs in the learned alias memory (audit_financials_label_aliases_v1),
    which scales automatically with report volume. This cap is the guardrail: if it trips, the right
    fix is almost always to let alias memory absorb the wording, not to enlarge this dict.
    """

    MAX_PATHS = 40
    MAX_SURFACE_FORMS = 160

    def test_dict_size_capped(self):
        from src.services.financial_field_definitions import CANONICAL_SYNONYMS

        n_paths = len(CANONICAL_SYNONYMS)
        n_forms = sum(len(v) for v in CANONICAL_SYNONYMS.values())
        assert n_paths <= self.MAX_PATHS, (
            f"CANONICAL_SYNONYMS has {n_paths} paths (cap {self.MAX_PATHS}). Company-specific "
            f"wording should go to alias memory, not this dict."
        )
        assert n_forms <= self.MAX_SURFACE_FORMS, (
            f"CANONICAL_SYNONYMS has {n_forms} surface forms (cap {self.MAX_SURFACE_FORMS})."
        )

    def test_every_synonym_targets_a_real_leaf(self):
        """No synonym may point at a non-existent canonical path (catches schema drift / typos)."""
        from src.services.financial_field_definitions import CANONICAL_SYNONYMS

        valid = {c["full_path"] for c in CAND}
        bad = [p for p in CANONICAL_SYNONYMS if p not in valid]
        assert not bad, f"Synonyms target unknown canonical paths: {bad}"

    def test_no_duplicate_path_keys(self):
        """A path must appear once in the dict literal.

        A duplicate key silently overwrites the earlier entry (Python keeps the last), which would
        drop synonyms — exactly the trap when merging Ind-AS + IFRS/US-GAAP forms for one leaf. The
        live dict can't show dupes (already collapsed), so parse the source AST and count keys.
        """
        import ast
        from pathlib import Path

        import src.services.financial_field_definitions as fd

        tree = ast.parse(Path(fd.__file__).read_text())
        keys: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CANONICAL_SYNONYMS" for t in node.targets
            ):
                for k in node.value.keys:  # type: ignore[attr-defined]
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.append(k.value)
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, f"Duplicate path keys silently overwrite synonyms: {dupes}"

    def test_no_duplicate_surface_forms_within_a_path(self):
        from src.services.financial_field_definitions import CANONICAL_SYNONYMS

        offenders = {p: forms for p, forms in CANONICAL_SYNONYMS.items() if len(forms) != len(set(forms))}
        assert not offenders, f"Repeated surface forms within a path: {list(offenders)}"


class TestHybridResidual:
    """Deterministic-first split + band/role-validated LLM residual assignment (no live LLM)."""

    def test_split_places_confident_and_returns_residual(self):
        raw = {
            "balance_sheet": {
                "non_current_liabilities": {"borrowings": 500},
                "current_assets": {"sundry_widget_doohickey": 77},  # no confident match → residual
            }
        }
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        assert _dotted(
            mapped, "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
        ) == 500
        assert (
            "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
            in placed
        )
        residual_labels = {r["label"] for r in residual}
        assert "sundry widget doohickey" in residual_labels
        # Residual rows carry band + stable id for the assignment step.
        r = next(r for r in residual if r["label"] == "sundry widget doohickey")
        assert r["band"]["currentness"] == "current" and r["band"]["side"] == "assets"
        assert r["id"].startswith("det-")

    def test_apply_residual_accepts_valid_assignment(self):
        raw = {"balance_sheet": {"current_assets": {"stock_in_trade": 320}}}
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        assert residual, "expected an unmatched residual line to assign"
        line_id = residual[0]["id"]
        target = "balance_sheet.assets.current_assets.inventories"
        unmatched = apply_residual_assignments(
            mapped, placed, residual, {line_id: target}, CAND
        )
        assert _dotted(mapped, target) == 320
        assert unmatched == []

    def test_apply_residual_rejects_cross_band_assignment(self):
        """A current-asset residual the LLM tries to drop into a non-current slot is bounced."""
        raw = {"balance_sheet": {"current_assets": {"stock_in_trade": 320}}}
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        line_id = residual[0]["id"]
        # Non-current investments slot — wrong currentness for a current-asset line.
        bad_target = "balance_sheet.assets.non_current_assets.financial_assets.investments"
        unmatched = apply_residual_assignments(
            mapped, placed, residual, {line_id: bad_target}, CAND
        )
        assert _dotted(mapped, bad_target) is None
        assert any(r["source"] == "llm_band_conflict" for r in unmatched)

    def test_apply_residual_rejects_unknown_target(self):
        raw = {"balance_sheet": {"current_assets": {"stock_in_trade": 320}}}
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        line_id = residual[0]["id"]
        unmatched = apply_residual_assignments(
            mapped, placed, residual, {line_id: "balance_sheet.assets.made_up_path"}, CAND
        )
        assert any(r["source"] == "llm_target_unknown" for r in unmatched)

    def test_apply_residual_unmatched_when_no_assignment(self):
        raw = {"balance_sheet": {"current_assets": {"stock_in_trade": 320}}}
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        unmatched = apply_residual_assignments(mapped, placed, residual, {}, CAND)
        assert any(r["source"] == "llm_unmatched" for r in unmatched)

    def test_apply_residual_rejects_role_conflict(self):
        """A plain component residual must not be assignable to a total slot."""
        raw = {"balance_sheet": {"current_assets": {"stock_in_trade": 320}}}
        mapped, placed, residual = split_confident_and_residual(raw, SCHEMA, CAND, ALIASES)
        line_id = residual[0]["id"]
        unmatched = apply_residual_assignments(
            mapped, placed, residual, {line_id: "balance_sheet.assets.total_assets"}, CAND
        )
        # total_assets is also a different band-less slot; role conflict fires first/either way it's bounced.
        assert _dotted(mapped, "balance_sheet.assets.total_assets") is None
        assert unmatched and unmatched[0]["source"] in ("llm_role_conflict", "llm_band_conflict")

    def test_leaf_catalog_shape(self):
        catalog = build_leaf_catalog(CAND)
        assert catalog and all({"path", "statement", "is_total"}.issubset(c) for c in catalog)
        nc = next(
            c
            for c in catalog
            if c["path"]
            == "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
        )
        assert nc["currentness"] == "non_current" and nc["side"] == "liabilities"

    def test_candidates_for_line_squeezes_to_band_and_role(self):
        # A non-current liability COMPONENT line: candidates must include the non-current twin,
        # exclude the current twin, and exclude every total slot.
        line = {
            "label": "borrowings",
            "is_total": False,
            "band": {
                "statement": "balance_sheet",
                "side": "liabilities",
                "currentness": "non_current",
                "activity": None,
            },
        }
        cands = set(candidates_for_line(line, CAND))
        assert (
            "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings"
            in cands
        )
        assert (
            "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings"
            not in cands
        )
        # No total/subtotal slot is offered to a component line, and nothing crosses statement/side.
        assert not any(is_total_label(p.rsplit(".", 1)[-1]) for p in cands)
        assert all(p.startswith("balance_sheet.liabilities.") for p in cands)
        # Strictly smaller than the full leaf universe — the whole point of the squeeze.
        assert len(cands) < len(CAND)

    def test_candidates_for_line_total_line_only_gets_total_slots(self):
        line = {
            "label": "total non-current liabilities",
            "is_total": True,
            "band": {
                "statement": "balance_sheet",
                "side": "liabilities",
                "currentness": "non_current",
                "activity": None,
            },
        }
        cands = candidates_for_line(line, CAND)
        assert cands and all(is_total_label(p.rsplit(".", 1)[-1]) for p in cands)


class TestFlatten:
    def test_flatten_tags_band_and_role(self):
        raw = {
            "balance_sheet": {
                "non_current_liabilities": {"long_term_borrowings": 500, "total_non_current_liabilities": 800}
            }
        }
        lines = flatten_source_lines(raw)
        by_label = {l["label"]: l for l in lines}
        assert by_label["long term borrowings"]["band"]["currentness"] == "non_current"
        assert by_label["long term borrowings"]["is_total"] is False
        assert by_label["total non current liabilities"]["is_total"] is True
