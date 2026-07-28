"""
v2 quarter-aware Snowflake PR formulas: ``PRSubmissionDataRaw`` → ``FinancialDataSnowflake``.

This supersedes the v1 4-bucket model (``snowflake_pr_financial_mapping``). The v1
model conflated the *year choice* (Year 1 vs Year 2) into a hand-written formula
per calendar quarter. v2 separates the two concerns the finance logic actually has:

* **What a metric is** (which columns compose revenue/ebitda/… ) — configured here.
* **Which MIS record / which year / exact-quarter for balances** — decided
  deterministically by :mod:`src.services.snowflake_pr_match`, not by config.

So the config axis is no longer 4 calendar buckets but the two real distinctions:
P&L vs Balance sheet, and (for Growth/Venture P&L only) aligned (Year 2) vs lagged
(Year 1).

Stored in ``ConfigTable`` under :data:`SNOWFLAKE_PR_V2_CONFIG_KEY`.

Config shape::

    {
        "stage_groups": {
            "surge_seed": {
                "pnl":     {"revenue": {"formula": "revenue_yr_1"}, "ebitda": {...}, "pbt": {...}, "pat": {...}},
                "balance": {"cash": {"formula": "cash_on_hand"}, "debt": {"formula": "debt_total"}}
            },
            "growth_venture": {
                "pnl_aligned": {"revenue": {"formula": "revenue_yr_2"}, ...},   # Year 2
                "pnl_lagged":  {"revenue": {"formula": "revenue_yr_1"}, ...},   # Year 1
                "balance":     {"cash": {...}, "debt": {...}}
            }
        }
    }
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.models import ConfigTable, PRSubmissionDataRaw
from src.services.snowflake_pr_financial_mapping import (
    SNOWFLAKE_PR_AUDIT_LABELS,
    SNOWFLAKE_PR_STAGE_GROUP_IDS,
    SNOWFLAKE_PR_STAGE_GROUPS,
)
from src.services.snowflake_pr_formula_eval import (
    FormulaEvaluationError,
    numeric_identifier_whitelist_from_pr_submission_model,
    validate_formula,
)

SNOWFLAKE_PR_V2_CONFIG_KEY = "snowflake_pr_financial_metric_mapping_v2"

# The two metric families (see snowflake_pr_match for why they differ).
PNL_METRICS: tuple[str, ...] = ("revenue", "ebitda", "pbt", "pat")
BALANCE_METRICS: tuple[str, ...] = ("cash", "debt")

# Slots per stage group, and which metrics each slot carries.
V2_GROUP_SLOTS: dict[str, tuple[str, ...]] = {
    "surge_seed": ("pnl", "balance"),
    "growth_venture": ("pnl_aligned", "pnl_lagged", "balance"),
}
V2_SLOT_METRICS: dict[str, tuple[str, ...]] = {
    "pnl": PNL_METRICS,
    "pnl_aligned": PNL_METRICS,
    "pnl_lagged": PNL_METRICS,
    "balance": BALANCE_METRICS,
}
V2_SLOT_LABELS: dict[str, str] = {
    "pnl": "P&L (Year 1)",
    "pnl_aligned": "P&L · aligned (Year 2)",
    "pnl_lagged": "P&L · lagged (Year 1)",
    "balance": "Balance sheet",
}


# ---------------------------------------------------------------------------
# Default seed formulas (used by the migration / first-run seeding)
# ---------------------------------------------------------------------------


def default_v2_stage_groups() -> dict[str, dict[str, dict[str, str]]]:
    """Sensible starting formulas; finance edits these in Settings afterwards.

    P&L → annual yr_1/yr_2 columns; balances → point-in-time columns.
    EBITDA defaults to ``ebitda_yr_n`` (not ``ebitda_accepted_yr_n``) — confirm
    the agreed comparison base before relying on it.
    """
    return {
        "surge_seed": {
            "pnl": {
                "revenue": "revenue_yr_1",
                "ebitda": "ebitda_yr_1",
                "pbt": "pbt_yr_1",
                "pat": "pat_yr_1",
            },
            "balance": {"cash": "cash_on_hand", "debt": "debt_total"},
        },
        "growth_venture": {
            "pnl_aligned": {
                "revenue": "revenue_yr_2",
                "ebitda": "ebitda_yr_2",
                "pbt": "pbt_yr_2",
                "pat": "pat_yr_2",
            },
            "pnl_lagged": {
                "revenue": "revenue_yr_1",
                "ebitda": "ebitda_yr_1",
                "pbt": "pbt_yr_1",
                "pat": "pat_yr_1",
            },
            "balance": {"cash": "cash_on_hand", "debt": "debt_total"},
        },
    }


# ---------------------------------------------------------------------------
# Resolve / normalise
# ---------------------------------------------------------------------------


def _extract_formula(raw: Any) -> str:
    if isinstance(raw, dict):
        f = raw.get("formula")
        return f.strip() if isinstance(f, str) else ""
    if isinstance(raw, str):
        return raw.strip()
    return ""


def resolve_snowflake_pr_v2_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise a stored payload to the full resolved shape.

    Returns ``{"groups": {group_id: {slot: {metric: formula}}}}`` with every
    group/slot/metric present (empty strings where unconfigured).
    """
    payload = payload if isinstance(payload, dict) else {}
    stored = payload.get("stage_groups") if isinstance(payload.get("stage_groups"), dict) else {}
    groups: dict[str, dict[str, dict[str, str]]] = {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        g_raw = stored.get(group_id) if isinstance(stored.get(group_id), dict) else {}
        slots: dict[str, dict[str, str]] = {}
        for slot in V2_GROUP_SLOTS[group_id]:
            s_raw = g_raw.get(slot) if isinstance(g_raw.get(slot), dict) else {}
            slots[slot] = {m: _extract_formula(s_raw.get(m)) for m in V2_SLOT_METRICS[slot]}
        groups[group_id] = slots
    return {"groups": groups}


async def load_snowflake_pr_v2_full(db: AsyncSession) -> dict[str, Any]:
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY)))
        .scalars()
        .first()
    )
    payload = dict(row.value) if row and isinstance(row.value, dict) else {}
    return resolve_snowflake_pr_v2_config(payload)


def load_snowflake_pr_v2_full_sync(session: Session) -> dict[str, Any]:
    row = (
        session.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY))
        .scalars()
        .first()
    )
    payload = dict(row.value) if row and isinstance(row.value, dict) else {}
    return resolve_snowflake_pr_v2_config(payload)


# ---------------------------------------------------------------------------
# Validate / persist
# ---------------------------------------------------------------------------


def _identifiers_allowlist() -> frozenset[str]:
    return frozenset(numeric_identifier_whitelist_from_pr_submission_model(PRSubmissionDataRaw))


def validated_v2_slot_formulas(
    slot: str,
    payload_metrics: dict[str, Any],
    *,
    allowed_identifiers: frozenset[str],
) -> dict[str, str]:
    """Validate one slot's formulas; empty formulas are allowed (metric skipped)."""
    out: dict[str, str] = {}
    for metric in V2_SLOT_METRICS[slot]:
        formula_str = _extract_formula(payload_metrics.get(metric))
        if not formula_str:
            out[metric] = ""
            continue
        try:
            validate_formula(formula_str, allowed_identifiers=allowed_identifiers)
        except FormulaEvaluationError as exc:
            raise ValueError(f"{metric}: {exc}") from exc
        out[metric] = formula_str
    return out


async def persist_snowflake_pr_v2(
    db: AsyncSession,
    *,
    stage_groups: dict[str, dict[str, dict[str, str]]],
) -> dict[str, Any]:
    """Persist validated v2 stage_groups and return the stored dict.

    ``stage_groups`` is ``{group_id: {slot: {metric: formula_str}}}``.
    """
    stored_groups: dict[str, Any] = {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        group_in = stage_groups.get(group_id) or {}
        stored_groups[group_id] = {
            slot: {
                m: {"formula": (group_in.get(slot) or {}).get(m, "")}
                for m in V2_SLOT_METRICS[slot]
            }
            for slot in V2_GROUP_SLOTS[group_id]
        }
    body: dict[str, Any] = {"stage_groups": stored_groups}
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_V2_CONFIG_KEY)))
        .scalars()
        .first()
    )
    if row is None:
        row = ConfigTable(
            key=SNOWFLAKE_PR_V2_CONFIG_KEY,
            value=body,
            description="Snowflake PR submission → FinancialDataSnowflake metric formulas (v2)",
        )
        db.add(row)
    else:
        row.value = body
    await db.flush()
    await db.refresh(row)
    return dict(row.value) if isinstance(row.value, dict) else body


# ---------------------------------------------------------------------------
# Audit diff helpers
# ---------------------------------------------------------------------------


def snowflake_pr_v2_audit_diff_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable diff of two resolved v2 configs (see resolve_*)."""
    lines: list[str] = []
    b_groups = before.get("groups", {}) if isinstance(before.get("groups"), dict) else {}
    a_groups = after.get("groups", {}) if isinstance(after.get("groups"), dict) else {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        group_label = SNOWFLAKE_PR_STAGE_GROUPS[group_id]["label"]
        b_group = b_groups.get(group_id, {}) if isinstance(b_groups.get(group_id), dict) else {}
        a_group = a_groups.get(group_id, {}) if isinstance(a_groups.get(group_id), dict) else {}
        for slot in V2_GROUP_SLOTS[group_id]:
            slot_label = V2_SLOT_LABELS.get(slot, slot)
            b_slot = b_group.get(slot, {}) if isinstance(b_group.get(slot), dict) else {}
            a_slot = a_group.get(slot, {}) if isinstance(a_group.get(slot), dict) else {}
            for metric in V2_SLOT_METRICS[slot]:
                bf = (b_slot.get(metric) or "").strip()
                af = (a_slot.get(metric) or "").strip()
                if bf != af:
                    metric_label = SNOWFLAKE_PR_AUDIT_LABELS.get(metric, metric)
                    lines.append(f"{group_label} · {slot_label} · {metric_label}: «{bf}» → «{af}»")
    return lines


def snowflake_pr_v2_audit_user_summary(*, change_lines: list[str]) -> str:
    if not change_lines:
        return (
            "Snowflake PR financial formulas saved.\n• No formula changes "
            "(compared to the configuration prior to this save)."
        )
    body = "\n".join(f"• {line}" for line in change_lines)
    return f"Snowflake PR financial formulas updated\n\n{body}"
