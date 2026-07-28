"""
Quarter-aware Snowflake PR formulas: ``PRSubmissionDataRaw`` → ``FinancialDataSnowflake``.

Stored in ``ConfigTable`` under :data:`SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY`.

Config shape (v2, quarter-bucket-aware):
    {
        "stage_groups": {
            "surge_seed": {
                "03-31": { "revenue": {"formula": "..."}, ... },
                "06-30": { ... },
                "09-30": { ... },
                "12-31": { ... },
            },
            "growth_venture": { ... same buckets ... }
        }
    }

Only ``surge_seed`` and ``growth_venture`` are recognised stage groups.  Any
``PortfolioCompany.investment_stage`` that does not resolve to one of those groups
causes the PR row to be skipped (no upsert).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.models import ConfigTable, PRSubmissionDataRaw
from src.services.snowflake_pr_formula_eval import (
    FormulaEvaluationError,
    numeric_identifier_whitelist_from_pr_submission_model,
    validate_formula,
)

SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY = "snowflake_pr_financial_metric_mapping_v1"

SNOWFLAKE_PR_FINANCIAL_METRICS: tuple[str, ...] = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")

SNOWFLAKE_PR_AUDIT_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "pbt": "PBT",
    "pat": "PAT",
    "cash": "Cash",
    "debt": "Debt",
}

# Ordered for stable display / audit output.
SNOWFLAKE_PR_STAGE_GROUP_IDS: tuple[str, ...] = ("surge_seed", "growth_venture")

SNOWFLAKE_PR_STAGE_GROUPS: dict[str, dict[str, Any]] = {
    "surge_seed": {"label": "Surge / Seed", "stages": ("surge", "seed", "seed/surge")},
    "growth_venture": {"label": "Growth / Venture", "stages": ("growth", "venture", "venture/growth")},
}

# Reporting-date bucket keys in display/storage order.
SNOWFLAKE_PR_REPORTING_DATE_BUCKETS: tuple[str, ...] = ("03-31", "06-30", "09-30", "12-31")

SNOWFLAKE_PR_REPORTING_DATE_BUCKET_LABELS: dict[str, str] = {
    "03-31": "Mar Submission",
    "06-30": "Jun Submission",
    "09-30": "Sep Submission",
    "12-31": "Dec Submission",
}

# Both stage groups use per-bucket (quarter-aware) formulas.
SNOWFLAKE_PR_STAGE_GROUP_BUCKET_AWARE: dict[str, bool] = {
    "surge_seed": True,
    "growth_venture": True,
}

# Maps fye month name (lowercase) → reporting-date bucket suffix (MM-DD).
_FYE_MONTH_TO_BUCKET: dict[str, str] = {
    "jan": "03-31",
    "feb": "03-31",
    "mar": "03-31",
    "apr": "06-30",
    "may": "06-30",
    "jun": "06-30",
    "jul": "09-30",
    "aug": "09-30",
    "sep": "09-30",
    "oct": "12-31",
    "nov": "12-31",
    "dec": "12-31",
}


# ---------------------------------------------------------------------------
# Stage-group resolution
# ---------------------------------------------------------------------------


def resolve_stage_group(investment_stage: Optional[str]) -> Optional[str]:
    """Map ``PortfolioCompany.investment_stage`` to a formula-group id.

    Returns the group id (e.g. ``"surge_seed"``) or ``None`` when the stage is
    blank / unknown.  Callers must skip the row when ``None`` is returned.
    """
    if not isinstance(investment_stage, str):
        return None
    s = investment_stage.strip().lower()
    if not s:
        return None
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        if s in SNOWFLAKE_PR_STAGE_GROUPS[group_id]["stages"]:
            return group_id
    return None


# ---------------------------------------------------------------------------
# fye + reporting_date bucket validation
# ---------------------------------------------------------------------------


def fye_to_reporting_bucket(normalized_fye: Optional[str]) -> Optional[str]:
    """Return the expected reporting-date bucket suffix for a normalised fye string.

    ``normalized_fye`` is in the form ``"Dec-24"`` (as returned by
    ``normalize_fye_raw``).  Returns e.g. ``"12-31"`` or ``None`` if unparseable.
    """
    if not isinstance(normalized_fye, str):
        return None
    parts = normalized_fye.strip().split("-")
    if len(parts) < 1:
        return None
    month_key = parts[0].lower()[:3]
    return _FYE_MONTH_TO_BUCKET.get(month_key)


def validate_fye_reporting_date(
    normalized_fye: Optional[str],
    reporting_date: Optional[Any],
) -> Optional[str]:
    """Validate that ``fye`` and ``reporting_date`` belong to the same quarter bucket.

    Returns the bucket string (e.g. ``"03-31"``) when valid, or ``None`` when the
    combination is invalid or either field is missing.

    ``reporting_date`` may be a ``datetime.date``, ``datetime.datetime``, or a
    string in ``YYYY-MM-DD`` format.
    """
    if reporting_date is None:
        return None

    expected_bucket = fye_to_reporting_bucket(normalized_fye)
    if expected_bucket is None:
        return None

    # Derive MM-DD from reporting_date.
    from datetime import date as _date, datetime as _datetime
    if isinstance(reporting_date, _datetime):
        rd = reporting_date.date()
    elif isinstance(reporting_date, _date):
        rd = reporting_date
    elif isinstance(reporting_date, str):
        try:
            rd = _date.fromisoformat(reporting_date.strip())
        except ValueError:
            return None
    else:
        return None

    actual_bucket = f"{rd.month:02d}-{rd.day:02d}"
    if actual_bucket != expected_bucket:
        return None
    return expected_bucket


# ---------------------------------------------------------------------------
# Config loading helpers
# ---------------------------------------------------------------------------


def _empty_bucket_formulas() -> dict[str, str]:
    return {m: "" for m in SNOWFLAKE_PR_FINANCIAL_METRICS}


def _extract_bucket_metrics(bucket_payload: Any) -> dict[str, str]:
    """Extract ``{metric: formula_str}`` from a stored bucket payload dict."""
    out: dict[str, str] = {}
    raw: Any = bucket_payload if isinstance(bucket_payload, dict) else {}
    for col in SNOWFLAKE_PR_FINANCIAL_METRICS:
        rc = raw.get(col)
        formula_str = ""
        if isinstance(rc, dict):
            fr = rc.get("formula")
            formula_str = fr.strip() if isinstance(fr, str) else ""
        elif isinstance(rc, str):
            formula_str = rc.strip()
        out[col] = formula_str
    return out


def resolve_snowflake_pr_full_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the full config from a stored ConfigTable payload.

    Returns::

        {
            "groups": {
                # Non-bucket-aware group: single "all" key holds the shared formula set.
                "surge_seed": {
                    "all": {metric: formula, ...},
                },
                # Bucket-aware group: one key per reporting-date bucket.
                "growth_venture": {
                    "03-31": {metric: formula, ...},
                    "06-30": {...},
                    "09-30": {...},
                    "12-31": {...},
                }
            }
        }

    All buckets/keys and metrics are always present (empty strings where not configured).
    The sync uses the raw four-bucket storage directly; this resolved shape is used by
    the settings service for GET responses and audit diffs.
    """
    payload = payload if isinstance(payload, dict) else {}
    stored_groups = payload.get("stage_groups") if isinstance(payload.get("stage_groups"), dict) else {}
    groups: dict[str, dict[str, dict[str, str]]] = {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        group_raw = stored_groups.get(group_id) if isinstance(stored_groups.get(group_id), dict) else {}
        if not SNOWFLAKE_PR_STAGE_GROUP_BUCKET_AWARE[group_id]:
            # Expose the first bucket as the canonical "all" set (all four are identical
            # after a well-formed persist; fall back to the raw dict if not yet stored).
            first_bucket = SNOWFLAKE_PR_REPORTING_DATE_BUCKETS[0]
            groups[group_id] = {"all": _extract_bucket_metrics(group_raw.get(first_bucket) or group_raw)}
        else:
            buckets: dict[str, dict[str, str]] = {}
            for bucket in SNOWFLAKE_PR_REPORTING_DATE_BUCKETS:
                buckets[bucket] = _extract_bucket_metrics(group_raw.get(bucket))
            groups[group_id] = buckets
    return {"groups": groups}


def _identifiers_allowlist() -> frozenset[str]:
    return frozenset(numeric_identifier_whitelist_from_pr_submission_model(PRSubmissionDataRaw))


async def load_snowflake_pr_financial_mapping_full(db: AsyncSession) -> dict[str, Any]:
    """Async: full quarter-bucket config."""
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY)))
        .scalars()
        .first()
    )
    payload = dict(row.value) if row and isinstance(row.value, dict) else {}
    return resolve_snowflake_pr_full_config(payload)


def load_snowflake_pr_financial_mapping_full_sync(session: Session) -> dict[str, Any]:
    """Sync: full quarter-bucket config — used by the data-manipulation PR sync."""
    row = (
        session.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY))
        .scalars()
        .first()
    )
    payload = dict(row.value) if row and isinstance(row.value, dict) else {}
    return resolve_snowflake_pr_full_config(payload)


async def persist_snowflake_pr_financial_mapping(
    db: AsyncSession,
    *,
    stage_groups: dict[str, dict[str, dict[str, str]]],
) -> dict[str, Any]:
    """Persist the quarter-bucket config and return the stored dict.

    For ``surge_seed`` (not bucket-aware), the caller supplies a single bucket key
    ``"all"`` whose formula set is fanned out to all four reporting-date buckets.
    For ``growth_venture`` (bucket-aware), each of the four buckets is stored as-is.
    """
    stored_groups: dict[str, Any] = {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        group_in = stage_groups.get(group_id) or {}
        if not SNOWFLAKE_PR_STAGE_GROUP_BUCKET_AWARE[group_id]:
            # Non-bucket-aware: fan out the single formula set to all buckets.
            single = group_in.get("all") or {}
            stored_groups[group_id] = {
                bucket: {m: {"formula": single.get(m, "")} for m in SNOWFLAKE_PR_FINANCIAL_METRICS}
                for bucket in SNOWFLAKE_PR_REPORTING_DATE_BUCKETS
            }
        else:
            stored_groups[group_id] = {
                bucket: {
                    m: {"formula": (group_in.get(bucket) or {}).get(m, "")}
                    for m in SNOWFLAKE_PR_FINANCIAL_METRICS
                }
                for bucket in SNOWFLAKE_PR_REPORTING_DATE_BUCKETS
            }
    body: dict[str, Any] = {"stage_groups": stored_groups}
    row = (
        (await db.execute(select(ConfigTable).where(ConfigTable.key == SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY)))
        .scalars()
        .first()
    )
    if row is None:
        row = ConfigTable(
            key=SNOWFLAKE_PR_FINANCIAL_MAPPING_CONFIG_KEY,
            value=body,
            description="Snowflake PR submission → FinancialDataSnowflake metric formulas",
        )
        db.add(row)
    else:
        row.value = body
    await db.flush()
    await db.refresh(row)
    return dict(row.value) if isinstance(row.value, dict) else body


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validated_snowflake_pr_formulas_from_payload(
    payload_metrics: dict[str, Any],
    *,
    allowed_identifiers: frozenset[str],
) -> dict[str, str]:
    out: dict[str, str] = {}
    allow = allowed_identifiers
    for col in SNOWFLAKE_PR_FINANCIAL_METRICS:
        raw = payload_metrics.get(col)
        formula_str = ""
        if isinstance(raw, dict):
            f = raw.get("formula")
            formula_str = f.strip() if isinstance(f, str) else ""
        elif isinstance(raw, str):
            formula_str = raw.strip()
        if not formula_str:
            out[col] = ""
            continue
        try:
            validate_formula(formula_str, allowed_identifiers=allow)
        except FormulaEvaluationError as exc:
            raise ValueError(f"{col}: {exc}") from exc
        out[col] = formula_str
    return out


# ---------------------------------------------------------------------------
# Audit diff / summary helpers
# ---------------------------------------------------------------------------


def snowflake_pr_mapping_audit_diff_lines(before: dict[str, str], after: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for col in SNOWFLAKE_PR_FINANCIAL_METRICS:
        bf = (before.get(col) or "").strip()
        af = (after.get(col) or "").strip()
        if bf != af:
            label = SNOWFLAKE_PR_AUDIT_LABELS.get(col, col)
            lines.append(f"{label}: «{bf}» → «{af}»")
    return lines


def snowflake_pr_mapping_full_audit_diff_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Diff two full resolved configs (see :func:`resolve_snowflake_pr_full_config`)."""
    lines: list[str] = []
    b_groups = before.get("groups", {}) if isinstance(before.get("groups"), dict) else {}
    a_groups = after.get("groups", {}) if isinstance(after.get("groups"), dict) else {}
    for group_id in SNOWFLAKE_PR_STAGE_GROUP_IDS:
        group_label = SNOWFLAKE_PR_STAGE_GROUPS[group_id]["label"]
        b_group = b_groups.get(group_id, {}) if isinstance(b_groups.get(group_id), dict) else {}
        a_group = a_groups.get(group_id, {}) if isinstance(a_groups.get(group_id), dict) else {}
        if not SNOWFLAKE_PR_STAGE_GROUP_BUCKET_AWARE[group_id]:
            # Non-bucket-aware: single "all" key.
            b_all = b_group.get("all", {}) if isinstance(b_group.get("all"), dict) else {}
            a_all = a_group.get("all", {}) if isinstance(a_group.get("all"), dict) else {}
            lines.extend(
                f"{group_label} · {ln}"
                for ln in snowflake_pr_mapping_audit_diff_lines(b_all, a_all)
            )
        else:
            for bucket in SNOWFLAKE_PR_REPORTING_DATE_BUCKETS:
                bucket_label = SNOWFLAKE_PR_REPORTING_DATE_BUCKET_LABELS.get(bucket, bucket)
                b_bucket = b_group.get(bucket, {}) if isinstance(b_group.get(bucket), dict) else {}
                a_bucket = a_group.get(bucket, {}) if isinstance(a_group.get(bucket), dict) else {}
                lines.extend(
                    f"{group_label} · {bucket_label} · {ln}"
                    for ln in snowflake_pr_mapping_audit_diff_lines(b_bucket, a_bucket)
                )
    return lines


def snowflake_pr_mapping_audit_user_summary(*, change_lines: list[str]) -> str:
    if not change_lines:
        return (
            "Snowflake PR financial formulas saved.\n• No formula changes "
            "(compared to the configuration prior to this save)."
        )
    body = "\n".join(f"• {line}" for line in change_lines)
    return f"Snowflake PR financial formulas updated\n\n{body}"


def format_mapping_summary_json(metrics: dict[str, str]) -> str:
    try:
        s = json.dumps(metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        s = str(metrics)
    if len(s) > 16000:
        return s[:16000] + "…"
    return s


# ---------------------------------------------------------------------------
# Legacy compat shim: load_snowflake_pr_financial_mapping_config
# (kept so existing callers that just need the flat default set don't break
#  during the transition; returns an empty formula map since there is no longer
#  a global default set).
# ---------------------------------------------------------------------------


async def load_snowflake_pr_financial_mapping_config(db: AsyncSession) -> dict[str, str]:
    """Deprecated shim — returns empty formulas; callers should use the full config."""
    return {m: "" for m in SNOWFLAKE_PR_FINANCIAL_METRICS}


def load_snowflake_pr_financial_mapping_config_sync(session: Session) -> dict[str, str]:
    """Deprecated shim — returns empty formulas; callers should use the full config."""
    return {m: "" for m in SNOWFLAKE_PR_FINANCIAL_METRICS}
