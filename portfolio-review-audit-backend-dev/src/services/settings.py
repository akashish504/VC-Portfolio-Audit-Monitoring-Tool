from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConfigTable, ParameterThreshold, PRSubmissionDataRaw, ReviewCycle
from src.schema.settings import (
    ConfigTableCreate,
    ConfigTablePatch,
    DataSyncConfigPut,
    DataSyncConfigRead,
    FinancialMetricMappingPack,
    FinancialMetricMappingPut,
    FinancialMetricMappingRead,
    ParameterThresholdBulkPatchItem,
    ParameterThresholdCreate,
    ParameterThresholdPatch,
    ReviewCycleCreate,
    ReviewCyclePatch,
    SnowflakePRFinancialMappingPut,
    SnowflakePRFinancialMappingRead,
)
from src.services.financial_audit_schema import (
    list_audit_financials_numeric_leaf_paths,
    load_audit_financials_schema,
)
from src.services.financial_data_extraction_sync import (
    reapply_financial_metric_mapping_to_all_files,
)
from src.services.financial_metric_mapping import (
    financial_metric_mapping_audit_diff_lines,
    financial_metric_mapping_audit_user_summary,
    load_financial_metric_mapping_config,
    persist_financial_metric_mapping,
    validated_mapping_from_payload,
)
from src.services.snowflake_pr_formula_eval import numeric_identifier_whitelist_from_pr_submission_model
from src.services.snowflake_pr_financial_mapping import (
    SNOWFLAKE_PR_STAGE_GROUPS,
)
from src.services.snowflake_pr_financial_mapping_v2 import (
    BALANCE_METRICS,
    PNL_METRICS,
    V2_GROUP_SLOTS,
    V2_SLOT_LABELS,
    load_snowflake_pr_v2_full,
    persist_snowflake_pr_v2,
    snowflake_pr_v2_audit_diff_lines,
    snowflake_pr_v2_audit_user_summary,
    validated_v2_slot_formulas,
)
from src.services.settings_audit_recorder import (
    FinancialMetricMappingAuditRecorder,
    ParameterThresholdAuditRecorder,
    ReviewCycleAuditRecorder,
    SnowflakePRFinancialMappingAuditRecorder,
)

_THRESHOLD_SKIP_KEYS = frozenset({"currency"})


def _is_configurable_threshold_key(key: str) -> bool:
    return (key or "").strip().lower() not in _THRESHOLD_SKIP_KEYS


def _pct_num(val: dict[str, Any]) -> Optional[float]:
    pct = val.get("percent_threshold")
    if isinstance(pct, (int, float)):
        return float(pct)
    legacy = val.get("threshold_percent")
    if isinstance(legacy, (int, float)):
        return float(legacy) / 100.0
    return None


def _abs_num(val: dict[str, Any]) -> Optional[float]:
    abs_t = val.get("absolute_threshold")
    if isinstance(abs_t, (int, float)):
        return float(abs_t)
    return None


def _pct_label(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%"


def _abs_label(v: Optional[float]) -> str:
    if v is None:
        return "0"
    return f"{v:,.0f}"


def _pnl_pack_dict(formulas: dict[str, str] | None) -> dict[str, Any]:
    return {m: {"formula": (formulas or {}).get(m, "")} for m in PNL_METRICS}


def _balance_pack_dict(formulas: dict[str, str] | None) -> dict[str, Any]:
    return {m: {"formula": (formulas or {}).get(m, "")} for m in BALANCE_METRICS}


def _snowflake_pr_read_from_full(full: dict[str, Any]) -> SnowflakePRFinancialMappingRead:
    """Map the resolved v2 config ``{"groups": {gid: {slot: {metric: formula}}}}``
    to the typed read schema."""
    groups = full.get("groups", {}) if isinstance(full.get("groups"), dict) else {}
    ss = groups.get("surge_seed", {}) if isinstance(groups.get("surge_seed"), dict) else {}
    gv = groups.get("growth_venture", {}) if isinstance(groups.get("growth_venture"), dict) else {}
    return SnowflakePRFinancialMappingRead.model_validate(
        {
            "stage_groups": {
                "surge_seed": {
                    "pnl": _pnl_pack_dict(ss.get("pnl")),
                    "balance": _balance_pack_dict(ss.get("balance")),
                },
                "growth_venture": {
                    "pnl_aligned": _pnl_pack_dict(gv.get("pnl_aligned")),
                    "pnl_lagged": _pnl_pack_dict(gv.get("pnl_lagged")),
                    "balance": _balance_pack_dict(gv.get("balance")),
                },
            }
        }
    )


class SettingsService:
    # ---- ReviewCycle ----
    @staticmethod
    async def list_review_cycles(
        db: AsyncSession, *, limit: int, offset: int
    ) -> Tuple[list[ReviewCycle], int]:
        base = select(ReviewCycle)
        total = (
            await db.execute(
                select(func.count(ReviewCycle.id))
            )
        ).scalar_one()
        items = (
            (await db.execute(
                base.order_by(ReviewCycle.starts_at.desc().nulls_last(), ReviewCycle.id.desc())
                .limit(limit)
                .offset(offset)
            ))
            .scalars()
            .all()
        )
        return items, total

    @staticmethod
    async def create_review_cycle(db: AsyncSession, payload: ReviewCycleCreate):
        obj = ReviewCycle(**payload.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        label = (obj.name or "").strip() or obj.id
        await ReviewCycleAuditRecorder(db).log(
            action=f'Review cycle "{label}" created',
            review_cycle_id=obj.id,
            meta={"event": "review_cycle.created", "review_cycle_id": obj.id},
            search_text=f"{label} {obj.id} created",
        )
        return obj

    @staticmethod
    async def get_review_cycle(db: AsyncSession, review_cycle_id: str):
        obj = await db.get(ReviewCycle, review_cycle_id)
        if not obj:
            return None
        return obj

    @staticmethod
    async def patch_review_cycle(
        db: AsyncSession, review_cycle_id: str, payload: ReviewCyclePatch
    ):
        obj = await SettingsService.get_review_cycle(db, review_cycle_id)
        if not obj:
            return None
        updates = payload.model_dump(exclude_unset=True)
        before: dict[str, Any] = {k: getattr(obj, k, None) for k in updates}
        for k, v in updates.items():
            setattr(obj, k, v)
        await db.flush()
        await db.refresh(obj)
        recorder = ReviewCycleAuditRecorder(db)
        label = (obj.name or "").strip() or review_cycle_id
        for field, new_val in updates.items():
            old_val = before[field]
            if old_val == new_val:
                continue
            if field == "name" and new_val:
                await recorder.log(
                    action=f'Review cycle renamed from "{old_val or review_cycle_id}" → "{new_val}"',
                    review_cycle_id=obj.id,
                    meta={"event": "review_cycle.renamed", "field": "name", "before": old_val, "after": new_val},
                    search_text=f"{old_val} {new_val} {obj.id}",
                )
            elif field == "status":
                await recorder.log(
                    action=f'Review cycle "{label}" status updated from "{old_val or "—"}" → "{new_val}"',
                    review_cycle_id=obj.id,
                    meta={"event": "review_cycle.status_changed", "field": "status", "before": old_val, "after": new_val},
                    search_text=f"{label} {obj.id} status {old_val} {new_val}",
                )
            else:
                field_label = field.replace("_", " ").title()
                await recorder.log(
                    action=f'Review cycle "{label}" {field_label} updated',
                    review_cycle_id=obj.id,
                    meta={"event": "review_cycle.field_changed", "field": field, "before": old_val, "after": new_val},
                    search_text=f"{label} {obj.id} {field}",
                )
        return obj

    @staticmethod
    async def delete_review_cycle(db: AsyncSession, review_cycle_id: str) -> bool:
        obj = await SettingsService.get_review_cycle(db, review_cycle_id)
        if not obj:
            return False
        label = (obj.name or "").strip() or review_cycle_id
        await ReviewCycleAuditRecorder(db).log(
            action=f'Review cycle "{label}" deleted',
            review_cycle_id=review_cycle_id,
            meta={"event": "review_cycle.deleted", "review_cycle_id": review_cycle_id},
            search_text=f"{label} {review_cycle_id} deleted",
        )
        await db.delete(obj)
        return True

    # ---- ParameterThreshold ----
    @staticmethod
    async def list_parameter_thresholds(
        db: AsyncSession, *, limit: int, offset: int
    ) -> Tuple[list[ParameterThreshold], int]:
        total = (
            await db.execute(
                select(func.count(ParameterThreshold.id))
            )
        ).scalar_one()
        items = (
            (
                await db.execute(
                    select(ParameterThreshold)
                    .order_by(ParameterThreshold.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return items, total

    @staticmethod
    async def create_parameter_threshold(
        db: AsyncSession, payload: ParameterThresholdCreate
    ):
        obj = ParameterThreshold(**payload.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def get_parameter_threshold(db: AsyncSession, threshold_id: int):
        obj = await db.get(ParameterThreshold, threshold_id)
        if not obj:
            return None
        return obj

    @staticmethod
    async def patch_parameter_threshold(
        db: AsyncSession, threshold_id: int, payload: ParameterThresholdPatch
    ):
        obj = await SettingsService.get_parameter_threshold(db, threshold_id)
        if not obj:
            return None
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def bulk_patch_parameter_thresholds(
        db: AsyncSession, items: list[ParameterThresholdBulkPatchItem]
    ) -> list[ParameterThreshold]:
        updated: list[ParameterThreshold] = []
        change_parts: list[str] = []

        for item in items:
            obj = await SettingsService.get_parameter_threshold(db, item.id)
            if not obj or not _is_configurable_threshold_key(obj.key):
                continue
            patch = item.model_dump(exclude_unset=True)
            patch.pop("id", None)
            if not patch:
                continue
            before_val = dict(obj.value or {})
            before_pct = _pct_num(before_val)
            before_abs = _abs_num(before_val)
            for k, v in patch.items():
                setattr(obj, k, v)
            await db.flush()
            await db.refresh(obj)
            updated.append(obj)
            after_val = dict(obj.value or {})
            after_pct = _pct_num(after_val)
            after_abs = _abs_num(after_val)
            parts: list[str] = []
            if before_pct != after_pct:
                parts.append(f"% {_pct_label(before_pct)}→{_pct_label(after_pct)}")
            if before_abs != after_abs:
                parts.append(f"abs {_abs_label(before_abs)}→{_abs_label(after_abs)}")
            if parts:
                change_parts.append(f"{obj.key} ({', '.join(parts)})")

        if change_parts:
            detail = "; ".join(change_parts)
            action = f"Parameter thresholds saved: {detail}"
            if len(action) > 255:
                action = f"Parameter thresholds saved ({len(change_parts)} parameters updated)"
            await ParameterThresholdAuditRecorder(db).log(
                action=action,
                meta={
                    "event": "parameter_threshold.bulk_saved",
                    "change_count": len(change_parts),
                    "changes": change_parts,
                },
                search_text=detail,
            )
        return updated

    @staticmethod
    async def delete_parameter_threshold(db: AsyncSession, threshold_id: int) -> bool:
        obj = await SettingsService.get_parameter_threshold(db, threshold_id)
        if not obj:
            return False
        await db.delete(obj)
        return True

    # ---- ConfigTable ----
    @staticmethod
    async def list_config(
        db: AsyncSession, *, limit: int, offset: int
    ) -> Tuple[list[ConfigTable], int]:
        total = (
            await db.execute(
                select(func.count(ConfigTable.id))
            )
        ).scalar_one()
        items = (
            (
                await db.execute(
                    select(ConfigTable)
                    .order_by(ConfigTable.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return items, total

    @staticmethod
    async def create_config(db: AsyncSession, payload: ConfigTableCreate):
        obj = ConfigTable(**payload.model_dump())
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def get_config(db: AsyncSession, config_id: int):
        obj = await db.get(ConfigTable, config_id)
        if not obj:
            return None
        return obj

    @staticmethod
    async def patch_config(
        db: AsyncSession, config_id: int, payload: ConfigTablePatch
    ):
        obj = await SettingsService.get_config(db, config_id)
        if not obj:
            return None
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        await db.flush()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def delete_config(db: AsyncSession, config_id: int) -> bool:
        obj = await SettingsService.get_config(db, config_id)
        if not obj:
            return False
        await db.delete(obj)
        return True

    # ---- Financial metric extraction mapping ----
    @staticmethod
    async def get_financial_metric_mapping_read(db: AsyncSession) -> FinancialMetricMappingRead:
        effective = await load_financial_metric_mapping_config(db)
        pack = FinancialMetricMappingPack.model_validate(effective)
        return FinancialMetricMappingRead(metrics=pack)

    @staticmethod
    async def list_financial_metric_schema_paths(db: AsyncSession, *, q: Optional[str]) -> list[str]:
        schema = await load_audit_financials_schema(db)
        paths = list_audit_financials_numeric_leaf_paths(schema)
        term = (q or "").strip().lower()
        if term:
            paths = [p for p in paths if term in p.lower()]
        return paths

    @staticmethod
    async def put_financial_metric_mapping(
        db: AsyncSession,
        payload: FinancialMetricMappingPut,
    ) -> FinancialMetricMappingRead:
        before_snapshot = copy.deepcopy(await load_financial_metric_mapping_config(db))

        schema = await load_audit_financials_schema(db)
        dump = payload.metrics.model_dump(mode="python")
        try:
            validated = validated_mapping_from_payload(dump, schema=schema)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        await persist_financial_metric_mapping(db, mapping=validated)
        await db.flush()

        after_effective = await load_financial_metric_mapping_config(db)

        change_lines = financial_metric_mapping_audit_diff_lines(before_snapshot, validated)
        user_summary = financial_metric_mapping_audit_user_summary(change_lines=change_lines)

        changed_n = len(change_lines)
        if changed_n > 0:
            action_short = f"Financial extraction metric mapping updated — {changed_n} metric(s)"
        else:
            action_short = "Financial extraction metric mapping saved (unchanged formulas)"

        search_blob = "\n".join(change_lines)
        await FinancialMetricMappingAuditRecorder(db).log(
            action=action_short,
            meta={
                "event": "financial_metric_mapping.saved",
                "summary": user_summary,
                "change_lines": change_lines,
                "metrics_before": before_snapshot,
                "metrics_after": validated,
            },
            search_text=search_blob or action_short,
        )

        pack = FinancialMetricMappingPack.model_validate(after_effective)
        return FinancialMetricMappingRead(metrics=pack)

    # ---- Snowflake PR submission → FinancialDataSnowflake formulas (v2) ----
    @staticmethod
    async def get_snowflake_pr_financial_mapping_read(db: AsyncSession) -> SnowflakePRFinancialMappingRead:
        full = await load_snowflake_pr_v2_full(db)
        return _snowflake_pr_read_from_full(full)

    @staticmethod
    async def list_snowflake_pr_numeric_columns(_db: AsyncSession, *, q: Optional[str]) -> list[str]:
        cols = numeric_identifier_whitelist_from_pr_submission_model(PRSubmissionDataRaw)
        term = (q or "").strip().lower()
        if term:
            cols = [c for c in cols if term in c.lower()]
        return sorted(cols)

    @staticmethod
    async def put_snowflake_pr_financial_mapping(
        db: AsyncSession,
        payload: SnowflakePRFinancialMappingPut,
    ) -> SnowflakePRFinancialMappingRead:
        allow = frozenset(numeric_identifier_whitelist_from_pr_submission_model(PRSubmissionDataRaw))
        before_full = copy.deepcopy(await load_snowflake_pr_v2_full(db))

        # Validate each stage group's slots (P&L / balance; aligned/lagged for G/V).
        sg = payload.stage_groups
        slot_packs: dict[str, dict[str, Any]] = {
            "surge_seed": {"pnl": sg.surge_seed.pnl, "balance": sg.surge_seed.balance},
            "growth_venture": {
                "pnl_aligned": sg.growth_venture.pnl_aligned,
                "pnl_lagged": sg.growth_venture.pnl_lagged,
                "balance": sg.growth_venture.balance,
            },
        }
        validated_stage_groups: dict[str, dict[str, dict[str, str]]] = {}
        for group_id in ("surge_seed", "growth_venture"):
            group_label = SNOWFLAKE_PR_STAGE_GROUPS[group_id]["label"]
            validated_stage_groups[group_id] = {}
            for slot in V2_GROUP_SLOTS[group_id]:
                pack = slot_packs[group_id][slot]
                try:
                    validated_stage_groups[group_id][slot] = validated_v2_slot_formulas(
                        slot, pack.model_dump(mode="python"), allowed_identifiers=allow
                    )
                except ValueError as exc:
                    slot_label = V2_SLOT_LABELS.get(slot, slot)
                    raise ValueError(f"{group_label} · {slot_label}: {exc}") from exc

        await persist_snowflake_pr_v2(db, stage_groups=validated_stage_groups)
        await db.flush()

        after_full = await load_snowflake_pr_v2_full(db)
        change_lines = snowflake_pr_v2_audit_diff_lines(before_full, after_full)
        user_summary = snowflake_pr_v2_audit_user_summary(change_lines=change_lines)
        changed_n = len(change_lines)
        if changed_n > 0:
            action_short = f"Snowflake PR financial formulas updated — {changed_n} change(s)"
        else:
            action_short = "Snowflake PR financial formulas saved (unchanged)"

        search_blob = "\n".join(change_lines)
        await SnowflakePRFinancialMappingAuditRecorder(db).log(
            action=action_short,
            meta={
                "event": "snowflake_pr_financial_mapping.saved",
                "summary": user_summary,
                "change_lines": change_lines,
                "config_before": before_full,
                "config_after": after_full,
            },
            search_text=search_blob or action_short,
        )

        return _snowflake_pr_read_from_full(after_full)

    # ---- Data sync schedule config ----

    _DATA_SYNC_CONFIG_KEY = "data_sync_schedule_config_v1"
    _DATA_SYNC_FIXED = {
        "enabled": True,
        "time": "21:00",
        "timezone": "Asia/Kolkata",
        "day_of_week": "sun",
    }
    _DATA_SYNC_DEFAULT_FREQUENCY = "weekly"

    @staticmethod
    async def get_data_sync_config_read(db: AsyncSession) -> DataSyncConfigRead:
        result = await db.execute(
            select(ConfigTable).where(ConfigTable.key == SettingsService._DATA_SYNC_CONFIG_KEY)
        )
        row = result.scalars().first()
        raw: dict[str, Any] = (row.value or {}) if row is not None else {}
        frequency = raw.get("frequency", SettingsService._DATA_SYNC_DEFAULT_FREQUENCY)
        if frequency not in ("daily", "weekly"):
            frequency = SettingsService._DATA_SYNC_DEFAULT_FREQUENCY
        raw_cutoff = raw.get("cutoff_date")
        cutoff_date = None
        if raw_cutoff:
            try:
                from datetime import date
                cutoff_date = date.fromisoformat(str(raw_cutoff))
            except (ValueError, TypeError):
                cutoff_date = None
        return DataSyncConfigRead(
            frequency=frequency,
            enabled=raw.get("enabled", True),
            time=raw.get("time", "21:00"),
            timezone=raw.get("timezone", "Asia/Kolkata"),
            day_of_week=raw.get("day_of_week", "sun"),
            cutoff_date=cutoff_date,
        )

    @staticmethod
    async def put_data_sync_config(db: AsyncSession, payload: DataSyncConfigPut) -> DataSyncConfigRead:
        new_value: dict[str, Any] = {
            **SettingsService._DATA_SYNC_FIXED,
            "frequency": payload.frequency,
            "cutoff_date": payload.cutoff_date.isoformat() if payload.cutoff_date else None,
        }
        result = await db.execute(
            select(ConfigTable).where(ConfigTable.key == SettingsService._DATA_SYNC_CONFIG_KEY)
        )
        row = result.scalars().first()
        if row is None:
            row = ConfigTable(
                key=SettingsService._DATA_SYNC_CONFIG_KEY,
                value=new_value,
                description="Automatic data sync schedule configuration",
            )
            db.add(row)
        else:
            row.value = new_value
        await db.flush()
        await db.refresh(row)
        return DataSyncConfigRead(
            frequency=new_value["frequency"],
            enabled=new_value["enabled"],
            time=new_value["time"],
            timezone=new_value["timezone"],
            day_of_week=new_value["day_of_week"],
            cutoff_date=payload.cutoff_date,
        )
