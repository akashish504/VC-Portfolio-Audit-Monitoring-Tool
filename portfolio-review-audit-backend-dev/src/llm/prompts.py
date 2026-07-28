"""
Central LLM prompts for document extraction (org chart + audit files).

Services import from here only — do not duplicate prompt text in feature modules.
"""
from __future__ import annotations

# --- Org chart (entity hierarchy) ---

# ORG_CHART_SYSTEM_PROMPT = """Role: You are a specialized Data Extraction Assistant focused on Corporate Governance and Entity Structures.

# Task: Analyze the attached document and extract the complete organizational hierarchy of the group.

# Output Format: Return the result strictly as a JSON array of objects.

# Entity Schema:
# For every entity found in the document, create an object with this structure:
# {
#     "id": "Sequential integer starting from 1",
#     "entity_name": "Full legal name of the entity",
#     "geolocation": "Country or City/State of incorporation",
#     "is_parent": true,
#     "children": ["List of IDs of direct subsidiaries or step-down entities"]
# }

# Note: Use boolean true/false for is_parent. Set is_parent to true ONLY for the ultimate holding company. children must be an array of numeric IDs (same id field as sibling objects).

# Extraction Guidelines:

# 1. Identify the Root: Start by identifying the "Ultimate Holding Company" or the "Parent" at the top of the chart. Mark this as is_parent: true.

# 2. Trace Relationships: Map every connection (lines in diagrams or "Subsidiary of" labels in text) to populate the children array.

# 3. Handle Step-Downs: Ensure that "Step-Down Subsidiaries" are correctly nested as children of their immediate parent, not the ultimate parent.

# 4. Clean Data: Remove any ownership percentages (e.g., "100% stake" or "66%") from the entity_name field.

# 5. Identify Geolocation: Extract the country/region mentioned within the entity box or the adjacent column.

# Strict Rule: Do not include any conversational text or explanations. Return only the valid JSON array."""

# ORG_CHART_USER_INSTRUCTION = (
#     "Analyze the attached document and return only the JSON array specified in the system message."
# )


ORG_CHART_SYSTEM_PROMPT = """You are a data extraction assistant. Your only job is to read a corporate structure document and return a JSON array. Nothing else.

---
## STEP-BY-STEP PROCESS

Follow these steps in order. Do not skip any step.

**Step 1 — Find every entity.**
Scan the entire document. Count every distinct box (or row/bullet) in the diagram. Each box is ONE entity — even if its label looks like a location (e.g. "Haryana, India", "Singapore", "USA"). Assign each box a unique integer ID starting from 1.

SKIP any entry that is a human name (i.e. an individual investor, natural person, founder, director, trustee, or shareholder written as a personal name). Human names often appear beside a holding percentage (e.g. "John Smith — 35%") or in an ownership note. Do NOT include these as entities — only legal corporate entities and their location-labelled subsidiary boxes belong in the output.

**Step 2 — Find the root (ultimate parent).**
The root is the single entity at the very top of the chart with no parent above it. There is always exactly one root. Set `is_parent: true` for this entity only. Set `is_parent: false` for every other entity.

**Step 3 — Map direct parent → child relationships using arrows.**
An arrow (→ or ↓ or any connecting line with an arrowhead) pointing FROM box A TO box B means A is the parent and B is the child. Follow every arrow in the diagram. Do not skip levels — a grandchild is NOT a direct child of the grandparent.

CRITICAL: Every box that an arrow points TO is a child entity. Record it in the `children` array of the entity the arrow points FROM. Never omit a child just because its box label looks like a place name — the arrow is the definitive signal of a parent→child relationship.

**Step 4 — Populate `children` arrays.**
For every entity, list the IDs of its direct children only (the boxes its arrows point to). If an entity has no outgoing arrows, use an empty array `[]`.

**Step 5 — Extract geolocation.**
Use the country or city/state written inside or beside each entity's box. If a box's entire label is a location (e.g. "Haryana, India"), use that label as both the `entity_name` AND the `geolocation`. If not stated, use `null`.

**Step 6 — Extract entity type (optional, independent of hierarchy).**
If the document explicitly labels an entity's type (in the box, legend, or notes), set `entity_type` to exactly one of: `"Intermediate Holding"`, `"Associate"`, `"Branch"`, `"Ultimate Holding"`, `"Subsidiary"`, `"Holding"`, or `"Other"`. Do not infer type from chart position, root status, or parent/child links alone. If not explicitly stated, use `null`.

**Step 7 — Extract incorporation details.**
Look for a registration number, UEN, company number, or date of incorporation near each entity. If not present, use `null` for that field.

**Step 8 — Clean entity names.**
Remove all ownership percentages (e.g. "100%", "66.7% stake") and connector words like "via", "through", "held by" from the `entity_name` field. Keep only the legal name (or location label if that is all the box contains).

---
## OUTPUT SCHEMA

Return a JSON array. Each element must follow this exact structure:

```json
[
  {
    "id": 1,
    "entity_name": "Full legal name of the entity (or location label if that is all the box shows)",
    "geolocation": "Country or City/State of incorporation, or null",
    "entity_type": "Intermediate Holding | Associate | Branch | Ultimate Holding | Subsidiary | Holding | Other | null",
    "is_parent": true,
    "children": [2, 3]
  },
  {
    "id": 2,
    "entity_name": "...",
    "geolocation": "...",
    "entity_type": null,
    "is_parent": false,
    "children": []
  }
]
```

---
## EXAMPLE

Given a chart with two boxes connected by a downward arrow:
- Top box: "Alpha Holdings" (Singapore)
- Bottom box: "Haryana, India"  ← arrow points here from top box

Correct output (the arrow makes "Haryana, India" a child entity):
```json
[
  {
    "id": 1,
    "entity_name": "Alpha Holdings",
    "geolocation": "Singapore",
    "entity_type": "Ultimate Holding",
    "is_parent": true,
    "children": [2]
  },
  {
    "id": 2,
    "entity_name": "Haryana, India",
    "geolocation": "Haryana, India",
    "entity_type": null,
    "is_parent": false,
    "children": []
  }
]
```

Given a three-level chart: Alpha Holdings (Singapore) → Beta Pte Ltd (Singapore, 100%) → Gamma LLC (USA)

Correct output:
```json
[
  {
    "id": 1,
    "entity_name": "Alpha Holdings",
    "geolocation": "Singapore",
    "entity_type": "Ultimate Holding",
    "is_parent": true,
    "children": [2]
  },
  {
    "id": 2,
    "entity_name": "Beta Pte Ltd",
    "geolocation": "Singapore",
    "entity_type": "Intermediate Holding",
    "is_parent": false,
    "children": [3]
  },
  {
    "id": 3,
    "entity_name": "Gamma LLC",
    "geolocation": "USA",
    "entity_type": "Associate",
    "is_parent": false,
    "children": []
  }
]
```

---
## STRICT RULES

- Return ONLY the JSON array. No explanations, no markdown, no extra text.
- `is_parent` must be `true` for exactly ONE entity (the root). All others must be `false`.
- `children` must be an array of integers. Never use strings or names inside `children`.
- Every entity must appear exactly once in the array.
- Every arrow in the diagram MUST be reflected as a parent→child link. Never omit an arrow.
- If two entities share the same name but are distinct boxes in the diagram, assign them separate IDs.
- NEVER include human names (individual investors, natural persons, founders, directors, trustees, or personal shareholders) as entities, even if they appear with a holding percentage. Only legal corporate entities and their location-labelled boxes are valid."""

ORG_CHART_USER_INSTRUCTION = (
    "Extract the complete organizational hierarchy from the attached document "
    "and return only the JSON array as specified. Do not include any other text."
)

# --- Audit document: financial statements (primary) ---

AUDIT_FINANCIALS_SYSTEM_PROMPT = """Role: You are a Senior Financial Auditor and Data Extraction Specialist.

Task: Extract financial data from the attached audit report exactly as it appears in the document, **scoped to group-level (consolidated) reporting only** where both group and separate-entity statements exist (see rule 6). Do NOT map to a predefined schema in this pass. Instead, preserve the original structure, labels, and hierarchy of the **consolidated / group** statements you use.

Rules:

1. Key Naming: Convert each line item label from the document into snake_case (lowercase, spaces replaced with underscores). For example:
   - "Property, Plant and Equipment" → "property_plant_and_equipment"
   - "Trade and Other Receivables" → "trade_and_other_receivables"
   - "Profit Before Tax" → "profit_before_tax"

2. Structure: Preserve the natural grouping and hierarchy as presented in the document. Use nested JSON objects for sections and sub-sections (e.g., "non_current_assets", "current_assets" nested under "assets"). For **`balance_sheet`**, use **exactly three** top-level keys only: `"assets"`, `"equity"`, and `"liabilities"`. Every line item must nest under those branches (and their sub-objects). Do **not** add a fourth top-level key such as `"cash"`, `"debt"`, `"other"`, or `"borrowings"` beside the three sections—place those labels under the correct branch. For a **balance sheet**, when the layout is a conventional statement of financial position, nest items under the correct section: e.g. cash, bank balances, and receivables under **assets** (typically current assets), borrowings and debt under **liabilities**.

3. Values: Extract numeric values as numbers (not strings). Use 0 for dashes or blanks. Negative values should be represented as negative numbers. **Every stored number MUST be in the currency’s smallest standard whole unit** (e.g. whole rupees, whole dollars) — see rule 10 if the document uses crore, lakh, thousands, millions, etc.

4. Completeness: Extract EVERY line item that appears in the **group / consolidated** financial statements you are using (per rule 6). Do not skip or merge items within that scope. If a line item exists in those statements, it must appear in the output.

5. Sections: Organize the output into top-level sections matching the document structure. Common sections include:
   - "report_metadata" (entity name, UEN, reporting period, currency, audit firm, etc.)
   - income/earnings statement — use the document's own header as the key, e.g. "profit_and_loss", "statement_of_comprehensive_income", "income_statement", "statement_of_operations", "statement_of_earnings", "statement_of_financial_performance", "statement_of_income"
   - balance sheet — use the document's own header as the key, e.g. "balance_sheet", "statement_of_financial_position", "statement_of_financial_condition"
   - "cash_flow_statement" (if present)
   - "statement_of_changes_in_equity" (if present)
   - Any other statements present in the document

6. Group vs entity (consolidated scope — critical): Many audit reports include **both** group-level and **separate / entity-only** financials. Extract and report **group-level (consolidated) data only**. Use statements or columns explicitly labeled consolidated, group, combined, or equivalent. **Do not** extract standalone company-only, parent-only, or subsidiary-only statement figures when consolidated group statements exist for the same period. If one table has both a "Company" and a "Group" (or "Consolidated") column, use **only** the Group/Consolidated column. If the document has **no** group/consolidated statements and only separate-entity statements, extract that available set (do not invent consolidated totals).

6b. Bilingual / dual-language labels (one line, one figure — critical): Some statements label each line item in **two languages** — a local language and English — shown side by side, stacked, or in parallel label columns that share **one** set of figures (one number per period). These two labels are the **same single line**, not two lines. In that case, extract each line **exactly once**, using the **English** label for the key, and do **not** emit a second entry (or repeat the figure) for the non-English label. When a line is shown in only one language with no English equivalent, extract it normally using the label that is present. This mirrors rule 6: never duplicate a single line just because it appears under two labels.

7. Latest Period: Only extract values for the most recent financial period/year shown.

8. Notes: Do not extract notes to the financial statements, only the primary financial statement figures.

9. Currency (REQUIRED): In ADDITION to the sections above, the output JSON MUST contain a top-level field named "currency" whose value is the ISO-4217 3-letter code (uppercase) of the amounts shown in the statements. Derive it from whichever is clearest:
   - Explicit mention on the statement or in the notes (e.g. "Amounts in INR", "Amounts in S$", "Figures in US$ thousands").
   - Symbol conventions, mapped to ISO codes: "Rs." / "₹" / "Rupees" → "INR"; "$" in a clearly US context → "USD"; "$" in a Singapore context → "SGD"; "HK$" → "HKD"; "£" → "GBP"; "€" → "EUR"; "¥" in a Chinese context → "CNY"; "¥" in a Japanese context → "JPY"; "AED" / "Dhs." → "AED".
   - Country of incorporation as a last resort ONLY when the document lacks any currency mark (e.g. an Indian company reporting in its home statements → "INR").
   If you truly cannot determine it, use null. Do NOT wrap the value in "report_metadata"; it must be at the top level of the returned JSON object.

10. Amount scale (denomination) and normalization (REQUIRED): Audit reports often print figures in **larger units** (e.g. **crore / Cr**, **lakh / Lac**, **thousands**, **millions**, **m**, **'000**, or “in millions of USD”). You MUST:
   - Convert every line item to **full base units** in JSON (e.g. `1.2` crore INR → `12000000` rupees; `500` in “USD thousands” → `500000` dollars). Never leave values in crore/lakh/thousands as printed.
   - Add two **top-level** fields next to `currency`:
     - `figures_denomination` (string): How the **audited primary statements** describe the scale, e.g. `INR — amounts in crore (₹ Cr)`, `USD in thousands`, `Absolute units / no scale stated on face of statements`.
     - `figures_scale_to_smallest_unit` (JSON number): Multiplier such that **printed_amount × figures_scale_to_smallest_unit = the numeric value you store**. Examples: Indian **crore** (1 Cr = 10⁷ rupees) → `10000000`; **lakh** → `100000`; **thousands** → `1000`; **millions** of currency → `1000000`; already in rupees/dollars with no scale → `1`.
   - If wording is ambiguous, infer the scale from column headers, note 1, or “Amounts in …” on the face of the **consolidated** statements. Use **one** scale consistently for all numbers in this JSON when the primary statements use a single basis.

Output Rule: Return ONLY the JSON object. Do not provide conversational text."""

# --- Audit document: schema-driven mapping (Option B) ---

# Required top-level sections in the *mapped* output.
# These keys are canonical (snake_case) and must always exist (possibly empty objects).
AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL = (
    "profit_and_loss",
    "balance_sheet",
    "cash_flow_statement",
)

# Fallback canonical schema (used when config_table is not populated yet).
# Keep this minimal-but-structured; you can override/extend via ConfigTable key `audit_financials_schema_v1`.
#
# Convention for the LLM mapper and for UI defaults:
# - ``null`` (Python ``None`` → JSON ``null`` in the prompt): **scalar** slot — a single numeric value at this path
#   when the line exists. If the document omits the line, the mapper still omits it; the API merges ``null``
#   later so users can fill it manually.
# - ``{}`` or nested dicts: **bucket** — container for line items (e.g. ``tax`` → ``current_tax``, ``deferred_tax``).
AUDIT_FINANCIALS_SCHEMA_FALLBACK: dict = {
    "profit_and_loss": {
        "revenue": {
            "revenue_from_operations": None,
            "other_income": None,
            "total_income": None,
            "other": {},
        },
        "expenses": {
            "cost_of_material_consumed": None,
            "purchase_of_stock_in_trade": None,
            "changes_in_inventories_of_finished_goods_stock_in_trade_and_work_in_progress": None,
            "employee_benefit_expense": None,
            "finance_costs": None,
            "depreciation_and_amortization_expense": None,
            "other_expenses": None,
            "total_expenses": None,
            "other": {},
        },
        "profit_loss_before_exceptional_items_or_tax": None,
        "exceptional_items": None,
        "profit_loss_before_tax": None,
        # EBITDA is a derived figure (PBT + finance costs + depreciation, added back); surface it just
        # above the tax expense section. Order here drives the served/UI order via
        # reorder_extracted_to_schema (applied on read in AuditService.extraction_status).
        "ebitda": None,
        "tax_expense": {
            "current_tax": None,
            "deferred_tax": None,
            "total_tax_expense": None,
            "other": {},
        },
        "profit_loss_for_the_period_of_continuing_operation": None,
        "profit_loss_from_discontinued_operations": None,
        "tax_expense_for_discontinued_operation": None,
        "profit_loss_from_discontinued_operations_after_tax": None,
        "profit_loss_for_the_period": None,
        "other_comprehensive_income": {
            "a_items_that_will_not_be_reclassified_to_profit_or_loss": None,
            "a_income_tax_relating_to_items_that_will_not_be_reclassified_to_profit_or_loss": None,
            "b_items_that_will_be_reclassified_to_profit_or_loss": None,
            "b_income_tax_relating_to_items_that_will_be_reclassified_to_profit_or_loss": None,
            "total_oci": None,
            "other": {},
        },
        "total_comprehensive_income_for_the_period": None,
        "other": {},
    },
    "balance_sheet": {
        "assets": {
            "non_current_assets": {
                "property_plant_and_equipment": None,
                "capital_work_in_progress": None,
                "investment_property": None,
                "goodwill": None,
                "other_intangible_assets": None,
                "intangible_assets_under_development": None,
                "biological_assets_other_than_bearer_plants": None,
                "financial_assets": {
                    "investments": None,
                    "trade_receivables": None,
                    "loans": None,
                    "others": None,
                    "total_financial_assets": None,
                    "other": {},
                },
                "deferred_tax_assets_net": None,
                "other_non_current_assets": None,
                "total_non_current_assets": None,
                "other": {},
            },
            "current_assets": {
                "inventories": None,
                "financial_assets": {
                    "investments": None,
                    "trade_receivables": None,
                    "cash_and_cash_equivalents": None,
                    "bank_balances_other_than_cash_and_cash_equivalents": None,
                    "loans": None,
                    "others": None,
                    "total_financial_assets": None,
                    "other": {},
                },
                "current_tax_assets_net": None,
                "other_current_assets": None,
                "total_current_assets": None,
                "other": {},
            },
            "total_assets": None,
            "other": {},
        },
        "equity": {
            "equity_share_capital": None,
            "preference_share_capital": None,
            "other_equity": None,
            "total_equity": None,
            "other": {},
        },
        "liabilities": {
            "non_current_liabilities": {
                "financial_liabilities": {
                    "borrowings": None,
                    "lease_liabilities": None,
                    "trade_payables": {
                        "dues_of_micro_enterprises_and_small_enterprises": None,
                        "dues_of_creditors_other_than_micro_enterprises_and_small_enterprises": None,
                        "total_trade_payables": None,
                    },
                    "other_financial_liabilities": None,
                    "total_financial_liabilities": None,
                    "other": {},
                },
                "provisions": None,
                "deferred_tax_liabilities_net": None,
                "other_non_current_liabilities": None,
                "total_non_current_liabilities": None,
                "other": {},
            },
            "current_liabilities": {
                "financial_liabilities": {
                    "borrowings": None,
                    "lease_liabilities": None,
                    "trade_payables": {
                        "dues_of_micro_enterprises_and_small_enterprises": None,
                        "dues_of_creditors_other_than_micro_enterprises_and_small_enterprises": None,
                        "total_trade_payables": None,
                    },
                    "other_financial_liabilities": None,
                    "total_financial_liabilities": None,
                    "other": {},
                },
                "other_current_liabilities": None,
                "provisions": None,
                "current_tax_liabilities_net": None,
                "total_current_liabilities": None,
                "other": {},
            },
            "total_liabilities": None,
            "total_equity_and_liabilities": None,
            "other": {},
        },
    },
    "cash_flow_statement": {
        "cash_flows_from_operating_activities": {
            # Indirect method (IFRS / Ind-AS) — the common layout in these reports. Listed FIRST and
            # in reading order so the served tree flows top-to-bottom exactly as presented: profit
            # before tax → non-cash adjustments → operating profit before working-capital changes →
            # working-capital movements → cash generated → taxes paid → net cash. The US-GAAP keys
            # below are kept for US-style filings (they stay null/hidden for indirect statements).
            "profit_before_tax": None,
            "adjustments": {},
            "operating_profit_before_working_capital_changes": None,
            "changes_in_working_capital": {},
            "cash_generated_from_operations": None,
            "income_taxes_paid": None,
            "net_cash_from_operating_activities": None,
            "net_income": None,
            "adjustments_to_reconcile_net_income": {
                "accretion_amortization_of_discount_premium_on_issued_debt_securities": None,
                "gain_loss_on_extinguishment_of_debt": None,
                "depreciation_and_amortization": None,
                "amortization_of_debt_issue_costs": None,
                "share_based_incentive_compensation": None,
                "impairment_of_assets": None,
                "provision_for_bad_debt_expense": None,
                "inventory_obsolescence_impairment": None,
                "deferred_taxes": None,
                "noncash_provisions_for_exit_costs": None,
                "loss_gain_on_disposal_of_property_and_equipment": None,
                "income_loss_from_equity_method_investments_net_of_dividends_received": None,
                "foreign_currency_transactions": None,
                "other": {},
            },
            "changes_in_operating_assets_and_liabilities": {
                "decrease_increase_in_trade_receivables": None,
                "cash_received_on_sale_of_accounts_receivable": None,
                "decrease_increase_in_inventories": None,
                "decrease_increase_in_other_assets_net": None,
                "increase_decrease_in_operating_accounts_payable": None,
                "increase_decrease_in_accrued_liabilities": None,
                "increase_decrease_in_income_taxes_payable": None,
                "increase_decrease_in_other_liabilities_net": None,
                "other": {},
            },
            "net_cash_provided_by_used_in_operating_activities": None,
            "other": {},
        },
        "cash_flows_from_investing_activities": {
            "acquisition_sale_of_equity_securities": None,
            "acquisition_proceeds_from_sale_of_property_plant_and_equipment": None,
            "acquisition_sale_of_a_business_net_of_cash_and_cash_equivalents_acquired_or_sold": None,
            "impact_to_cash_resulting_from_initial_consolidation_deconsolidation": None,
            "contributions_and_advances_to_joint_ventures": None,
            "subsequent_collections_of_receivables_sold_and_reacquired": None,
            "net_cash_provided_by_used_in_investing_activities": None,
            "other": {},
        },
        "cash_flows_from_financing_activities": {
            "bank_overdrafts": None,
            "payment_of_contingent_consideration": None,
            "proceeds_from_debt": None,
            "repayments_of_debt": None,
            "payments_of_debt_issue_costs": None,
            "dividends_paid": None,
            "net_payments_of_short_term_borrowings": None,
            "repurchases_of_equity_securities": None,
            "acquisition_of_common_stock_for_tax_withholding_obligations": None,
            "distributions_to_noncontrolling_interests": None,
            "principal_payments_under_capital_lease_obligations": None,
            "net_activity_from_derivatives_with_an_other_than_insignificant_financing_element": None,
            "net_cash_provided_by_used_in_financing_activities": None,
            "other": {},
        },
        "effect_of_exchange_rate_changes_on_cash_cash_equivalents_and_restricted_cash": None,
        "cash_cash_equivalents_and_restricted_cash": {
            "net_change_during_the_period": None,
            "balance_beginning_of_period": None,
            "balance_end_of_period": None,
            "other": {},
        },
        "other": {},
    },
}


def audit_financials_schema_mapping_system_prompt(*, schema: dict) -> str:
    """
    Second-pass mapping: canonical ``mapped`` tree + ``unmatched`` rows for human-in-the-loop review.

    Absent lines: omit keys (do not use 0 as a stand-in for "not in document").
    """
    import json

    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))

    return f"""Role: You are a Senior Financial Auditor and Data Normalization Specialist.

Task: You will receive raw extracted JSON from a financial statement. Map it into the canonical schema using **loose semantic matching**: different wordings for the same concept belong in the same bucket (e.g. "Revenue from operations" and "Operating revenue" → the same revenue group).

If the raw JSON accidentally mixes **entity-only** and **group/consolidated** figures, map **only** values that correspond to **group / consolidated** reporting. Ignore duplicate line items that clearly belong to separate-entity or company-only columns when consolidated data is also present.

The raw JSON may include top-level metadata keys such as `currency`, `figures_denomination`, and `figures_scale_to_smallest_unit`. **Do not** copy them into `"mapped"` or `"unmatched"`; map only financial statement line items.

Output format — return ONE JSON object with exactly these top-level keys:
- "mapped": object — the canonical financial tree.
- "unmatched": array — each row is a document line you could not place under a canonical key (even under an appropriate `other`).

Canonical schema for the "mapped" object (keep top-level keys exactly: profit_and_loss, balance_sheet, cash_flow_statement; do not add or remove them):
{schema_json}

Schema shape legend (critical):
- A key whose value is JSON ``null`` in the schema is a **scalar** — at most one number belongs at that path (e.g. ``profit_and_loss.profit_before_tax``).
- A key whose value is an **object** in the schema is a **bucket** — place line items as nested snake_case keys under it (e.g. ``tax`` → ``current_tax``, ``deferred_tax``). Empty ``{{}}`` means you may add any semantically appropriate child keys.

Rules for "mapped":

1) Only use keys that exist in the canonical schema, plus dynamic snake_case keys you add **under** bucket objects when the schema shows ``{{}}`` or nested containers.

2) Presence vs zero (critical):
   - **Omit** a leaf key if that line does **not** appear in the document. Do **not** use 0 to mean "missing"; users must be able to tell "not in document" from "reported as zero".
   - Use **0** only when the document explicitly shows zero for that line.
   - Prefer omitting unused schema keys entirely over filling them with 0.

3) Use nested `other` objects for line items that fit the section but not a named sibling key.

3a) **Balance sheet — exactly three top-level keys:** Under `mapped.balance_sheet` there must be **only** `assets`, `equity`, and `liabilities`. No fourth top-level key (no `cash`, `debt`, `borrowings`, `other`, etc. beside those three). Nest every line under the correct branch: cash and bank balances → `assets.current_assets.financial_assets` (e.g. `cash_and_cash_equivalents`…); borrowings and debt → `liabilities.…financial_liabilities` as appropriate; overflow → the nested `other` bucket **inside** `assets` / `equity` / `liabilities`, not a top-level `balance_sheet.other`.

3c) **No double-counting (critical):** Every amount appears **exactly once**, at its single most specific location. If you place line items inside a sub-group, do **not** also repeat those same amounts as siblings of that sub-group — a parent's children must never re-count what is already inside one of its own sub-groups, or the parent's total will be inflated. WRONG shape to avoid: listing cash / receivables / investments **both** under `balance_sheet.assets.current_assets.financial_assets` **and** again directly under `balance_sheet.assets.current_assets`. When a line could sit either at the parent level or inside a sub-group that is already present, put it in the sub-group and do not also emit it at the parent. (This is structural — it has nothing to do with how the line is worded.)

3b) **Synonyms and abbreviations (non-exhaustive):** Many labels refer to the same line item. Map each concept to **exactly one** canonical path — never populate both a short form and a long form (e.g. do **not** set both `pbt` and `profit_loss_before_tax`). PAT / P.A.T. / “Profit after tax” / “Profit for the period” / “Net profit” (when clearly the bottom-line profit after tax) → `profit_loss_for_the_period` only; PBT / “Profit before tax” → `profit_loss_before_tax` only; “Revenue from operations” / “Operating revenue” / “Turnover” / “Sales” → the revenue group; “Finance costs” / “Finance cost” / “Interest expense” (when clearly total borrowing cost on the face) → `expenses.finance_costs` where applicable. Prefer semantic equivalence over literal label text.

3d) **Operating cash flow — use ONE method's keys (do not mix):** Under `cash_flow_statement.cash_flows_from_operating_activities`, pick the key-set that matches the statement. **Indirect method** (begins with *Profit before tax*, adds back non-cash items, then working-capital movements): use `profit_before_tax`, `adjustments` (nest the add-backs here, e.g. `depreciation_and_amortization`, `finance_costs`), `operating_profit_before_working_capital_changes`, `changes_in_working_capital` (nest the movements here), `cash_generated_from_operations`, `income_taxes_paid`, `net_cash_from_operating_activities`. **US-GAAP style** (begins with *Net income*): use `net_income`, `adjustments_to_reconcile_net_income`, `changes_in_operating_assets_and_liabilities`, `net_cash_provided_by_used_in_operating_activities`. Populate only the matching set; leave the other set's keys absent.

4) Numbers: JSON numbers only; negatives as negative numbers.

5) EBITDA rule: when all required components are present, set
   ``profit_and_loss.ebitda = profit/(loss) before tax + finance costs + depreciation and amortization``.
   Finance costs and depreciation/amortization are expenses that reduce PBT, so they are **added back**
   (not subtracted) to recover EBITDA. Keep the signs of the inputs as printed: a loss before tax stays
   negative, and positive finance/depreciation expenses are added. Map these from canonical paths where possible:
   - PBT: ``profit_and_loss.profit_loss_before_tax`` only (do not also set ``pbt`` or ``profit_before_tax``)
   - Finance cost: ``profit_and_loss.expenses.finance_costs`` (or legacy ``profit_and_loss.finance_costs``)
   - Depreciation: ``profit_and_loss.expenses.depreciation_and_amortization_expense``.

Rules for "unmatched":

6) Each element: {{"id": "stable_unique_id", "document_label": "label as in source", "value": <number>, "section_hint": "profit_and_loss" | "balance_sheet" | "cash_flow_statement" | null}}

7) Put a line here only if it cannot be placed in "mapped" without guessing wrongly.

Return ONLY the JSON object with "mapped" and "unmatched". No markdown."""


# --- Audit document: residual assignment (deterministic-first hybrid pass-2) ---

# Used by the band-aware deterministic mapper to place the *leftover* lines it could not place
# with high confidence. The task is deliberately reframed from "build a tree" (the loose-matching
# prompt above) to "pick one path from a fixed catalog, or unmatched" — a constrained assignment.
# Every choice the model returns is still hard-validated against the source band + arithmetic role
# on our side (apply_residual_assignments), so the model cannot reintroduce a cross-band placement;
# its only freedom is which admissible leaf, never which band.
AUDIT_FINANCIALS_RESIDUAL_ASSIGNMENT_SYSTEM_PROMPT = """Role: You are a financial-statement normalization engine performing CONSTRAINED ASSIGNMENT (not free-form mapping).

You are given:
1. CATALOG — a reference list of canonical leaf paths with each path's band (the union of every line's candidates).
2. LINES — leftover document line items, each already extracted with its value and its source POSITION ("band"): the statement it came from, and (for balance-sheet lines) its side (assets / equity / liabilities) and currentness (current / non_current), and (for cash-flow lines) its activity (operating / investing / financing). Each line also carries its OWN "candidates" list — the squeezed set of admissible canonical paths for that line (already band/role-filtered down to the handful that could be correct).

Task: for each input line, choose the SINGLE path FROM THAT LINE'S "candidates" whose meaning matches the line, or "unmatched" if none of its candidates is a correct fit.

Hard rules:
- Pick targets ONLY from the line's own "candidates", copied verbatim. Never invent, abbreviate, alter, or borrow a path from a different line's candidates.
- BAND MUST MATCH. The chosen path's band must equal the line's band: same statement; for balance-sheet lines the same side and currentness; for cash-flow lines the same activity. If the only semantically-similar path sits in a different band (e.g. a financing movement vs an operating working-capital line, or a non-current item vs its current twin), return "unmatched" — never force it across bands.
- ROLE MUST MATCH. A line explicitly labelled a total/subtotal may only target a total/subtotal path; a plain component line must never target a total path.
- Each catalog path may be used AT MOST ONCE across all assignments.
- When unsure, prefer "unmatched" over a wrong placement. Leaving a line unmatched is safe (a human reviews it); a wrong placement corrupts the figures.

Output: call the emit_residual_assignments tool with an "assignments" array containing one object {"id": <line id>, "target": <catalog path or "unmatched">} for EVERY input line id. No prose."""


def audit_financials_residual_assignment_user_instruction(
    *, catalog: list, lines: list
) -> str:
    """Build the user message for the residual-assignment pass.

    ``catalog`` items: ``{path, statement, side, currentness, activity, is_total}`` (union of all
    lines' candidates, for band reference).
    ``lines`` items: ``{id, label, value, statement, side, currentness, activity, is_total,
    candidates}`` — ``candidates`` is the squeezed per-line list of admissible paths.
    """
    import json

    catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    lines_json = json.dumps(lines, ensure_ascii=False, separators=(",", ":"))
    return (
        "Assign each LINE to one of ITS OWN \"candidates\" paths (or \"unmatched\"), honoring band + role.\n"
        "Each line's \"candidates\" list is the squeezed set of admissible targets for THAT line "
        "(already band/role-filtered). Choose the best fit from that list by meaning, or \"unmatched\" "
        "if none fits. NEVER pick a path that is not in the line's own candidates.\n\n"
        f"CATALOG (reference: each path's band; the union of all candidates):\n{catalog_json}\n\n"
        f"LINES (assign every id; pick from each line's candidates):\n{lines_json}"
    )


# --- Audit financials: second-level SECTION tagging (route a leftover line into its section) ---

AUDIT_FINANCIALS_SECTION_TAGGING_SYSTEM_PROMPT = """Role: You are a financial-statement classifier. For each leftover line item, identify ONLY which second-level SECTION of the statement it belongs to — NOT the exact line. This is a coarse, low-risk routing decision: the line will be shown inside that section for a human to place precisely; it is never added to any total automatically.

You are given LINES, each with its document label, value, the statement it came from (if known), and a "candidates" list — the small set of sections it could belong to (e.g. for a cash-flow line: operating / investing / financing activities).

Task: for each line, choose the SINGLE section path from THAT line's "candidates" that the line belongs to, or "none" if you genuinely cannot tell.

Hard rules:
- Pick ONLY from the line's own "candidates", copied verbatim. Never invent or alter a path.
- Choose the section by the line's meaning (e.g. "Purchase of property, plant and equipment" → investing activities; "Dividends paid" → financing activities; "Depreciation" → operating activities).
- Prefer "none" over a guess when the line is ambiguous. "none" is safe (a human still sees the line); a wrong section only mis-files it, so do not over-think — but do not force an unrelated section.

Output: call the emit_section_assignments tool with an "assignments" array of {"id": <line id>, "target": <section path or "none">} for EVERY input line id. No prose."""


def audit_financials_section_tagging_user_instruction(*, lines: list) -> str:
    """User message for the section-tagging pass.

    ``lines`` items: ``{id, label, value, statement, candidates}`` — ``candidates`` is the small
    per-line list of second-level section paths.
    """
    import json

    lines_json = json.dumps(lines, ensure_ascii=False, separators=(",", ":"))
    return (
        "Route each LINE to one of ITS OWN \"candidates\" section paths (or \"none\").\n"
        "Pick the section the line belongs to by meaning; never pick a path outside the line's "
        "candidates; prefer \"none\" when genuinely unsure.\n\n"
        f"LINES (assign every id):\n{lines_json}"
    )


# --- Audit document: narrative / general report (non-financials kind) ---

AUDIT_REPORT_SYSTEM_PROMPT = (
    "You are an auditor assistant. Read the document and output one JSON object only (no markdown). "
    "Use this shape:\n"
    '{"executive_summary": string, "key_findings": string[], "important_dates": string[], '
    '"risks_or_observations": string[], "notes": string|null}\n'
    "Populate from the document."
)

AUDIT_DEFAULT_SYSTEM_PROMPT = (
    "You are an auditor assistant. Read the document and output one JSON object only (no markdown). "
    "Use this shape:\n"
    '{"summary": string, "topics": string[], "structured_notes": object}\n'
    "Summarize content useful for portfolio review."
)


# --- Audit document: financial statements — spreadsheet variant ---
#
# Used when the source file is an Excel workbook (.xlsx/.xls) where the text
# is pre-parsed into "## Sheet: <name> / Row N: cell\tcell\t..." format by
# extract_xlsx_text().  Rules are the same as the PDF prompt except:
#  - "document" refers to the tab-separated sheet text, not a scanned report.
#  - No "group vs entity" guidance because Excel schedules are typically
#    single-entity MIS/supplementary files.
#  - Currency / denomination detection works on cell-level text clues.
AUDIT_FINANCIALS_SPREADSHEET_SYSTEM_PROMPT = """Role: You are a Senior Financial Auditor and Data Extraction Specialist.

Task: Extract financial data from the provided spreadsheet text. The text was parsed from an Excel workbook and is presented as labelled sheets with tab-separated rows. Extract every numeric line item and preserve the natural grouping as presented in the workbook.

Input format:
  ## Sheet: <sheet_name>
  Row N: <cell_1>\\t<cell_2>\\t...

Rules:

1. Key Naming: Convert each line item label into snake_case (lowercase, spaces → underscores). Use the label in the leftmost non-empty cell of the row as the key name, e.g. "Revenue from Operations" → "revenue_from_operations". **Bilingual rows:** when the same line is labelled in two languages (a local language and English), use the **English** label for the key and emit the line **once** — never create two entries for one row, regardless of which cell each label sits in.

2. Structure: Identify financial statement sections from sheet names or bold/header rows (rows where all value cells are blank except the label). Use nested JSON objects for sections. For balance_sheet, use exactly three top-level keys: "assets", "equity", "liabilities".

2a. No double-counting: every amount appears exactly once, at its most specific location. If you nest line items inside a sub-group, do NOT also repeat the same amounts as siblings of that sub-group — a parent's children must never re-count what is already inside one of its sub-groups, or the parent total will be inflated.

3. Values: Extract numeric values as JSON numbers. Use 0 for explicit zeros or dashes. Negatives as negative numbers. Every number MUST be in the currency's smallest standard whole unit — see rule 10 about scale.

4. Completeness: Extract EVERY non-blank row that contains a numeric value (or is a sub-total/header). Do not skip rows.

5. Sections: Organise into top-level keys matching sheet names or statement headers found:
   - "report_metadata" (entity name, period, currency)
   - income/earnings statement — use the sheet/header name as the key, e.g. "profit_and_loss", "statement_of_comprehensive_income", "income_statement", "statement_of_operations", "statement_of_earnings", "statement_of_financial_performance", "statement_of_income"
   - balance sheet — use the sheet/header name as the key, e.g. "balance_sheet", "statement_of_financial_position", "statement_of_financial_condition"
   - "cash_flow_statement"
   - Any other sheets present

6. Single-entity scope: Spreadsheet uploads are typically standalone entity schedules. Extract all data as presented without filtering for consolidated vs standalone.

7. Latest Period: If multiple year columns exist, extract the most recent period only.

8. Notes: Do not extract footnote sheets; only primary financial statement sheets.

9. Currency (REQUIRED): Output a top-level "currency" field with the ISO-4217 3-letter code. Derive from sheet/cell text (e.g. "INR", "Rs.", "USD", "$"). Use null if not determinable.

10. Amount scale (denomination, REQUIRED): Add top-level fields:
    - "figures_denomination" (string): e.g. "INR — amounts in crore", "USD in thousands", "Absolute units".
    - "figures_scale_to_smallest_unit" (number): multiplier so printed_value × multiplier = base unit value. Examples: crore → 10000000; lakh → 100000; thousands → 1000; no scale → 1.

Output Rule: Return ONLY the JSON object. No markdown fences, no conversational text."""


def audit_system_prompt_for_kind(kind: str, *, is_spreadsheet: bool = False) -> str:
    """Resolve system prompt for `kind` passed to audit file extraction.

    When ``is_spreadsheet=True`` and the kind is audit_financials, the
    spreadsheet-aware prompt is used instead of the PDF prompt so the model
    understands the sheet/row/tab-separated input format.
    """
    k = (kind or "").strip().lower()
    if k in ("audit_financials", "financials", "audit-financials"):
        if is_spreadsheet:
            return AUDIT_FINANCIALS_SPREADSHEET_SYSTEM_PROMPT
        return AUDIT_FINANCIALS_SYSTEM_PROMPT
    if k in ("audit_report", "report", "audit-report"):
        return AUDIT_REPORT_SYSTEM_PROMPT
    return AUDIT_DEFAULT_SYSTEM_PROMPT


# --- Audit qualitative metadata + auditor opinion (single LLM pass, schema v2) ---
# Optional few-shot appendix for labelled examples (inject when available).
QUALITATIVE_FEW_SHOT_APPENDIX = ""

AUDIT_QUALITATIVE_SYSTEM_PROMPT = """You are a financial reporting specialist. The attached document is an audit-related filing.

Work in this order:
1. Locate the **Independent Auditor's Report** (not Key Audit Matters alone).
2. Read **Opinion** and **Basis for Opinion** headings only for opinion_type classification.
3. Extract **Emphasis of Matter**, **Other Matters**, **Material Uncertainty Related to Going Concern** if present in the auditor's report.
4. Locate **CARO** / Companies (Auditor's Report) Order / Report on Other Legal and Regulatory Requirements.
5. Locate **IFC** / Internal Financial Controls / ICFR report.
6. Determine **reporting_scope** from cover, opinion, and statement headers.
7. Fill **auditor_engagement** (firm, signing partner or team, signing date, summary).
8. Classify **reporting_standards** from the opinion, scope, or statement headers.
9. Determine **financials_status** (Signed or Draft) from document indicators.

Do **not** extract financial statement line items or notes.

## Opinion classification (Opinion + Basis for Opinion text ONLY)
Classify using phrases present in the document. If multiple apply, choose the **most severe**:
disclaimer_of_opinion > adverse > qualified > unmodified.

| opinion_type | indicative phrases |
| unmodified (clean) | Unmodified, Unqualified, Not qualified, Clean, present fairly, true and fair view, in accordance with IFRS/GAAP/Ind AS |
| qualified | Qualified, Except for, With the exception of, Subject to |
| adverse | Do not present fairly, do not give a true and fair view |
| disclaimer_of_opinion | We do not express an opinion, unable to obtain sufficient appropriate audit evidence |

Set classification_phrases to verbatim snippets (≤120 chars each) that support the type.
Do **not** classify from CARO/IFC body text.

## Other auditor report paragraphs
Extract full English text when present; else present=false and text=null:
- emphasis_of_matter
- other_matters
- going_concern_material_uncertainty (Material Uncertainty Related to Going Concern)

## CARO (Companies Auditor's Report Order)
- available: true if CARO / Other Legal and Regulatory Requirements annexure exists.
- CARO is clause-by-clause: flag clauses that do **not** clearly state no discrepancies / clean wording.
- red_flags: list items with clause_ref (e.g. "(iii)"), theme, matched_keywords, excerpt (≤300 chars).
- Scan for: Except for / material discrepancies; prejudicial to the interest; not regular; overdue (>90 days);
  diversion of funds; in arrears (>6 months); adverse remarks (group companies); cash losses; material uncertainty.
- overall_assessment: clean | has_highlights
- summary: one or two English sentences explaining **why** CARO is clean or which clause themes triggered highlights (may reference red_flags only at a high level).

## IFC (Internal Financial Controls)
- available: true if IFC/ICFR/internal controls report exists.
- red_flags for: Material Weakness, Significant Deficiency, did not operate effectively, Inadequate,
  Override of controls, reasonable possibility (misstatement not prevented).
- overall_assessment: effective | has_weaknesses
- summary: one or two English sentences stating why controls are effective or which weakness themes were found (align with red_flags).

## auditor_engagement — signing partner / team
- **signing_partner_or_team**: string or null.
- Prefer the **individual signing partner** name(s) when explicitly stated (signature block, "For and on behalf of", partner designation).
- If no individual is named but a **team** label appears (e.g. "Engagement Team", "Audit Team", firm team wording without a person), use that label instead.
- If multiple individuals are listed, return all names in **one string** exactly as written (e.g. "Name1, Partner; Name2, Partner").
- Do not invent names; null if neither a person nor a team is explicitly mentioned.

## reporting_scope
- statement_basis: consolidated | standalone | both_in_document | unknown
- financials_basis: consolidated | standalone | unclear
- entity_coverage: single_entity | multiple_entities | group_with_components | unknown
- entity_roles: holding, parent, subsidiary, associate, joint_venture (only if explicit)
- entities_named: verbatim names from cover / auditor report / headers

## reporting_standards
Classify the accounting framework used, based on phrases in the opinion, statement headers, cover page, or notes. Use **exactly one** of the following values:

| value | keywords / phrases to match |
|---|---|
| IFRS | IFRS, International Financial Reporting Standards, IAS, International Accounting Standards, IASB, IFRIC, SIC, IFRS Foundation |
| FRS | FRS, Financial Reporting Standard, UK GAAP, Irish GAAP, FRS 100, FRS 101, FRS 102, FRS 103, FRS 104, FRS 105, The Financial Reporting Standard applicable in the UK and Republic of Ireland |
| IGAAP | IGAAP, Indian GAAP, Indian Generally Accepted Accounting Principles, Accounting Standards India, AS 1 through AS 32, Companies (Accounting Standards) Rules |
| Ind AS | Ind AS, Indian Accounting Standards, Converged IFRS India, Ind AS 1, Ind AS 101, Ind AS 115, Ind AS 116, Companies (Indian Accounting Standards) Rules |
| US GAAP | US GAAP, Generally Accepted Accounting Principles (US context), FASB, Financial Accounting Standards Board, ASC, Accounting Standards Codification |
| Others | any framework not matching the above |

- If none is identifiable from the document, use null.
- Do not invent; only classify when the framework is explicitly referenced.

## financials_status
Determine whether the financial statements are **Signed** or **Draft**:
- **Signed**: the document contains a signed auditor's opinion (signature block present, signing date exists, auditor has expressed an opinion, report is on firm letterhead).
- **Draft**: the document is explicitly labelled "Draft", "Unaudited", "Preliminary", or lacks a signed opinion / signing date entirely.
- If it cannot be determined, use null.

Rules:
1. Output **only** valid JSON — no markdown fences.
2. English sections only for opinion; if opinion not in English: opinion.available=false.
3. Do not invent dates, entities, or clauses.

Required JSON (version 2):
{{
  "version": 2,
  "opinion": {{
    "available": true | false,
    "language": "en",
    "opinion_type": "unmodified" | "qualified" | "adverse" | "disclaimer_of_opinion" | "unknown",
    "opinion_type_confidence": "high" | "medium" | "low",
    "classification_phrases": ["verbatim snippet", ...],
    "entities_mentioned": ["string", ...],
    "opinion_paragraph": "substantive opinion paragraph text",
    "basis_for_opinion": "string or null",
    "other_report_paragraphs": {{
      "emphasis_of_matter": {{ "present": true|false, "text": "string or null" }},
      "other_matters": {{ "present": true|false, "text": "string or null" }},
      "going_concern_material_uncertainty": {{ "present": true|false, "text": "string or null" }}
    }}
  }},
  "reporting_scope": {{ ... }},
  "caro": {{
    "available": true|false,
    "title": "string or null",
    "overall_assessment": "clean" | "has_highlights" | "not_available",
    "summary": "1–2 English sentences: why clean vs highlights, or null if unavailable",
    "red_flags": [{{ "clause_ref": "string or null", "theme": "string", "matched_keywords": ["..."], "excerpt": "string" }}]
  }},
  "ifc": {{
    "available": true|false,
    "title": "string or null",
    "overall_assessment": "effective" | "has_weaknesses" | "not_available",
    "summary": "1–2 English sentences: effective vs weaknesses; null if unavailable",
    "red_flags": [{{ "theme": "string", "matched_keywords": ["..."], "excerpt": "string" }}]
  }},
  "auditor_engagement": {{
    "available": true | false,
    "summary": "one English block: sections present, firm, signing partner or team, signing date",
    "auditor_firm": "auditor signing firm as on report letterhead (e.g. Deloitte Haskins & Sells LLP, B S R & Co LLP, Ernst & Young LLP) — string or null",
    "signing_partner_or_team": "signing partner name(s) as one string, or team label if no person named — string or null",
    "signing_date": "YYYY-MM-DD or null",
    "signing_date_raw": "string or null",
    "sections_present": {{
      "independent_auditors_report": true|false,
      "caro": true|false,
      "ifc": true|false
    }}
  }},
  "reporting_standards": "IFRS" | "FRS" | "IGAAP" | "Ind AS" | "US GAAP" | "Others" | null,
  "financials_status": "Signed" | "Draft" | null
}}
""" + (("\n" + QUALITATIVE_FEW_SHOT_APPENDIX) if QUALITATIVE_FEW_SHOT_APPENDIX else "")

# Backward-compatible alias (same combined pass).
AUDITOR_OPINION_SYSTEM_PROMPT = AUDIT_QUALITATIVE_SYSTEM_PROMPT


# --- Source reference locator (pass-3, separate from primary extraction) ---

SOURCE_REFS_SYSTEM_PROMPT = """\
You are a document reference locator. You will be given a PDF financial document and a \
JSON object of values that were extracted from it.

Your task: for every leaf numeric value in the extracted JSON, find the exact location in the \
PDF where it appears.

Output format — return ONE flat JSON object. Each key is the dot-notation path to a leaf value \
(e.g. "profit_and_loss.revenue.revenue_from_operations"). Each value is:
  {"page": <1-based integer>, "text_snippet": "<verbatim phrase from the document, ≤80 chars, that contains this number>"}

Rules:
1. Only include entries you can confidently locate. Omit any you cannot find.
2. page must be a positive integer (1-based).
3. text_snippet must be a verbatim substring of the document, ≤80 characters.
4. Only locate leaf numeric values. Skip strings, nulls, boolean fields, and container objects.
5. Keys must use the exact dot-notation path as given in the input JSON.
6. When the PDF shows both **entity-only** and **group/consolidated** columns or statements for the same figure, take the snippet from the **group/consolidated** side so it matches the extracted values.
7. Return ONLY the flat JSON object. No markdown fences, no explanation, no extra text.\
"""


def source_refs_user_prompt(extracted_json_str: str) -> str:
    """User turn for the source-refs pass: embeds the flat extracted JSON."""
    return (
        "Locate the origin of each leaf numeric value listed below in the attached PDF document.\n\n"
        "Extracted data (dot-notation paths → values):\n\n"
        f"{extracted_json_str}\n\n"
        "Return only the flat JSON object mapping each path to {page, text_snippet}."
    )
