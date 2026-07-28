from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FINANCIAL_METRIC_KEYS = frozenset({"revenue", "ebitda", "pbt", "pat", "cash", "debt"})


class ReconciliationStatus(str, enum.Enum):
    """Allowed values for financial_metric_reconciliation.status and manual_reconciliation_queries.status."""

    OPEN = "Open"
    SENT = "Sent"
    NOT_SENT = "Not Sent (if No)"
    PARTIAL = "Partial"
    CLOSED = "Closed"
    SENT_FLAGGED = "Sent - Flagged"
    PARTIAL_FLAGGED = "Partial - Flagged"
    CLOSED_FLAGGED = "Closed - Flagged"


class EntityReviewStatus(str, enum.Enum):
    """Allowed values for ``entities.status`` — the per-entity audit workflow state.

    This is the single source of truth for audit state. It replaces the former
    company-level ``portfolio_companies.review_stage``: every lifecycle stage that
    used to live on the company now lives here, at entity granularity. The first
    three members are non-workflow scoping/archival states; the remainder mirror
    the audit lifecycle formerly tracked on ``review_stage``.
    """

    # Non-workflow scoping / archival states
    SCOPED_IN = "Scoped In"
    SCOPED_OUT = "Scoped Out"
    ARCHIVE_ENTITY = "Archive Entity"

    # Audit lifecycle (formerly CompanyReviewStage on review_stage)
    NOT_APPLICABLE = "Not applicable"
    FINANCIALS_TO_BE_RECEIVED = "Financials to be received"
    IN_REVIEW = "In review"
    DISCREPANCY_IDENTIFIED = "Discrepancy identified"
    NO_DISCREPANCY_IDENTIFIED = "No discrepancy identified"
    NOT_COMPARABLE = "Not comparable"
    QUERY_SENT = "Query sent"
    QUERY_RESPONSE_REMINDER_1 = "Query response reminder sent 1"
    QUERY_RESPONSE_REMINDER_2 = "Query response reminder sent 2"
    RESPONSE_RECEIVED = "Response received"
    RESPONSE_RECEIVED_PARTIAL = "Response received - Partially answered"
    RESPONSE_RECEIVED_CALL = "Response received - Call to be scheduled"
    APPROVED = "Approved"
    APPROVED_FLAGGED = "Approved - Flagged"
    NOT_APPROVED = "Not Approved"
    NOT_APPROVED_FLAGGED = "Not Approved - Flagged"


# Back-compat alias: ``EntityAuditStatus`` is imported/exported in several modules.
# It now resolves to the full review-status vocabulary above.
EntityAuditStatus = EntityReviewStatus


# Entity statuses that count as "actively under review" — past intake
# ("Financials to be received") but not yet a terminal approval decision.
# Used to gate the In Review Tracker (membership = company has >=1 such entity).
ENTITY_ACTIVE_REVIEW_STATUSES: frozenset[str] = frozenset({
    EntityReviewStatus.IN_REVIEW.value,
    EntityReviewStatus.DISCREPANCY_IDENTIFIED.value,
    EntityReviewStatus.NO_DISCREPANCY_IDENTIFIED.value,
    EntityReviewStatus.NOT_COMPARABLE.value,
    EntityReviewStatus.QUERY_SENT.value,
    EntityReviewStatus.QUERY_RESPONSE_REMINDER_1.value,
    EntityReviewStatus.QUERY_RESPONSE_REMINDER_2.value,
    EntityReviewStatus.RESPONSE_RECEIVED.value,
    EntityReviewStatus.RESPONSE_RECEIVED_PARTIAL.value,
    EntityReviewStatus.RESPONSE_RECEIVED_CALL.value,
})


# Entity statuses that may appear in the In Review Tracker = the active-review set.
# "Financials to be received" is a pre-review intake stage (financials not yet received)
# and is intentionally excluded — an entity appears only once its review has started.
# Both tracker membership (a company appears if it has >=1 such entity) and the listed
# entity rows are restricted to these statuses — no other entity is shown.
ENTITY_IN_REVIEW_TRACKER_STATUSES: frozenset[str] = ENTITY_ACTIVE_REVIEW_STATUSES


class CompanyReviewStage(str, enum.Enum):
    """DEPRECATED. Former allowed values for ``portfolio_companies.review_stage``.

    State management has moved to ``entities.status`` (see ``EntityReviewStatus``),
    which carries the identical lifecycle vocabulary. This enum is retained only so
    historical references and migrations keep resolving; no flow writes
    ``review_stage`` anymore.
    """

    NOT_APPLICABLE = "Not applicable"
    FINANCIALS_TO_BE_RECEIVED = "Financials to be received"
    IN_REVIEW = "In review"
    DISCREPANCY_IDENTIFIED = "Discrepancy identified"
    NO_DISCREPANCY_IDENTIFIED = "No discrepancy identified"
    NOT_COMPARABLE = "Not comparable"
    QUERY_SENT = "Query sent"
    QUERY_RESPONSE_REMINDER_1 = "Query response reminder sent 1"
    QUERY_RESPONSE_REMINDER_2 = "Query response reminder sent 2"
    RESPONSE_RECEIVED = "Response received"
    RESPONSE_RECEIVED_PARTIAL = "Response received - Partially answered"
    RESPONSE_RECEIVED_CALL = "Response received - Call to be scheduled"
    APPROVED = "Approved"
    APPROVED_FLAGGED = "Approved - Flagged"
    NOT_APPROVED = "Not Approved"
    NOT_APPROVED_FLAGGED = "Not Approved - Flagged"


class DealLevelStage1(str, enum.Enum):
    """Allowed values for ``portfolio_company_metadata.deal_level_stage_1``."""

    COMPLETED_WITHIN_DUE_DATE = "Completed within due date"
    COMPLETED_POST_DUE_DATE = "Completed post due date"
    OVERDUE = "Overdue"
    EXPECTED_TO_COMPLETE_WITHIN_TIMELINE = "Expected to complete within timeline"
    EXPECTED_DELAY = "Expected delay"


class DealLevelStage2(str, enum.Enum):
    """Allowed values for ``portfolio_company_metadata.deal_level_stage_2``."""

    OPERATING_SUBSIDIARY_COMPLETED_HOLDING_PENDING = "Operating subsidiary completed, Holding pending"
    STANDALONE_COMPLETED_CONSOLIDATED_PENDING = "Standalone completed, Consolidated pending"
    CLOSED = "Closed"
    CLOSED_FLAGGED = "Closed - Flagged"


class _ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PortfolioCompanyCreate(BaseModel):
    company_id: str = Field(..., max_length=64)
    name: str = Field(..., max_length=255)

    contact_name: Optional[str] = None
    contact_email_id: Optional[str] = None
    contact_contact_id: Optional[str] = None

    investment_stage: Optional[str] = None
    investment_type: Optional[str] = None

    review_cycle_id: Optional[str] = Field(default=None, max_length=128)
    review_stage: Optional[str] = Field(default=None, max_length=128)
    org_chart_file_id: Optional[int] = None

    partner_email: Optional[str] = None
    prepcreator_audit_email: Optional[str] = None
    reviewer_audit_email: Optional[str] = None

    fund: Optional[str] = Field(default=None, max_length=255)
    investment_lead: Optional[str] = Field(default=None, max_length=255)
    company_stage: Optional[str] = Field(default=None, max_length=64)
    geography: Optional[str] = Field(default=None, max_length=64)
    ownership_pct: Optional[str] = Field(default=None, max_length=128)
    cost: Optional[str] = Field(default=None, max_length=128)
    fmv: Optional[str] = Field(default=None, max_length=128)
    position_is_unique: Optional[str] = Field(default=None, max_length=32)
    consolidated_ownership_pct: Optional[str] = Field(default=None, max_length=128)
    consolidated_cost: Optional[str] = Field(default=None, max_length=128)
    consolidated_fmv: Optional[str] = Field(default=None, max_length=128)
    company_category_1: Optional[str] = Field(default=None, max_length=128)
    company_category_2: Optional[str] = Field(default=None, max_length=128)
    scoped_in_for_audit: Optional[str] = Field(default=None, max_length=32)
    exclusion_reason: Optional[str] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    fy_end_date: Optional[str] = Field(default=None, max_length=32)
    currency: Optional[str] = Field(default=None, max_length=8)
    due_date: Optional[str] = Field(default=None, max_length=32)
    audit_status: Optional[str] = Field(default=None, max_length=128)
    auditor: Optional[str] = Field(default=None, max_length=255)
    tentative_completion_date: Optional[str] = Field(default=None, max_length=32)
    company_response: Optional[str] = None
    peak_xv_actionable: Optional[str] = None
    reason_to_scope_out: Optional[str] = None

    extra_data: dict[str, Any] = Field(default_factory=dict)


class PortfolioCompanyPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)

    contact_name: Optional[str] = None
    contact_email_id: Optional[str] = None
    contact_contact_id: Optional[str] = None

    investment_stage: Optional[str] = None
    investment_type: Optional[str] = None

    review_cycle_id: Optional[str] = Field(default=None, max_length=128)
    review_stage: Optional[CompanyReviewStage] = None
    org_chart_file_id: Optional[int] = None

    partner_email: Optional[str] = None
    prepcreator_audit_email: Optional[str] = None
    reviewer_audit_email: Optional[str] = None

    fund: Optional[str] = Field(default=None, max_length=255)
    investment_lead: Optional[str] = Field(default=None, max_length=255)
    company_stage: Optional[str] = Field(default=None, max_length=64)
    geography: Optional[str] = Field(default=None, max_length=64)
    ownership_pct: Optional[str] = Field(default=None, max_length=128)
    cost: Optional[str] = Field(default=None, max_length=128)
    fmv: Optional[str] = Field(default=None, max_length=128)
    position_is_unique: Optional[str] = Field(default=None, max_length=32)
    consolidated_ownership_pct: Optional[str] = Field(default=None, max_length=128)
    consolidated_cost: Optional[str] = Field(default=None, max_length=128)
    consolidated_fmv: Optional[str] = Field(default=None, max_length=128)
    company_category_1: Optional[str] = Field(default=None, max_length=128)
    company_category_2: Optional[str] = Field(default=None, max_length=128)
    scoped_in_for_audit: Optional[str] = Field(default=None, max_length=32)
    exclusion_reason: Optional[str] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    fy_end_date: Optional[str] = Field(default=None, max_length=32)
    currency: Optional[str] = Field(default=None, max_length=8)
    due_date: Optional[str] = Field(default=None, max_length=32)
    audit_status: Optional[str] = Field(default=None, max_length=128)
    auditor: Optional[str] = Field(default=None, max_length=255)
    tentative_completion_date: Optional[str] = Field(default=None, max_length=32)
    company_response: Optional[str] = None
    peak_xv_actionable: Optional[str] = None
    reason_to_scope_out: Optional[str] = None

    extra_data: Optional[dict[str, Any]] = None
    edit_reason: Optional[str] = Field(default=None, max_length=4000)


class OrgChartClearResponse(BaseModel):
    """Removing a company's org chart file deletes that file and all org entities (extracted + manual)."""

    deleted_entities: int
    deleted_file_id: Optional[int] = None


class PortfolioCompanyRead(_ORMBase):
    id: int
    company_id: str
    name: str

    contact_name: Optional[str] = None
    contact_email_id: Optional[str] = None
    contact_contact_id: Optional[str] = None

    investment_stage: Optional[str] = None
    investment_type: Optional[str] = None
    review_cycle_id: Optional[str] = None
    review_stage: Optional[str] = None
    org_chart_file_id: Optional[int] = None

    partner_email: Optional[str] = None
    prepcreator_audit_email: Optional[str] = None
    reviewer_audit_email: Optional[str] = None

    fund: Optional[str] = None
    investment_lead: Optional[str] = None
    company_stage: Optional[str] = None
    geography: Optional[str] = None
    ownership_pct: Optional[str] = None
    cost: Optional[str] = None
    fmv: Optional[str] = None
    position_is_unique: Optional[str] = None
    consolidated_ownership_pct: Optional[str] = None
    consolidated_cost: Optional[str] = None
    consolidated_fmv: Optional[str] = None
    company_category_1: Optional[str] = None
    company_category_2: Optional[str] = None
    scoped_in_for_audit: Optional[str] = None
    exclusion_reason: Optional[str] = None
    fy_end: Optional[str] = None
    fy_end_date: Optional[str] = None
    currency: Optional[str] = None
    due_date: Optional[str] = None
    audit_status: Optional[str] = None
    auditor: Optional[str] = None
    tentative_completion_date: Optional[str] = None
    company_response: Optional[str] = None
    peak_xv_actionable: Optional[str] = None
    reason_to_scope_out: Optional[str] = None

    extra_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    has_orphan_files: bool = False


class CycleEntityRowRead(PortfolioCompanyRead):
    """One Audit Tracker row = one entity (or a company placeholder when no entities exist).

    When ``has_entities`` is True the row represents a real entity and all
    ``entity_*`` fields are populated.  When False the company has no entities
    yet; entity fields are None and ``entity_status`` is set to the synthetic
    label "Upload org chart / create entities".  ``has_org_chart`` reflects
    whether the company already has an org-chart file on record.
    """

    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    entity_status: Optional[str] = None
    entity_type: Optional[str] = None
    parent_entity_id: Optional[int] = None
    entity_is_parent: bool = False
    entity_review_cycle: Optional[str] = None
    has_entities: bool = True
    has_org_chart: bool = False


class InReviewTrackerRowRead(BaseModel):
    portfolio_company_id: int
    company_name: str
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    entity_status: Optional[str] = None
    review_cycle_id: Optional[str] = None
    contact_name: Optional[str] = None
    has_files_without_entity: bool = False
    has_pending_org_chart_reconciliation: bool = False


class PortfolioCompanyBulkItem(BaseModel):
    """One row for POST /portfolio-companies/bulk-upsert."""

    company_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(..., max_length=255)
    contact_name: Optional[str] = None
    contact_email_id: Optional[str] = None
    review_cycle_id: Optional[str] = Field(default=None, max_length=128)
    review_stage: Optional[str] = Field(default=None, max_length=128)

    fund: Optional[str] = Field(default=None, max_length=255)
    investment_lead: Optional[str] = Field(default=None, max_length=255)
    company_stage: Optional[str] = Field(default=None, max_length=64)
    geography: Optional[str] = Field(default=None, max_length=64)
    ownership_pct: Optional[str] = Field(default=None, max_length=128)
    cost: Optional[str] = Field(default=None, max_length=128)
    fmv: Optional[str] = Field(default=None, max_length=128)
    position_is_unique: Optional[str] = Field(default=None, max_length=32)
    consolidated_ownership_pct: Optional[str] = Field(default=None, max_length=128)
    consolidated_cost: Optional[str] = Field(default=None, max_length=128)
    consolidated_fmv: Optional[str] = Field(default=None, max_length=128)
    company_category_1: Optional[str] = Field(default=None, max_length=128)
    company_category_2: Optional[str] = Field(default=None, max_length=128)
    scoped_in_for_audit: Optional[str] = Field(default=None, max_length=32)
    exclusion_reason: Optional[str] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    fy_end_date: Optional[str] = Field(default=None, max_length=32)
    due_date: Optional[str] = Field(default=None, max_length=32)
    audit_status: Optional[str] = Field(default=None, max_length=128)
    auditor: Optional[str] = Field(default=None, max_length=255)
    tentative_completion_date: Optional[str] = Field(default=None, max_length=32)
    company_response: Optional[str] = None
    peak_xv_actionable: Optional[str] = None
    reason_to_scope_out: Optional[str] = None


class PortfolioCompanyBulkUpsertRequest(BaseModel):
    items: list[PortfolioCompanyBulkItem]


class PortfolioCompanyBulkUpsertResponse(BaseModel):
    items: list[PortfolioCompanyRead]


ENTITY_TYPE_KNOWN_VALUES: tuple[str, ...] = (
    "Intermediate Holding",
    "Associate",
    "Branch",
    "Ultimate Holding",
    "Other",
    "Subsidiary",
    "Holding",
)


class EntityCreate(BaseModel):
    portfolio_company_id: int
    name: str = Field(..., max_length=255)

    geolocation: Optional[str] = None
    entity_type: Optional[str] = Field(default=None, max_length=32)
    review_cycle: Optional[str] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    status: Optional[EntityAuditStatus] = None
    parent_entity_id: Optional[int] = None
    region: Optional[str] = None
    is_parent: bool = False
    extra_data: dict[str, Any] = Field(default_factory=dict)
    comments: Optional[str] = None
    one_desk_email_status: Optional[str] = Field(default=None, max_length=128)


class EntityPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    geolocation: Optional[str] = None
    entity_type: Optional[str] = Field(default=None, max_length=32)
    review_cycle: Optional[str] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    status: Optional[EntityAuditStatus] = None
    parent_entity_id: Optional[int] = None
    region: Optional[str] = None
    is_parent: Optional[bool] = None
    extra_data: Optional[dict[str, Any]] = None
    comments: Optional[str] = None
    one_desk_email_status: Optional[str] = Field(default=None, max_length=128)


class EntityRead(_ORMBase):
    id: int
    portfolio_company_id: int
    name: str

    geolocation: Optional[str] = None
    entity_type: Optional[str] = None
    review_cycle: Optional[str] = None
    fy_end: Optional[str] = None
    status: Optional[str] = None
    parent_entity_id: Optional[int] = None
    region: Optional[str] = None
    is_parent: bool
    extra_data: dict[str, Any]
    comments: Optional[str] = None
    one_desk_email_status: Optional[str] = None

    portfolio_company_name: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class FinancialMetricReconciliationPatch(BaseModel):
    """PATCH one reconciliation row — AFS amount/currency edits require ``edit_reason``.

    MIS amounts are resolved live from ``FinancialDataSnowflake`` and cannot be patched here.
    """

    model_config = ConfigDict(extra="ignore")

    afs_amount: Optional[float] = None
    afs_currency: Optional[str] = Field(default=None, max_length=8)
    frequency: Optional[str] = Field(default=None, max_length=32)
    discrepency_text: Optional[str] = None
    status: Optional[ReconciliationStatus] = None
    enable: Optional[bool] = None
    company_response: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    variance_category: Optional[str] = None
    flagged: Optional[bool] = None
    extra_data: Optional[dict[str, Any]] = None
    edit_reason: Optional[str] = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_edit_reason_when_afs_changes(self) -> "FinancialMetricReconciliationPatch":
        amt_keys = {"afs_amount", "afs_currency"}
        touched = amt_keys.intersection(self.model_fields_set)
        if touched and not (self.edit_reason or "").strip():
            raise ValueError(
                "edit_reason is required when updating AFS metric amounts or currency fields"
            )
        return self


class MappingBreakdownTerm(BaseModel):
    path: str
    sign: str
    raw_value: Optional[float] = None
    contribution: float
    source: str  # "ocr" | "default_zero"


class MappingBreakdownDerivedComponent(BaseModel):
    path: Optional[str] = None
    value: Optional[float] = None


class MappingBreakdownDerived(BaseModel):
    computed: bool
    formula: str
    components: dict[str, MappingBreakdownDerivedComponent]


class MappingBreakdown(BaseModel):
    """Per-metric breakdown produced by Financial Extraction Mapping evaluation.

    Stored under ``FinancialMetricReconciliation.extra_data['mapping_breakdown'][metric_key]``
    by the extraction sync; surfaced here as a typed field for UI consumption.
    """

    model_config = ConfigDict(extra="ignore")

    computed_at: Optional[str] = None
    config_signature: Optional[str] = None
    currency: Optional[str] = None
    total: float
    terms: list[MappingBreakdownTerm]
    missing_paths: list[str] = Field(default_factory=list)
    derived_components: Optional[dict[str, MappingBreakdownDerived]] = None


class FinancialMetricReconciliationRead(_ORMBase):
    id: int
    portfolio_company_id: int
    entity_id: int
    entity_name: Optional[str] = None

    review_cycle: str
    metric_key: str
    frequency: Optional[str] = None

    afs_amount: Optional[float] = None
    afs_currency: Optional[str] = None
    # Computed at read time from FinancialDataSnowflake — never stored on this table.
    mis_amount: Optional[float] = None
    mis_currency: Optional[str] = None

    category: str
    type: Optional[str] = None
    discrepency_text: str
    status: Optional[ReconciliationStatus] = None
    enable: bool
    company_response: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    variance_category: Optional[str] = None
    flagged: bool

    extra_data: dict[str, Any]
    mapping_breakdown: Optional[MappingBreakdown] = None
    # Per-field manual-edit markers (keyed by column, e.g. "afs_amount"): drives the
    # "edited manually" badge + justification popup. None when nothing was edited.
    manual_edits: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class ManualReconciliationQueryCreate(BaseModel):
    portfolio_company_id: int
    entity_id: int
    discrepency_text: str

    category: str = Field(default="manual", max_length=16)
    type: Optional[str] = Field(default=None, max_length=64)
    status: Optional[ReconciliationStatus] = ReconciliationStatus.OPEN
    enable: bool = True
    company_response: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    flagged: bool = False
    variance_category: Optional[str] = None


class ManualReconciliationQueryPatch(BaseModel):
    discrepency_text: Optional[str] = None
    type: Optional[str] = Field(default=None, max_length=64)
    status: Optional[ReconciliationStatus] = None
    enable: Optional[bool] = None
    company_response: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    flagged: Optional[bool] = None
    variance_category: Optional[str] = None


class ManualReconciliationQueryRead(_ORMBase):
    id: int
    portfolio_company_id: int
    entity_id: int
    entity_name: Optional[str] = None

    discrepency_text: str
    category: str
    type: Optional[str] = None
    status: Optional[ReconciliationStatus] = None
    enable: bool
    company_response: Optional[str] = None
    reviewer_remarks: Optional[str] = None
    flagged: bool
    variance_category: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class FinancialDataSnowflakeCreate(BaseModel):
    """POST body to create a company-level Snowflake / MIS row with a single manually-entered metric."""

    model_config = ConfigDict(extra="ignore")

    portfolio_company_id: int = Field(..., ge=1)
    entity_id: Optional[int] = None
    review_cycle: Optional[str] = None
    frequency: Optional[str] = None
    currency: Optional[str] = None
    metric_key: str = Field(..., pattern=r"^(revenue|ebitda|pbt|pat|cash|debt)$")
    metric_value: float
    edit_reason: str = Field(..., min_length=1, max_length=4000)


class FinancialDataSnowflakePatch(BaseModel):
    """PATCH body for Snowflake / MIS financial rows — metrics require ``edit_reason``."""

    model_config = ConfigDict(extra="ignore")

    portfolio_company_id: Optional[int] = Field(default=None, ge=1)
    entity_id: Optional[int] = None
    review_cycle: Optional[str] = None
    frequency: Optional[str] = None
    currency: Optional[str] = None

    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    pbt: Optional[float] = None
    pat: Optional[float] = None
    cash: Optional[float] = None
    debt: Optional[float] = None

    extra_data: Optional[dict[str, Any]] = None
    edit_reason: Optional[str] = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_edit_reason_when_metrics_change(self) -> "FinancialDataSnowflakePatch":
        touched = _FINANCIAL_METRIC_KEYS.intersection(self.model_fields_set)
        if touched and not (self.edit_reason or "").strip():
            raise ValueError("edit_reason is required when updating financial metrics")
        return self


class SnowflakeEntityAttachmentPatch(BaseModel):
    """Attach, reassign, or detach a Snowflake financial row to an org-chart entity."""

    entity_id: Optional[int] = Field(
        default=None,
        description="Entity to attach. Pass null to detach.",
    )


class FinancialDataSnowflakeRead(BaseModel):
    id: int
    source_ref: Optional[str] = None
    portfolio_company_id: Optional[int] = None
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    review_cycle: Optional[str] = None
    frequency: Optional[str] = None
    currency: Optional[str] = None
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    pbt: Optional[float] = None
    pat: Optional[float] = None
    cash: Optional[float] = None
    debt: Optional[float] = None
    extra_data: dict[str, Any] = Field(default_factory=dict)
    # Per-metric manual-edit markers (keyed by metric, e.g. "ebitda"); None when nothing edited.
    manual_edits: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConvertCurrencyRequest(BaseModel):
    target_currency: str = Field(..., pattern=r"^[A-Za-z]{3}$", description="ISO-4217 target currency code")


class EntityReviewCycleConvertCurrencyRequest(BaseModel):
    """Convert all tall AFS metric rows sharing (entity_id, review_cycle) to ``target_currency``."""

    entity_id: int = Field(..., ge=1)
    review_cycle: str = Field(..., max_length=128)
    target_currency: str = Field(..., pattern=r"^[A-Za-z]{3}$", description="ISO-4217 target currency code")


class FileCreate(BaseModel):
    portfolio_company_id: Optional[int] = None
    entity_id: Optional[int] = None
    review_cycle_id: Optional[str] = None

    filename: str = Field(..., max_length=512)
    content_type: Optional[str] = None
    storage_uri: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class FilePatch(BaseModel):
    """Partial update. `portfolio_company_id` reassigns or clears the file's company assignment."""
    portfolio_company_id: Optional[int] = Field(default=None, ge=1)
    entity_id: Optional[int] = None
    fy_end: Optional[str] = Field(default=None, max_length=16)
    review_cycle_id: Optional[str] = None
    filename: Optional[str] = Field(default=None, max_length=512)
    content_type: Optional[str] = None
    storage_uri: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    entity_detached_acknowledged: Optional[bool] = None


class BulkFileUploadResultItem(BaseModel):
    filename: str
    status: str = Field(..., pattern=r"^(success|failed)$")
    file_id: Optional[int] = None
    error: Optional[str] = None
    # Non-blocking warning: another non-deleted audit file already exists for this
    # entity + review cycle. The upload still succeeds; this just informs the user.
    duplicate_warning: Optional[bool] = None
    duplicate_warning_message: Optional[str] = None
    existing_files: Optional[list[dict]] = None


class BulkFileUploadResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BulkFileUploadResultItem]


class FileRead(_ORMBase):
    id: int
    portfolio_company_id: Optional[int] = None
    entity_id: Optional[int] = None
    review_cycle_id: Optional[str] = None
    filename: str
    content_type: Optional[str] = None
    storage_uri: Optional[str] = None
    size_bytes: Optional[int] = None
    status: Optional[str] = None
    tags: list[str]
    portfolio_company_name: Optional[str] = None
    portfolio_company_review_cycle_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_geolocation: Optional[str] = None
    entity_fy_end: Optional[str] = None
    entity_detached_acknowledged: bool = False
    created_at: datetime
    updated_at: datetime
    # When the document's extraction output was last written (FileOCRMetadata.updated_at) — i.e.
    # when it was last processed. None if it has never been processed.
    processed_at: Optional[datetime] = None


class AuditFinancialUnmatchedMapRequest(BaseModel):
    """Map one LLM ``unmatched`` row into the canonical tree under a parent path."""

    unmatched_id: str = Field(..., min_length=1)
    target_parent_path: str = Field(..., min_length=1)
    target_key: Optional[str] = None
    confirm_overwrite: bool = False
    # How to resolve an occupied leaf (multiple document lines → one canonical tag):
    #   "sum"     → append as a signed roll-up component (no data loss; default on confirm)
    #   "total"   → treat the incoming line as the stated total; keep prior lines as breakdown
    #   "replace" → overwrite (legacy behaviour)
    # When None and the slot is occupied, the endpoint returns a 409 with breakdown + a
    # double-count warning so the UI can ask.
    conflict_mode: Optional[Literal["sum", "total", "replace"]] = None
    reason: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional justification for the manual mapping (shown in the 'edited manually' popup).",
    )


class AuditFinancialFieldDetachRequest(BaseModel):
    """Remove a field from the extracted tree and move it to the unmatched panel (recoverable).

    The inverse of :class:`AuditFinancialUnmatchedMapRequest` — a manual escape hatch for a
    duplicate / mis-mapped line the automatic dedupe missed. The removed line reappears in the
    unmatched list (re-attachable), so nothing is lost.
    """

    path: str = Field(..., min_length=3, description="Dot path of the field to remove, e.g. balance_sheet.assets.current_assets.short_term_investments")


class AuditFinancialComponentInput(BaseModel):
    """One contributing line in a canonical field's roll-up (lead schedule)."""

    label: str = Field(..., min_length=1)
    value: float = 0.0
    sign: Literal["+", "-"] = "+"


class AuditFinancialFieldComponentsRequest(BaseModel):
    """Replace the full component breakdown for one canonical leaf; the leaf becomes Σ signed."""

    path: str = Field(..., min_length=1)
    components: list[AuditFinancialComponentInput] = Field(default_factory=list)
    reason: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional justification for the manual breakdown edit (shown in the 'edited manually' popup).",
    )


class AuditFinancialDismissUnmatchedRequest(BaseModel):
    """Mark an unmatched row as dismissed so it moves to the 'dismissed' section.

    The row is NOT deleted — it remains in ``audit_financials_unmatched`` with
    ``dismissed: true`` so it is always recoverable.
    """

    unmatched_id: str = Field(..., min_length=1, description="ID of the unmatched row to dismiss")


class AuditFinancialRestoreUnmatchedRequest(BaseModel):
    """Clear the dismissed flag on a previously dismissed unmatched row."""

    unmatched_id: str = Field(..., min_length=1, description="ID of the dismissed row to restore")


class ExtractionCurrencyPatchRequest(BaseModel):
    """Manually set the currency label on a file's extraction metadata.

    The currency is stored as a 3-letter ISO-4217 code in ``ocr_json.currency``
    alongside ``ocr_json.currency_source = "manual"``. It is applied purely as a
    display label on numeric values; it does not convert or rewrite any numbers.
    """

    currency: str = Field(..., pattern=r"^[A-Za-z]{3}$", description="ISO-4217 3-letter code, e.g. 'INR', 'USD'")


class ExtractionConvertCurrencyApplyRequest(BaseModel):
    """Persist FX scaling for P&amp;L / balance sheet / cash flow amounts on a completed audit-financials file."""

    target_currency: str = Field(..., pattern=r"^[A-Za-z]{3}$", description="Target ISO-4217 code after conversion")


class AuditFinancialExtractedValuePatchRequest(BaseModel):
    """Patch one numeric leaf under ``ocr_json.extracted`` (canonical audit_financials tree)."""

    path: str = Field(
        ...,
        min_length=5,
        description="Dot path from the statement root, e.g. profit_and_loss.tax.current_tax",
    )
    value: Optional[Union[float, int]] = Field(default=None, description="Amount, or null to clear the cell")
    reason: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional justification for the manual edit (shown in the 'edited manually' popup).",
    )

    @field_validator("value")
    @classmethod
    def reject_bool(cls, v: Optional[Union[float, int]]) -> Optional[Union[float, int]]:
        if isinstance(v, bool):
            raise ValueError("value must be a number or null, not a boolean")
        return v


class AuditFinancialAddCompositeFieldRequest(BaseModel):
    """Create an empty composite (bucket) node at a dotted path under the extracted tree."""

    path: str = Field(
        ...,
        min_length=3,
        description="Dot path for the new composite node, e.g. profit_and_loss.operating_expenses",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional justification shown in the audit trail.",
    )
