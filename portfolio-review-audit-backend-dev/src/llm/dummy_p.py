"""
Central LLM prompts for document extraction (org chart + audit files).

Services import from here only — do not duplicate prompt text in feature modules.
"""
from __future__ import annotations

# --- Org chart (entity hierarchy) ---

ORG_CHART_SYSTEM_PROMPT = """You are a data extraction assistant. Your only job is to read a corporate structure document and return a JSON array. Nothing else.

---
## STEP-BY-STEP PROCESS

Follow these steps in order. Do not skip any step.

**Step 1 — Find every entity.**
Scan the entire document. An entity is any named company, subsidiary, holding company, branch, or legal vehicle shown in a box, row, or bullet. List every one you find. Assign each a unique integer ID starting from 1.

**Step 2 — Find the root (ultimate parent).**
The root is the single entity at the very top of the chart with no parent above it. There is always exactly one root. Set `is_parent: true` for this entity only. Set `is_parent: false` for every other entity.

**Step 3 — Map direct parent → child relationships.**
For each entity, look only at the entity directly above it (connected by a line or arrow). Record that relationship. Do not skip levels — a grandchild is NOT a direct child of the grandparent.

**Step 4 — Populate `children` arrays.**
For every entity, list the IDs of its direct children only. If an entity has no children, use an empty array `[]`.

**Step 5 — Extract geolocation.**
Use the country or city/state written inside or beside each entity's box. If not stated, use `null`.

**Step 6 — Extract incorporation details.**
Look for a registration number, UEN, company number, or date of incorporation near each entity. If not present, use `null` for that field.

**Step 7 — Clean entity names.**
Remove all ownership percentages (e.g. "100%", "66.7% stake") and connector words like "via", "through", "held by" from the `entity_name` field. Keep only the legal name.

---
## OUTPUT SCHEMA

Return a JSON array. Each element must follow this exact structure:

```json
For every entity found in the document, create an object with this structure:
{
    "id": "Sequential integer starting from 1",
    "entity_name": "Full legal name of the entity",
    "geolocation": "Country or City/State of incorporation",
    "is_parent": true,
    "children": ["List of IDs of direct subsidiaries or step-down entities"]
}
```

---
## EXAMPLE

Given a chart: Alpha Holdings (Singapore) → Beta Pte Ltd (Singapore, 100%) → Gamma LLC (USA)

Correct output:
```json
[
{
"id": 1,
"entity_name": "Alpha Holdings",
"geolocation": "Singapore",
"is_parent": true,
},
"children": [2]
},
{
"id": 2,
"entity_name": "Beta Pte Ltd",
"geolocation": "Singapore",
"is_parent": false,
},
"children": [3]
},
{
"id": 3,
"entity_name": "Gamma LLC",
"geolocation": "USA",
"is_parent": false,

},
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
- Do not infer or guess relationships. Only extract what is explicitly shown in the document.
- If two entities share the same name but are distinct boxes in the diagram, assign them separate IDs."""

ORG_CHART_USER_INSTRUCTION = (
    "Extract the complete organizational hierarchy from the attached document "
    "and return only the JSON array as specified. Do not include any other text."
)

# --- Audit document: financial statements (primary) ---

AUDIT_FINANCIALS_SYSTEM_PROMPT = """Role: You are a Senior Financial Auditor and Data Extraction Specialist.

Task: Extract ALL financial data from the attached audit report exactly as it appears in the document. Do NOT map to a predefined schema. Instead, preserve the original structure, labels, and hierarchy of the document.

Rules:

1. Key Naming: Convert each line item label from the document into snake_case (lowercase, spaces replaced with underscores). For example:
   - "Property, Plant and Equipment" → "property_plant_and_equipment"
   - "Trade and Other Receivables" → "trade_and_other_receivables"
   - "Profit Before Tax" → "profit_before_tax"

2. Structure: Preserve the natural grouping and hierarchy as presented in the document. Use nested JSON objects for sections and sub-sections (e.g., "non_current_assets", "current_assets" nested under "assets").

3. Values: Extract numeric values as numbers (not strings). Use 0 for dashes or blanks. Negative values should be represented as negative numbers.

4. Completeness: Extract EVERY line item that appears in the financial statements. Do not skip or merge items. If a line item exists in the document, it must appear in the output.

5. Sections: Organize the output into top-level sections matching the document structure. Common sections include:
   - "report_metadata" (entity name, UEN, reporting period, currency, audit firm, etc.)
   - income/earnings statement — use the document's own header as the key, e.g. "profit_and_loss", "statement_of_comprehensive_income", "income_statement", "statement_of_operations", "statement_of_earnings", "statement_of_financial_performance", "statement_of_income"
   - balance sheet — use the document's own header as the key, e.g. "balance_sheet", "statement_of_financial_position", "statement_of_financial_condition"
   - "cash_flow_statement" (if present)
   - "statement_of_changes_in_equity" (if present)
   - Any other statements present in the document

6. Latest Period: Only extract values for the most recent financial period/year shown.

7. Notes: Do not extract notes to the financial statements, only the primary financial statement figures.

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
AUDIT_FINANCIALS_SCHEMA_FALLBACK: dict = {
    "profit_and_loss": {
        "revenue": {},
        "cost_of_sales": {},
        "gross_profit": {},
        "other_income": {},
        "operating_expenses": {},
        "finance_income": {},
        "finance_costs": {},
        "profit_before_tax": {},
        "tax": {},
        "profit_after_tax": {},
        "ebitda": {},
        "pbt": {},
        "pat": {},
        "other": {},
    },
    "balance_sheet": {
        "assets": {
            "non_current_assets": {"other": {}},
            "current_assets": {"other": {}},
            "other": {},
        },
        "liabilities": {
            "non_current_liabilities": {"other": {}},
            "current_liabilities": {"other": {}},
            "other": {},
        },
        "equity": {"other": {}},
        "cash": {},
        "debt": {},
        "other": {},
    },
    "cash_flow_statement": {
        "cash_flows_from_operating_activities": {"other": {}},
        "cash_flows_from_investing_activities": {"other": {}},
        "cash_flows_from_financing_activities": {"other": {}},
        "net_increase_decrease_in_cash_and_cash_equivalents": {},
        "cash_and_cash_equivalents_at_beginning_of_period": {},
        "cash_and_cash_equivalents_at_end_of_period": {},
        "other": {},
    },
}


def audit_financials_schema_mapping_system_prompt(*, schema: dict) -> str:
    """
    Second-pass mapping: canonical ``mapped`` tree + ``unmatched`` rows for human-in-the-loop review.

    Absent lines: omit keys (do not use 0 as a stand-in for “not in document”).
    """
    import json

    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))

    return f"""Role: You are a Senior Financial Auditor and Data Normalization Specialist.

Task: You will receive raw extracted JSON from a financial statement. Map it into the canonical schema using **loose semantic matching**: different wordings for the same concept belong in the same bucket (e.g. "Revenue from operations" and "Operating revenue" → the same revenue group).

Output format — return ONE JSON object with exactly these top-level keys:
- "mapped": object — the canonical financial tree.
- "unmatched": array — each row is a document line you could not place under a canonical key (even under an appropriate `other`).

Canonical schema for the "mapped" object (keep top-level keys exactly: profit_and_loss, balance_sheet, cash_flow_statement; do not add or remove them):
{schema_json}

Rules for "mapped":

1) Only use keys that exist in the canonical schema, plus dynamic snake_case keys you add **under** the correct parent object when the schema shows {{}} placeholders.

2) Presence vs zero (critical):
   - **Omit** a leaf key if that line does **not** appear in the document. Do **not** use 0 to mean "missing"; users must be able to tell "not in document" from "reported as zero".
   - Use **0** only when the document explicitly shows zero for that line.
   - Prefer omitting unused schema keys entirely over filling them with 0.

3) Use nested `other` objects for line items that fit the section but not a named sibling key.

4) Numbers: JSON numbers only; negatives as negative numbers.

Rules for "unmatched":

5) Each element: {{"id": "stable_unique_id", "document_label": "label as in source", "value": <number>, "section_hint": "profit_and_loss" | "balance_sheet" | "cash_flow_statement" | null}}

6) Put a line here only if it cannot be placed in "mapped" without guessing wrongly.

Return ONLY the JSON object with "mapped" and "unmatched". No markdown."""

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


def audit_system_prompt_for_kind(kind: str) -> str:
    """Resolve system prompt for `kind` passed to audit file extraction (e.g. audit_financials, audit_report)."""
    k = (kind or "").strip().lower()
    if k in ("audit_financials", "financials", "audit-financials"):
        return AUDIT_FINANCIALS_SYSTEM_PROMPT
    if k in ("audit_report", "report", "audit-report"):
        return AUDIT_REPORT_SYSTEM_PROMPT
    return AUDIT_DEFAULT_SYSTEM_PROMPT
