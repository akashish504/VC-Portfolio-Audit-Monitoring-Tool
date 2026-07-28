"""Unit tests for second-level section tagging (placement_path) of unmatched audit-financials rows."""
from __future__ import annotations

from src.llm.prompts import AUDIT_FINANCIALS_SCHEMA_FALLBACK
from src.services.financial_section_tagging import (
    apply_llm_section_assignments,
    assign_placement_paths,
    llm_section_candidates,
    placement_from_band,
    section_from_memory,
)

SCHEMA = AUDIT_FINANCIALS_SCHEMA_FALLBACK


class TestPlacementFromBand:
    def test_cash_flow_activity_maps_to_section(self):
        assert (
            placement_from_band({"statement": "cash_flow_statement", "activity": "operating"})
            == "cash_flow_statement.cash_flows_from_operating_activities"
        )
        assert (
            placement_from_band({"statement": "cash_flow_statement", "activity": "financing"})
            == "cash_flow_statement.cash_flows_from_financing_activities"
        )

    def test_cash_flow_without_activity_defers(self):
        assert placement_from_band({"statement": "cash_flow_statement", "activity": None}) is None

    def test_balance_sheet_side_and_currentness(self):
        assert (
            placement_from_band({"statement": "balance_sheet", "side": "assets", "currentness": "non_current"})
            == "balance_sheet.assets.non_current_assets"
        )
        assert (
            placement_from_band({"statement": "balance_sheet", "side": "liabilities", "currentness": "current"})
            == "balance_sheet.liabilities.current_liabilities"
        )
        assert (
            placement_from_band({"statement": "balance_sheet", "side": "equity"})
            == "balance_sheet.equity"
        )

    def test_balance_sheet_side_only_falls_to_side(self):
        assert (
            placement_from_band({"statement": "balance_sheet", "side": "assets", "currentness": None})
            == "balance_sheet.assets"
        )

    def test_profit_and_loss_band_has_no_subdimension(self):
        assert placement_from_band({"statement": "profit_and_loss"}) is None


class TestLlmSectionCandidates:
    def test_cash_flow_universe_is_small(self):
        cands = llm_section_candidates(SCHEMA, "cash_flow_statement")
        assert "cash_flow_statement.cash_flows_from_operating_activities" in cands
        assert "cash_flow_statement.cash_flows_from_investing_activities" in cands
        assert "cash_flow_statement.cash_flows_from_financing_activities" in cands
        # No leaf/exact line ever leaks into the section universe.
        assert all(p.count(".") == 1 for p in cands)

    def test_profit_and_loss_universe(self):
        cands = set(llm_section_candidates(SCHEMA, "profit_and_loss"))
        assert {"profit_and_loss.revenue", "profit_and_loss.expenses", "profit_and_loss.tax_expense"} <= cands

    def test_unknown_statement_returns_all_sections(self):
        cands = llm_section_candidates(SCHEMA, None)
        assert any(p.startswith("cash_flow_statement.") for p in cands)
        assert any(p.startswith("balance_sheet.") for p in cands)
        assert any(p.startswith("profit_and_loss.") for p in cands)


class TestAssignPlacementPaths:
    def test_tier1_confident_suggestion(self):
        rows = [
            {
                "id": "u1",
                "document_label": "Net cash from operating activities",
                "value": 6206625,
                "suggestion": {
                    "full_path": "cash_flow_statement.cash_flows_from_operating_activities."
                    "net_cash_provided_by_used_in_operating_activities",
                    "confidence": 0.95,
                },
            }
        ]
        out, need = assign_placement_paths(rows, SCHEMA)
        assert out[0]["placement_path"] == "cash_flow_statement.cash_flows_from_operating_activities"
        assert out[0]["placement_tier"] == 1
        assert out[0]["placement_source"] == "suggestion"
        assert need == []

    def test_tier2_band_deterministic(self):
        rows = [
            {
                "id": "u2",
                "document_label": "Operating Activities",
                "value": 6206625,
                "band": {"statement": "cash_flow_statement", "activity": "operating"},
            }
        ]
        out, need = assign_placement_paths(rows, SCHEMA)
        assert out[0]["placement_path"] == "cash_flow_statement.cash_flows_from_operating_activities"
        assert out[0]["placement_tier"] == 2
        assert out[0]["placement_source"] == "band"
        assert need == []

    def test_band_derived_from_raw_path_when_band_absent(self):
        rows = [
            {
                "id": "u3",
                "document_label": "Borrowings",
                "value": 500,
                "raw_path": "balance_sheet.non_current_liabilities.borrowings",
            }
        ]
        out, _ = assign_placement_paths(rows, SCHEMA)
        assert out[0]["placement_path"] == "balance_sheet.liabilities.non_current_liabilities"

    def test_tier3_when_only_statement_known(self):
        rows = [
            {
                "id": "u4",
                "document_label": "Sundry balances written back",
                "value": 1000,
                "section_hint": "profit_and_loss",
            }
        ]
        out, need = assign_placement_paths(rows, SCHEMA)
        assert out[0]["placement_tier"] == 3 and out[0]["placement_path"] is None
        assert [r["id"] for r in need] == ["u4"]

    def test_idempotent_without_overwrite(self):
        rows = [
            {
                "id": "u5",
                "document_label": "X",
                "value": 1,
                "placement_path": "balance_sheet.equity",
                "placement_tier": 2,
            }
        ]
        out, need = assign_placement_paths(rows, SCHEMA)
        assert out[0]["placement_path"] == "balance_sheet.equity"
        assert need == []


class TestSectionFromMemory:
    # alias index shape mirrors financial_label_matching.build_aliases_index:
    #   {(section_or_any, normalized_label): {parent_path, key, full_path, count}}
    ALIASES = {
        ("cash_flow_statement", "purchase of investments"): {
            "parent_path": "cash_flow_statement.cash_flows_from_investing_activities",
            "key": "acquisition_sale_of_equity_securities",
            "full_path": "cash_flow_statement.cash_flows_from_investing_activities."
            "acquisition_sale_of_equity_securities",
            "count": 3,
        },
    }

    def _valid(self):
        from src.services.financial_section_tagging import _section_nodes

        return _section_nodes(SCHEMA, max_depth=4)

    def test_memory_resolves_section_from_learned_alias(self):
        out = section_from_memory("Purchase of Investments", {}, self.ALIASES, self._valid())
        assert out == "cash_flow_statement.cash_flows_from_investing_activities"

    def test_memory_band_gate_blocks_incompatible(self):
        # A row whose band says operating must not pick up an investing-activities alias.
        out = section_from_memory(
            "Purchase of Investments",
            {"statement": "cash_flow_statement", "activity": "operating"},
            self.ALIASES,
            self._valid(),
        )
        assert out is None

    def test_memory_unknown_label_returns_none(self):
        assert section_from_memory("totally novel line", {}, self.ALIASES, self._valid()) is None

    def test_assign_uses_memory_when_band_absent(self):
        rows = [{"id": "m1", "document_label": "Purchase of Investments", "value": 100}]
        out, need = assign_placement_paths(rows, SCHEMA, aliases_index=self.ALIASES)
        assert out[0]["placement_path"] == "cash_flow_statement.cash_flows_from_investing_activities"
        assert out[0]["placement_tier"] == 2 and out[0]["placement_source"] == "memory"
        assert need == []


class TestApplyLlmSectionAssignments:
    def test_valid_target_promotes_to_tier2(self):
        rows = [{"id": "u6", "placement_tier": 3, "placement_path": None}]
        n = apply_llm_section_assignments(
            rows, {"u6": "cash_flow_statement.cash_flows_from_investing_activities"}, SCHEMA
        )
        assert n == 1
        assert rows[0]["placement_path"] == "cash_flow_statement.cash_flows_from_investing_activities"
        assert rows[0]["placement_tier"] == 2 and rows[0]["placement_source"] == "llm"

    def test_invalid_target_is_ignored(self):
        rows = [{"id": "u7", "placement_tier": 3, "placement_path": None}]
        n = apply_llm_section_assignments(rows, {"u7": "not.a.real.section"}, SCHEMA)
        assert n == 0 and rows[0]["placement_path"] is None
