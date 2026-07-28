from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReviewCycleCreate(BaseModel):
    id: str = Field(..., max_length=128)
    name: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ReviewCyclePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    meta: Optional[dict[str, Any]] = None


class ReviewCycleRead(_ORMBase):
    id: str
    name: Optional[str] = None
    status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("name", mode="before")
    @classmethod
    def strip_active_suffix(cls, v: Any) -> Any:
        if isinstance(v, str):
            import re
            return re.sub(r"\s*[-–—]?\s*active\s*$", "", v, flags=re.IGNORECASE).strip() or None
        return v


class FyEndOptionsRead(BaseModel):
    review_cycle_id: str
    options: list[str]


class FyEndResolveRead(BaseModel):
    fy_end: str
    review_cycle_id: str
    review_cycle_name: Optional[str] = None


class ParameterThresholdCreate(BaseModel):
    key: str = Field(..., max_length=128)
    value: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ParameterThresholdPatch(BaseModel):
    value: Optional[dict[str, Any]] = None
    description: Optional[str] = None


class ParameterThresholdBulkPatchItem(BaseModel):
    id: int
    value: Optional[dict[str, Any]] = None
    description: Optional[str] = None


class ParameterThresholdBulkPatchRequest(BaseModel):
    items: list[ParameterThresholdBulkPatchItem] = Field(default_factory=list)


class ParameterThresholdRead(_ORMBase):
    id: int
    key: str
    value: dict[str, Any]
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ConfigTableCreate(BaseModel):
    key: str = Field(..., max_length=128)
    value: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ConfigTablePatch(BaseModel):
    value: Optional[dict[str, Any]] = None
    description: Optional[str] = None


class ConfigTableRead(_ORMBase):
    id: int
    key: str
    value: dict[str, Any]
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FinancialMetricTerm(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    sign: Literal["+", "-"]
    abs: bool = False

    model_config = ConfigDict(str_strip_whitespace=True)


class FinancialMetricMappingPack(BaseModel):
    """Persisted bundle of six formulas (each ordered list of path terms)."""

    revenue: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]
    ebitda: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]
    pbt: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]
    pat: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]
    cash: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]
    debt: Annotated[list[FinancialMetricTerm], Field(min_length=0, max_length=512)]


class FinancialMetricMappingPut(BaseModel):
    metrics: FinancialMetricMappingPack


class FinancialMetricMappingRead(BaseModel):
    metrics: FinancialMetricMappingPack


class FinancialMetricSchemaPathsRead(BaseModel):
    paths: list[str]


class SnowflakePRFinancialFormulaSpec(BaseModel):
    """Single arithmetic formula over ``PRSubmissionDataRaw`` numeric column names.

    An empty string is allowed (the metric is then skipped during the PR sync),
    mirroring the Financial extraction mapping's "empty term list" behaviour.
    """

    formula: str = Field(default="", max_length=8192)

    model_config = ConfigDict(str_strip_whitespace=True)


def _empty_spec() -> "SnowflakePRFinancialFormulaSpec":
    return SnowflakePRFinancialFormulaSpec(formula="")


class SnowflakePRPnlPack(BaseModel):
    """P&L (flow) metrics — read from an annual ``yr_1``/``yr_2`` column."""

    revenue: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)
    ebitda: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)
    pbt: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)
    pat: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)


class SnowflakePRBalancePack(BaseModel):
    """Balance-sheet (stock) metrics — read from a point-in-time column."""

    cash: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)
    debt: SnowflakePRFinancialFormulaSpec = Field(default_factory=_empty_spec)


def _empty_pnl() -> "SnowflakePRPnlPack":
    return SnowflakePRPnlPack()


def _empty_balance() -> "SnowflakePRBalancePack":
    return SnowflakePRBalancePack()


class SnowflakePRSurgeSeedGroup(BaseModel):
    """Surge / Seed: P&L always uses Year 1, so a single P&L slot + balance slot."""

    pnl: SnowflakePRPnlPack = Field(default_factory=_empty_pnl)
    balance: SnowflakePRBalancePack = Field(default_factory=_empty_balance)


class SnowflakePRGrowthVentureGroup(BaseModel):
    """Growth / Venture: P&L year depends on quarter alignment, so two P&L slots."""

    pnl_aligned: SnowflakePRPnlPack = Field(default_factory=_empty_pnl)  # Year 2
    pnl_lagged: SnowflakePRPnlPack = Field(default_factory=_empty_pnl)   # Year 1
    balance: SnowflakePRBalancePack = Field(default_factory=_empty_balance)


class SnowflakePRStageGroupsV2(BaseModel):
    surge_seed: SnowflakePRSurgeSeedGroup = Field(default_factory=SnowflakePRSurgeSeedGroup)
    growth_venture: SnowflakePRGrowthVentureGroup = Field(default_factory=SnowflakePRGrowthVentureGroup)


class SnowflakePRFinancialMappingPut(BaseModel):
    """PUT body (v2): per stage group, P&L vs Balance slots (aligned/lagged for G/V)."""

    stage_groups: SnowflakePRStageGroupsV2 = Field(default_factory=SnowflakePRStageGroupsV2)


class SnowflakePRFinancialMappingRead(BaseModel):
    """GET response: same structure as the PUT body."""

    stage_groups: SnowflakePRStageGroupsV2 = Field(default_factory=SnowflakePRStageGroupsV2)


class SnowflakePRNumericColumnsRead(BaseModel):
    columns: list[str]


# ---------------------------------------------------------------------------
# Data sync schedule config
# ---------------------------------------------------------------------------

class DataSyncConfigRead(BaseModel):
    frequency: Literal["daily", "weekly"]
    enabled: bool
    time: str
    timezone: str
    day_of_week: str
    cutoff_date: Optional[date] = None


class DataSyncConfigPut(BaseModel):
    frequency: Literal["daily", "weekly"]
    cutoff_date: Optional[date] = None
