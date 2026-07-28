from __future__ import annotations

from typing import Optional, Tuple

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.scripts.data_manipulation.pr_submission_financial_sync import (
    reapply_snowflake_pr_financial_mapping,
)
from src.services.financial_data_extraction_sync import (
    reapply_financial_metric_mapping_to_all_files,
)
from src.schema.common import Page
from src.schema.settings import (
    ConfigTableCreate,
    ConfigTablePatch,
    ConfigTableRead,
    DataSyncConfigPut,
    DataSyncConfigRead,
    FinancialMetricMappingPut,
    FinancialMetricMappingRead,
    FinancialMetricSchemaPathsRead,
    ParameterThresholdBulkPatchRequest,
    ParameterThresholdCreate,
    ParameterThresholdPatch,
    ParameterThresholdRead,
    ReviewCyclePatch,
    ReviewCycleRead,
    FyEndOptionsRead,
    FyEndResolveRead,
    SnowflakePRFinancialMappingPut,
    SnowflakePRFinancialMappingRead,
    SnowflakePRNumericColumnsRead,
)
from src.services.settings import SettingsService
from src.services.fy_end import months_for_review_cycle_id, months_for_review_cycle_name, normalize_fy_end, resolve_review_cycle_id_for_fy_end

logger = logging.getLogger(__name__)
router = APIRouter()


def _pagination(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return limit, offset


@router.get("/review-cycles", response_model=Page[ReviewCycleRead])
async def list_review_cycles(
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await SettingsService.list_review_cycles(
        db, limit=limit, offset=offset
    )
    return Page(items=items, total=total)


@router.get("/review-cycles/resolve-from-fy-end", response_model=FyEndResolveRead)
async def resolve_review_cycle_from_fy_end(
    fy_end: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    normalized = normalize_fy_end(fy_end)
    if not normalized:
        raise HTTPException(status_code=422, detail=f"invalid fy_end: {fy_end!r}")
    try:
        cycle_id = await resolve_review_cycle_id_for_fy_end(db, normalized)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    cycle = await SettingsService.get_review_cycle(db, cycle_id)
    return FyEndResolveRead(
        fy_end=normalized,
        review_cycle_id=cycle_id,
        review_cycle_name=(cycle.name if cycle else None),
    )


@router.get("/review-cycles/{review_cycle_id}", response_model=ReviewCycleRead)
async def get_review_cycle(
    review_cycle_id: str,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.get_review_cycle(db, review_cycle_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ReviewCycle not found")
    return obj


@router.get("/review-cycles/{review_cycle_id}/fy-end-options", response_model=FyEndOptionsRead)
async def list_fy_end_options(review_cycle_id: str, db: AsyncSession = Depends(get_db)):
    rc = (review_cycle_id or "").strip()
    if not rc:
        raise HTTPException(status_code=422, detail="review_cycle_id is required")
    # Try parsing directly (works when PK is formatted like "CY24-FY25").
    options = months_for_review_cycle_id(rc)
    if not options:
        # Production uses UUID PKs — look up the cycle name and parse that instead.
        cycle = await SettingsService.get_review_cycle(db, rc)
        if cycle and cycle.name:
            options = months_for_review_cycle_name(cycle.name)
    if not options:
        raise HTTPException(status_code=404, detail=f"No FY end months for review cycle {rc!r}")
    return FyEndOptionsRead(review_cycle_id=rc, options=options)


@router.patch("/review-cycles/{review_cycle_id}", response_model=ReviewCycleRead)
async def patch_review_cycle(
    review_cycle_id: str,
    payload: ReviewCyclePatch,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.patch_review_cycle(db, review_cycle_id, payload)
    if not obj:
        raise HTTPException(status_code=404, detail="ReviewCycle not found")
    return obj


@router.delete("/review-cycles/{review_cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review_cycle(
    review_cycle_id: str,
    db: AsyncSession = Depends(get_db),
):
    ok = await SettingsService.delete_review_cycle(db, review_cycle_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ReviewCycle not found")
    return None


@router.get("/parameter-thresholds", response_model=Page[ParameterThresholdRead])
async def list_parameter_thresholds(
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await SettingsService.list_parameter_thresholds(
        db, limit=limit, offset=offset
    )
    return Page(items=items, total=total)


@router.post(
    "/parameter-thresholds",
    response_model=ParameterThresholdRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_parameter_threshold(
    payload: ParameterThresholdCreate,
    db: AsyncSession = Depends(get_db),
):
    return await SettingsService.create_parameter_threshold(db, payload)


@router.patch("/parameter-thresholds", response_model=list[ParameterThresholdRead])
async def bulk_patch_parameter_thresholds(
    payload: ParameterThresholdBulkPatchRequest,
    db: AsyncSession = Depends(get_db),
):
    items = await SettingsService.bulk_patch_parameter_thresholds(db, payload.items)
    return items


@router.get("/parameter-thresholds/{threshold_id}", response_model=ParameterThresholdRead)
async def get_parameter_threshold(
    threshold_id: int,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.get_parameter_threshold(db, threshold_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ParameterThreshold not found")
    return obj


@router.patch("/parameter-thresholds/{threshold_id}", response_model=ParameterThresholdRead)
async def patch_parameter_threshold(
    threshold_id: int,
    payload: ParameterThresholdPatch,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.patch_parameter_threshold(db, threshold_id, payload)
    if not obj:
        raise HTTPException(status_code=404, detail="ParameterThreshold not found")
    return obj


@router.delete("/parameter-thresholds/{threshold_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_parameter_threshold(
    threshold_id: int,
    db: AsyncSession = Depends(get_db),
):
    ok = await SettingsService.delete_parameter_threshold(db, threshold_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ParameterThreshold not found")
    return None


@router.get(
    "/financial-metric-mapping/schema-paths",
    response_model=FinancialMetricSchemaPathsRead,
)
async def list_financial_metric_schema_paths(
    q: Optional[str] = Query(default=None, description="Substring filter (case-insensitive)"),
    db: AsyncSession = Depends(get_db),
):
    paths = await SettingsService.list_financial_metric_schema_paths(db, q=q)
    return FinancialMetricSchemaPathsRead(paths=paths)


@router.get("/financial-metric-mapping", response_model=FinancialMetricMappingRead)
async def get_financial_metric_mapping(db: AsyncSession = Depends(get_db)):
    return await SettingsService.get_financial_metric_mapping_read(db)


@router.put("/financial-metric-mapping", response_model=FinancialMetricMappingRead)
async def put_financial_metric_mapping(
    payload: FinancialMetricMappingPut,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await SettingsService.put_financial_metric_mapping(db, payload)
    except ValueError as exc:
        logger.warning("[financial_metric_mapping] PUT validation rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Re-evaluate every completed audit-financials file against the new mapping so
    # newly added terms become globally effective immediately. The task opens its
    # own AsyncSessionLocal and commits per file — it never holds this request's
    # session (see comments in reapply_financial_metric_mapping_to_all_files).
    background_tasks.add_task(reapply_financial_metric_mapping_to_all_files)
    logger.info(
        "[financial_metric_mapping] PUT accepted — reapply scheduled (will run after response)",
    )
    return result


@router.get(
    "/snowflake-pr-financial-mapping/numeric-columns",
    response_model=SnowflakePRNumericColumnsRead,
)
async def list_snowflake_pr_numeric_columns(
    q: Optional[str] = Query(default=None, description="Substring filter on column names (case-insensitive)"),
    db: AsyncSession = Depends(get_db),
):
    cols = await SettingsService.list_snowflake_pr_numeric_columns(db, q=q)
    return SnowflakePRNumericColumnsRead(columns=cols)


@router.get("/snowflake-pr-financial-mapping", response_model=SnowflakePRFinancialMappingRead)
async def get_snowflake_pr_financial_mapping(db: AsyncSession = Depends(get_db)):
    return await SettingsService.get_snowflake_pr_financial_mapping_read(db)


@router.put("/snowflake-pr-financial-mapping", response_model=SnowflakePRFinancialMappingRead)
async def put_snowflake_pr_financial_mapping(
    payload: SnowflakePRFinancialMappingPut,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await SettingsService.put_snowflake_pr_financial_mapping(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Re-run the PR → FinancialDataSnowflake sync so the new (possibly stage-specific)
    # formulas take effect immediately. The task opens its own sync session and never
    # reuses this request's session.
    background_tasks.add_task(reapply_snowflake_pr_financial_mapping)
    logger.info("[snowflake_pr_financial_mapping] PUT accepted — re-sync scheduled (runs after response)")
    return result


@router.get("/config", response_model=Page[ConfigTableRead])
async def list_config(
    pagination: Tuple[int, int] = Depends(_pagination),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = pagination
    items, total = await SettingsService.list_config(db, limit=limit, offset=offset)
    return Page(items=items, total=total)


@router.post("/config", response_model=ConfigTableRead, status_code=status.HTTP_201_CREATED)
async def create_config(
    payload: ConfigTableCreate,
    db: AsyncSession = Depends(get_db),
):
    return await SettingsService.create_config(db, payload)


@router.get("/config/{config_id}", response_model=ConfigTableRead)
async def get_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.get_config(db, config_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ConfigTable not found")
    return obj


@router.patch("/config/{config_id}", response_model=ConfigTableRead)
async def patch_config(
    config_id: int,
    payload: ConfigTablePatch,
    db: AsyncSession = Depends(get_db),
):
    obj = await SettingsService.patch_config(db, config_id, payload)
    if not obj:
        raise HTTPException(status_code=404, detail="ConfigTable not found")
    return obj


@router.delete("/config/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    ok = await SettingsService.delete_config(db, config_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ConfigTable not found")
    return None


@router.get("/data-sync-config", response_model=DataSyncConfigRead)
async def get_data_sync_config(db: AsyncSession = Depends(get_db)):
    return await SettingsService.get_data_sync_config_read(db)


@router.put("/data-sync-config", response_model=DataSyncConfigRead)
async def put_data_sync_config(
    payload: DataSyncConfigPut,
    db: AsyncSession = Depends(get_db),
):
    return await SettingsService.put_data_sync_config(db, payload)
