"""
Single source of truth for canonical-field **bands** and **synonyms**.

This module is the leaf dependency for both the deterministic label matcher
(:mod:`src.services.financial_label_matching`) and the band-aware deterministic mapper
(:mod:`src.services.financial_deterministic_mapping`). It must not import either of them
(keep it dependency-free so there is no import cycle).

Two concepts live here:

1. **Band** — the positional coordinates of a line in a financial statement, the signal that
   disambiguates labels which are shared across positions ("Borrowings", "Trade receivables",
   "Provisions", "Investments" appear under both current and non-current; financing lines look
   like working-capital lines; depreciation appears in both the P&L and the cash-flow add-backs).
   A band has up to four dimensions:

     - ``statement``   — profit_and_loss / balance_sheet / cash_flow_statement
     - ``side``        — assets / equity / liabilities          (balance sheet only)
     - ``currentness`` — current / non_current                  (balance sheet only)
     - ``activity``    — operating / investing / financing      (cash flow only)

   :func:`band_for_path` derives the band of a canonical leaf from its canonical ancestor path;
   :func:`band_for_source_headers` derives the band of a *document* line from the chain of source
   headers above it. :func:`bands_compatible` is the hard gate: a candidate is admissible unless a
   dimension is *known on both sides* and conflicts (unknown is always permissive, so band-locking
   can only prevent cross-band placement, never increase misses).

2. **Synonyms** — curated surface forms for the metric-relevant canonical leaves, so the matcher
   recognises "Operating revenue" / "Turnover" / "Net sales" as the same line as
   ``revenue_from_operations``. Non-exhaustive on purpose; reviewer-confirmed aliases grow the rest.
"""
from __future__ import annotations

import re
from typing import Optional

# Statement / band vocabulary ---------------------------------------------------------------

STATEMENTS: tuple[str, ...] = ("profit_and_loss", "balance_sheet", "cash_flow_statement")
BALANCE_SHEET_SIDES: tuple[str, ...] = ("assets", "equity", "liabilities")
CURRENTNESS: tuple[str, ...] = ("current", "non_current")
CASH_FLOW_ACTIVITIES: tuple[str, ...] = ("operating", "investing", "financing")

# A band is a plain dict so it serialises trivially and is easy to assert in tests.
Band = dict  # {"statement", "side", "currentness", "activity"} → Optional[str]


def _norm(s: str) -> str:
    """Lowercase + collapse non-alphanumerics to single underscores (header/key matching)."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _empty_band() -> Band:
    return {"statement": None, "side": None, "currentness": None, "activity": None}


# Top-level statement headers we explicitly DO NOT reconcile. A line whose ROOT header is one of
# these is out of scope — it must return ``None`` even though its child keys may contain words like
# "profit_loss" or "comprehensive_income" (the SOCE movement rows are the classic trap: e.g.
# ``statement_of_changes_in_equity.profit_loss_for_the_year_fy2025.total_attributable_to_owners``).
_OUT_OF_SCOPE_ROOT_MARKERS: tuple[str, ...] = (
    "changes_in_equity",  # statement_of_changes_in_equity / consolidated_statement_of_changes_in_equity
    "changes_in_shareholders_equity",
    "statement_of_changes",
    "notes",  # notes to the financial statements
    "accounting_policies",
)


def _is_out_of_scope_root(root_norm: str) -> bool:
    return any(marker in root_norm for marker in _OUT_OF_SCOPE_ROOT_MARKERS)


def detect_statement(parts: list[str]) -> Optional[str]:
    """Canonical statement implied by a chain of (raw or canonical) header keys.

    Returns one of :data:`STATEMENTS`, or ``None`` when the line sits outside the three reconciled
    statements (statement of changes in equity, notes, accounting policies, …).

    Disambiguation order matters:
    1. If the **root** (top-level) header is an out-of-scope statement (SOCE / notes), bail with
       ``None`` *before* any keyword scan — otherwise deep child keys like ``profit_loss_for_the_year``
       or ``total_comprehensive_income`` would mis-tag SOCE movement rows as profit_and_loss.
    2. Cash flow next: its headers are unambiguous and may co-occur with income/profit add-backs.
    3. Balance sheet, then profit-and-loss, by header keywords.
    """
    joined = [_norm(p) for p in parts]
    if not joined:
        return None
    if _is_out_of_scope_root(joined[0]):
        return None
    blob = " ".join(joined)
    if "cash_flow" in blob or "cash_flows" in blob or "statement_of_cash_flows" in blob:
        return "cash_flow_statement"
    if (
        "balance_sheet" in blob
        or "financial_position" in blob
        or "non_current" in blob
        or any(p in BALANCE_SHEET_SIDES for p in joined)
    ):
        return "balance_sheet"
    if (
        "profit_and_loss" in blob
        or "profit_or_loss" in blob
        or "profit_loss" in blob
        or "comprehensive_income" in blob
        or "income_statement" in blob
        or "statement_of_profit" in blob
    ):
        return "profit_and_loss"
    return None


def _currentness(blob: str) -> Optional[str]:
    # Check non_current first — "current" is a substring of "non_current".
    if "non_current" in blob or "noncurrent" in blob:
        return "non_current"
    if "current" in blob:
        return "current"
    return None


def _bs_side(blob: str) -> Optional[str]:
    if "liabilit" in blob:
        return "liabilities"
    if "equity" in blob or "reserves_and_surplus" in blob:
        return "equity"
    if "asset" in blob:
        return "assets"
    return None


def _cf_activity(blob: str) -> Optional[str]:
    if "operating" in blob:
        return "operating"
    if "investing" in blob:
        return "investing"
    if "financing" in blob:
        return "financing"
    return None


def band_for_path(full_path: str) -> Band:
    """Band of a canonical leaf, derived from its canonical ancestor path.

    Example: ``balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings``
    → ``{statement: balance_sheet, side: liabilities, currentness: non_current, activity: None}``.
    """
    parts = [_norm(p) for p in (full_path or "").split(".") if p]
    band = _empty_band()
    if not parts:
        return band
    statement = parts[0]
    band["statement"] = statement
    blob = " ".join(parts)
    if statement == "balance_sheet":
        band["side"] = parts[1] if len(parts) > 1 and parts[1] in BALANCE_SHEET_SIDES else _bs_side(blob)
        band["currentness"] = _currentness(blob)
    elif statement == "cash_flow_statement":
        band["activity"] = _cf_activity(blob)
    return band


def band_for_source_headers(header_parts: list[str], statement: Optional[str] = None) -> Band:
    """Band of a *document* line, derived from the chain of source headers above it.

    ``statement`` may be passed when already detected; otherwise it is inferred from the headers.
    """
    band = _empty_band()
    stmt = statement or detect_statement(header_parts)
    band["statement"] = stmt
    blob = " ".join(_norm(p) for p in header_parts)
    if stmt == "balance_sheet":
        band["side"] = _bs_side(blob)
        band["currentness"] = _currentness(blob)
    elif stmt == "cash_flow_statement":
        band["activity"] = _cf_activity(blob)
    return band


def bands_compatible(src: Band, leaf: Band) -> bool:
    """Admissible unless a shared dimension is *known on both sides* and conflicts.

    Unknown on either side is permissive: band-locking only prevents a positive cross-band
    contradiction (e.g. current vs non-current, financing vs operating), so it can never turn a
    would-be match into a miss for lack of context.
    """
    for dim in ("statement", "side", "currentness", "activity"):
        a, b = src.get(dim), leaf.get(dim)
        if a and b and a != b:
            return False
    return True


# --- curated synonyms ---------------------------------------------------------------------
#
# Surface forms for the metric-relevant leaves (the lines that feed the six reconciled metrics).
# Keyed by canonical full path. Non-exhaustive on purpose — reviewer-confirmed aliases (the
# ``audit_financials_label_aliases_v1`` memory) grow the long tail from real activity.
CANONICAL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "profit_and_loss.revenue.revenue_from_operations": (
        "operating revenue",
        "revenue from operations",
        "revenue",
        "turnover",
        "sales",
        "net sales",
        "income from operations",
        # IFRS / US-GAAP top-line synonyms.
        "total net revenue",
        "net revenues",
        "total revenues",
    ),
    "profit_and_loss.revenue.other_income": ("other income", "other revenue", "other item of income"),
    "profit_and_loss.revenue.total_income": ("total income", "total revenue and other income"),
    "profit_and_loss.profit_loss_before_tax": (
        "pbt",
        "profit before tax",
        "profit loss before tax",
        "profit before taxation",
    ),
    "profit_and_loss.profit_loss_for_the_period": (
        "pat",
        "profit after tax",
        "net profit",
        "net loss for the year",
        "profit for the period",
        "profit for the year",
        "profit loss for the period",
        # IFRS / US-GAAP bottom-line synonyms.
        "net income",
        "net income loss",
        "net earnings",
    ),
    "profit_and_loss.profit_loss_from_discontinued_operations": (
        "profit loss from discontinued operations",
        "loss from discontinued operations",
    ),
    "profit_and_loss.total_comprehensive_income_for_the_period": (
        "total comprehensive income for the period",
        "total comprehensive income for the year",
    ),
    "profit_and_loss.expenses.finance_costs": (
        "finance cost",
        "finance costs",
        "interest expense",
        "borrowing costs",
    ),
    "profit_and_loss.expenses.employee_benefit_expense": (
        "employee benefit expense",
        "employee benefits expense",
        "staff costs",
        "personnel expenses",
        "salaries and wages",
    ),
    "profit_and_loss.expenses.other_expenses": (
        "other expenses",
        "other operating expenses",
        "administrative expenses",
        # US-GAAP operating-expense captions map to the catch-all expense leaf.
        "selling general and administrative expenses",
        "general and administrative expenses",
        "selling and marketing expenses",
        "research and development expenses",
    ),
    "profit_and_loss.expenses.total_expenses": (
        "total expenses",
        "total items of expense",
        "total expense",
    ),
    "profit_and_loss.expenses.cost_of_material_consumed": (
        "cost of materials consumed",
        "cost of material consumed",
        "cost of goods sold",
        "cost of sales",
        # IFRS / US-GAAP cost-of-sales synonyms.
        "cost of revenue",
        "cost of goods and services sold",
    ),
    "profit_and_loss.tax_expense.current_tax": (
        "current tax",
        "current tax expense",
        "current tax expenses",
    ),
    "profit_and_loss.tax_expense.deferred_tax": (
        "deferred tax",
        "deferred tax credit",
        "deferred tax charge",
        "deferred tax expense",
    ),
    "profit_and_loss.expenses.depreciation_and_amortization_expense": (
        "depreciation and amortisation",
        "depreciation and amortization",
        "depreciation",
        "amortisation",
        "depreciation and amortisation expense",
    ),
    "balance_sheet.assets.current_assets.financial_assets.cash_and_cash_equivalents": (
        "cash",
        "cash and bank",
        "cash and cash equivalents",
        "cash equivalents",
    ),
    "balance_sheet.assets.current_assets.financial_assets.bank_balances_other_than_cash_and_cash_equivalents": (
        "bank balances",
        "other bank balances",
    ),
    "balance_sheet.liabilities.non_current_liabilities.financial_liabilities.borrowings": (
        "borrowings",
        "long term borrowings",
        "non current borrowings",
        "debt",
        "long term debt",
    ),
    "balance_sheet.liabilities.current_liabilities.financial_liabilities.borrowings": (
        "borrowings",
        "short term borrowings",
        "current borrowings",
        "current debt",
    ),
    # --- standard Ind-AS / Schedule III captions (closed statutory vocabulary) ---
    # These are framework-standard line captions reused across every Indian report; they are NOT
    # company-specific (those go to alias memory). Keep additions here limited to standard captions.
    "balance_sheet.equity.other_equity": (
        "other equity",
        "reserves and surplus",
        "reserves & surplus",
    ),
    "balance_sheet.assets.non_current_assets.financial_assets.loans": (
        "loans",
        "long term loans and advances",
        "non current loans",
    ),
    "balance_sheet.assets.current_assets.financial_assets.loans": (
        "short term loans and advances",
        "current loans",
        "loans and advances",
    ),
    "balance_sheet.assets.current_assets.financial_assets.investments": (
        "investments",
        "current investments",
    ),
    "balance_sheet.assets.current_assets.financial_assets.trade_receivables": (
        "trade receivables",
        "trade and other receivables",
        "sundry debtors",
    ),
    "balance_sheet.liabilities.current_liabilities.financial_liabilities.trade_payables.total_trade_payables": (
        "trade payables",
        "trade and other payables",
        "sundry creditors",
    ),
    "balance_sheet.liabilities.non_current_liabilities.provisions": (
        "long term provisions",
        "non current provisions",
    ),
    "balance_sheet.liabilities.current_liabilities.provisions": (
        "short term provisions",
        "current provisions",
    ),
    # --- IFRS / US-GAAP standard captions (closed cross-framework vocabulary) ---
    # The canonical schema is Ind-AS Schedule III shaped, so IFRS/US-GAAP face captions are mapped
    # onto the nearest equivalent leaf. Standard captions only; entity-specific wording → alias memory.
    "balance_sheet.assets.non_current_assets.property_plant_and_equipment": (
        "property plant and equipment",
        "property and equipment",
        "net property plant and equipment",
    ),
    "balance_sheet.assets.non_current_assets.goodwill": ("goodwill",),
    "balance_sheet.assets.non_current_assets.other_intangible_assets": (
        "intangible assets",
        "intangible assets net",
        "other intangible assets",
    ),
    "balance_sheet.assets.current_assets.inventories": (
        "inventories",
        "inventory",
        "inventories net",
    ),
}


def synonyms_for_path(full_path: str) -> tuple[str, ...]:
    """Curated surface-form synonyms for a canonical leaf path (empty tuple if none)."""
    return CANONICAL_SYNONYMS.get(full_path, ())
