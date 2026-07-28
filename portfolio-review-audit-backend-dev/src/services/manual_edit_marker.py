"""Shared helper for "this field was manually edited" markers.

A *marker* records the **current** manual-edit state of a single field — who edited it,
when, why (justification), and the value it replaced — so the UI can render that field in a
distinct colour with an "edited manually" disclaimer + justification popup.

The marker is intentionally lightweight and stores only the **latest** edit; the full,
append-only history continues to live in ``CompanyAuditRecorder`` (we add markers *beside*
that trail, we do not replace it).

Markers attach to whichever JSON container a given surface already persists, keyed by a
stable field key:

- Extraction tree  → ``FileOCRMetadata.ocr_json["manual_edits"][<dotted_path>]``
- Reconciliation   → ``FinancialMetricReconciliation.extra_data["manual_edits"][<column>]``
- MIS / Snowflake  → ``FinancialDataSnowflake.payload["manual_edits"][<metric>]``

All writes are additive and best-effort: a marker failure must never block the edit itself
(callers wrap ``set_marker``/``clear_marker`` in try/except and log non-fatally).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# Key under each JSON container that holds the per-field marker map.
MANUAL_EDITS_KEY = "manual_edits"

# ``action`` discriminator — what kind of manual edit produced the marker. Kept open-ended
# (plain strings) so new surfaces can add their own without a schema change.
ACTION_VALUE_PATCH = "value_patch"          # extraction: set/clear a numeric leaf
ACTION_MAP_UNMATCHED = "map_unmatched"      # extraction: map an unmatched line into the tree
ACTION_FIELD_COMPONENTS = "field_components"  # extraction: edit a leaf's roll-up breakdown
ACTION_CURRENCY_CONVERT = "currency_convert"  # extraction/MIS: bulk FX conversion
ACTION_AMOUNT_EDIT = "amount_edit"          # dashboard: AFS/MIS amount correction
ACTION_METRIC_EDIT = "metric_edit"          # MIS: metric value correction
ACTION_FIELD_EDIT = "field_edit"            # dashboard: status/category/remarks/response edit


def build_marker(
    *,
    action: str,
    reason: Optional[str],
    actor: Optional[str],
    previous_value: Any = None,
    new_value: Any = None,
) -> dict[str, Any]:
    """Build a marker dict for the latest manual edit of one field.

    ``previous_value`` / ``new_value`` are stored only when JSON-serialisable scalars
    (numbers / strings / bools / None); anything else is dropped so the marker stays small
    and safe to embed in a JSONB column.
    """
    marker: dict[str, Any] = {
        "edited": True,
        "action": action,
        "edited_by": actor or None,
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "reason": (reason or "").strip() or None,
    }
    if _is_simple_scalar(previous_value):
        marker["previous_value"] = previous_value
    if _is_simple_scalar(new_value):
        marker["new_value"] = new_value
    return marker


def set_marker(container: dict[str, Any], key: str, marker: dict[str, Any]) -> None:
    """Attach ``marker`` to ``container[MANUAL_EDITS_KEY][key]`` (created if missing)."""
    if not isinstance(container, dict) or not key:
        return
    edits = container.get(MANUAL_EDITS_KEY)
    if not isinstance(edits, dict):
        edits = {}
    edits[str(key)] = marker
    container[MANUAL_EDITS_KEY] = edits


def clear_marker(container: dict[str, Any], key: str) -> None:
    """Remove the marker for ``key`` (e.g. when a field is detached/removed). No-op if absent."""
    if not isinstance(container, dict) or not key:
        return
    edits = container.get(MANUAL_EDITS_KEY)
    if isinstance(edits, dict) and str(key) in edits:
        edits.pop(str(key), None)
        container[MANUAL_EDITS_KEY] = edits


def rescale_marker_previous_values(container: dict[str, Any], factor: float) -> None:
    """Rescale numeric ``previous_value`` on every marker by ``factor``.

    Used after a bulk currency conversion so a marker's "was X" reflects the new scale and
    doesn't show a stale figure in the old currency. Best-effort; non-numeric values untouched.
    """
    if not isinstance(container, dict):
        return
    edits = container.get(MANUAL_EDITS_KEY)
    if not isinstance(edits, dict):
        return
    for marker in edits.values():
        if not isinstance(marker, dict):
            continue
        pv = marker.get("previous_value")
        if isinstance(pv, (int, float)) and not isinstance(pv, bool):
            try:
                marker["previous_value"] = float(pv) * float(factor)
            except (TypeError, ValueError):
                pass


def get_markers(container: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return the marker map from ``container`` (``{}`` when absent/malformed)."""
    if not isinstance(container, dict):
        return {}
    edits = container.get(MANUAL_EDITS_KEY)
    return edits if isinstance(edits, dict) else {}


def _is_simple_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (int, float, str, bool))
