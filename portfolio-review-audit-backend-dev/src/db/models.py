from __future__ import annotations

import uuid as uuid_stdlib
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship


class Base:  # placeholder, overwritten below
    pass


APP_SCHEMA = "portfolioauditreview"


try:
    # Backend uses its own Declarative Base in `src.db.session`.
    # Importing here keeps a single metadata registry for migrations/runtime.
    from src.db.session import Base as _BackendBase

    Base = _BackendBase
except Exception:
    # Allows this module to be imported in isolation (e.g. tooling) without backend context.
    from sqlalchemy.orm import DeclarativeBase as _DeclarativeBase

    class Base(_DeclarativeBase):
        pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PortfolioCompanyMetadata(TimestampMixin, Base):
    __tablename__ = "portfolio_company_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    fund: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    deal_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    deal_id_for_analysis: Mapped[Optional[str]] = mapped_column(String(100))
    deal_id_for_analysis_and_strategy: Mapped[Optional[str]] = mapped_column(String(100))
    deal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Classification
    il_main: Mapped[Optional[str]] = mapped_column(String(100))
    sector_l1: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    sector_l2: Mapped[Optional[str]] = mapped_column(String(100))
    geo_l1: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    geo_l2: Mapped[Optional[str]] = mapped_column(String(100))

    # Financials
    cost: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    distributed: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    proceeds: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    fmv: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    ownership: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))

    # Audit scoping
    scoping_for_audit: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    reason_for_exclusion: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    # Aggregated numeric values
    unique_by_company_id: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    unique_by_company_id_strategy: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    consolidated_cost: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    consolidated_fmv: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))

    # Audit workflow stages
    deal_level_stage_1: Mapped[Optional[str]] = mapped_column(String(100))
    deal_level_stage_2: Mapped[Optional[str]] = mapped_column(String(100))

    # Dates (stored as strings for flexible formatting)
    tentative_audit_completion_date: Mapped[Optional[str]] = mapped_column(String(100))
    fy_end: Mapped[Optional[str]] = mapped_column(String(100))

    # Auditor info
    auditor: Mapped[Optional[str]] = mapped_column(String(255))
    category_of_auditor: Mapped[Optional[str]] = mapped_column(String(100))

    # Prior year
    py_audit_status: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    # Review cycle
    review_cycle_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    # Free text
    comments: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("fund", "deal_id", "strategy", "review_cycle_id", name="uq_pcm_fund_deal_strategy_cycle"),
        Index("ix_pcm_fund_deal_strategy_cycle", "fund", "deal_id", "strategy", "review_cycle_id"),
        {"schema": APP_SCHEMA},
    )


class PortfolioCompany(TimestampMixin, Base):
    __tablename__ = "portfolio_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    company_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    contact_name: Mapped[Optional[str]] = mapped_column(String(255))
    contact_email_id: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    contact_contact_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    investment_stage: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    investment_type: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    review_cycle_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    review_stage: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    org_chart_file_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    partner_email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    prepcreator_audit_email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    reviewer_audit_email: Mapped[Optional[str]] = mapped_column(String(320), index=True)

    poc_email_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)
    poc_cc_email_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)

    fund: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    investment_lead: Mapped[Optional[str]] = mapped_column(String(255))
    company_stage: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    geography: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    ownership_pct: Mapped[Optional[str]] = mapped_column(String(128))
    cost: Mapped[Optional[str]] = mapped_column(String(128))
    fmv: Mapped[Optional[str]] = mapped_column(String(128))
    position_is_unique: Mapped[Optional[str]] = mapped_column(String(32))
    consolidated_ownership_pct: Mapped[Optional[str]] = mapped_column(String(128))
    consolidated_cost: Mapped[Optional[str]] = mapped_column(String(128))
    consolidated_fmv: Mapped[Optional[str]] = mapped_column(String(128))
    company_category_1: Mapped[Optional[str]] = mapped_column(String(128))
    company_category_2: Mapped[Optional[str]] = mapped_column(String(128))
    company_phase_category: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    scoped_in_for_audit: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    exclusion_reason: Mapped[Optional[str]] = mapped_column(Text)
    fy_end: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    fy_end_date: Mapped[Optional[str]] = mapped_column(String(32))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    investors: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)
    due_date: Mapped[Optional[str]] = mapped_column(String(32))
    audit_status: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    auditor: Mapped[Optional[str]] = mapped_column(String(255))
    tentative_completion_date: Mapped[Optional[str]] = mapped_column(String(32))
    company_response: Mapped[Optional[str]] = mapped_column(Text)
    peak_xv_actionable: Mapped[Optional[str]] = mapped_column(Text)
    reason_to_scope_out: Mapped[Optional[str]] = mapped_column(Text)

    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    entities: Mapped[list["Entity"]] = relationship(
        back_populates="portfolio_company", cascade="all, delete-orphan"
    )
    financial_metric_reconciliation: Mapped[list["FinancialMetricReconciliation"]] = relationship(
        back_populates="portfolio_company", cascade="all, delete-orphan"
    )
    manual_reconciliation_queries: Mapped[list["ManualReconciliationQuery"]] = relationship(
        back_populates="portfolio_company", cascade="all, delete-orphan"
    )
    files: Mapped[list["File"]] = relationship(
        back_populates="portfolio_company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "review_cycle_id",
            name="uq_portfolio_companies_company_cycle",
        ),
        Index("ix_portfolio_companies_name_company_id", "name", "company_id"),
        {"schema": APP_SCHEMA},
    )


class Entity(TimestampMixin, Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_company_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    geolocation: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    review_cycle: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    fy_end: Mapped[Optional[str]] = mapped_column(String(16), index=True)  #Mmm-YY
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    parent_entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.entities.id", ondelete="SET NULL"), index=True
    )

    region: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    is_parent: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=False, default=False)

    comments: Mapped[Optional[str]] = mapped_column(Text)
    one_desk_email_status: Mapped[Optional[str]] = mapped_column(String(128))

    # Email lifecycle timestamps (set once; never overwritten once non-NULL)
    discrepancy_email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_1_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_2_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_reply_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    portfolio_company: Mapped["PortfolioCompany"] = relationship(back_populates="entities")
    children: Mapped[list["Entity"]] = relationship(
        primaryjoin=lambda: Entity.id == foreign(Entity.parent_entity_id),
        cascade="save-update",
        passive_deletes=True,
    )

    financial_metric_reconciliation: Mapped[list["FinancialMetricReconciliation"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    manual_reconciliation_queries: Mapped[list["ManualReconciliationQuery"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    files: Mapped[list["File"]] = relationship(back_populates="entity")

    __table_args__ = (
        Index("ix_entities_company_name", "portfolio_company_id", "name"),
        Index("ix_entities_company_parent", "portfolio_company_id", "parent_entity_id"),
        {"schema": APP_SCHEMA},
    )


class FinancialMetricReconciliation(TimestampMixin, Base):
    """Tall reconciliation row per (company, entity, review cycle, metric key).

    MIS amounts are resolved live from company-level ``FinancialDataSnowflake`` at API read time.
    AFS/extracted amounts come from audit-financials extraction sync.
    """

    __tablename__ = "financial_metric_reconciliation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    portfolio_company_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_cycle: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    afs_amount: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    afs_currency: Mapped[Optional[str]] = mapped_column(String(8), index=True)

    frequency: Mapped[Optional[str]] = mapped_column(String(32))

    category: Mapped[str] = mapped_column(String(16), nullable=False, default="financial", index=True)
    type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    discrepency_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True, default="Open")
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    company_response: Mapped[Optional[str]] = mapped_column(Text)
    reviewer_remarks: Mapped[Optional[str]] = mapped_column(Text)
    variance_category: Mapped[Optional[str]] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    extracted_value_normalized: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    snowflake_value_normalized: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    target_currency: Mapped[Optional[str]] = mapped_column(String(8), index=True)
    extracted_source_currency: Mapped[Optional[str]] = mapped_column(String(8))
    snowflake_source_currency: Mapped[Optional[str]] = mapped_column(String(8))
    fx_date: Mapped[Optional[date]] = mapped_column(Date)
    extracted_fx_rate: Mapped[Optional[float]] = mapped_column(Float)
    snowflake_fx_rate: Mapped[Optional[float]] = mapped_column(Float)
    absolute_difference: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    percentage_difference: Mapped[Optional[float]] = mapped_column(Float)
    threshold_value: Mapped[Optional[float]] = mapped_column(Float)
    is_within_threshold: Mapped[Optional[bool]] = mapped_column(Boolean)
    discrepancy_status: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    last_reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    entity: Mapped["Entity"] = relationship(back_populates="financial_metric_reconciliation")
    portfolio_company: Mapped["PortfolioCompany"] = relationship(
        back_populates="financial_metric_reconciliation",
    )

    @property
    def mapping_breakdown(self) -> Optional[dict[str, Any]]:
        """Expose the per-metric breakdown stored in extra_data so Pydantic's
        from_attributes=True can read it as a top-level field on the schema."""
        bd = (self.extra_data or {}).get("mapping_breakdown")
        if not isinstance(bd, dict):
            return None
        return bd.get(self.metric_key) or None

    @property
    def manual_edits(self) -> Optional[dict[str, Any]]:
        """Expose the per-field manual-edit markers stored in
        ``extra_data['manual_edits']`` so the UI can flag manually edited cells
        (different colour + 'edited manually' disclaimer + justification popup).
        Keyed by column name, e.g. ``afs_amount``, ``status``."""
        me = (self.extra_data or {}).get("manual_edits")
        return me if isinstance(me, dict) and me else None

    __table_args__ = (
        UniqueConstraint(
            "portfolio_company_id",
            "entity_id",
            "review_cycle",
            "metric_key",
            name="uq_fmr_company_entity_cycle_metric",
        ),
        Index("ix_fmr_company_entity_cycle", "portfolio_company_id", "entity_id", "review_cycle"),
        {"schema": APP_SCHEMA},
    )


class ManualReconciliationQuery(TimestampMixin, Base):
    """Free-form manual queries included in outbound email text (below financial reconciliation)."""

    __tablename__ = "manual_reconciliation_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_company_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    discrepency_text: Mapped[str] = mapped_column(Text, nullable=False)

    category: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", index=True)
    type: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    company_response: Mapped[Optional[str]] = mapped_column(Text)
    reviewer_remarks: Mapped[Optional[str]] = mapped_column(Text)
    variance_category: Mapped[Optional[str]] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    portfolio_company: Mapped["PortfolioCompany"] = relationship(back_populates="manual_reconciliation_queries")
    entity: Mapped["Entity"] = relationship(back_populates="manual_reconciliation_queries")

    __table_args__ = (
        Index("ix_manual_queries_company_entity", "portfolio_company_id", "entity_id"),
        {"schema": APP_SCHEMA},
    )


class File(TimestampMixin, Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.entities.id", ondelete="SET NULL"), index=True
    )
    review_cycle_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey(f"{APP_SCHEMA}.review_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128))
    storage_uri: Mapped[Optional[str]] = mapped_column(Text)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    pending_audit_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    entity_detached_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    original_currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    # When true, this is the authoritative audited-financials file for its (entity, review cycle):
    # its extraction is the one that drives reconciliation / breakdowns / query emails. At most one
    # true per (entity_id, review_cycle_id), enforced by a partial unique index (see migration 0048).
    # When none is set the resolver falls back to the latest non-empty file (deterministic default).
    is_reconciliation_source: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # Email provenance — set when a file was created from an email attachment (null for manual uploads).
    source_email_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey(f"{APP_SCHEMA}.email_history.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_attachment_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    portfolio_company: Mapped[Optional["PortfolioCompany"]] = relationship(back_populates="files")
    entity: Mapped[Optional["Entity"]] = relationship(back_populates="files")
    ocr_metadata: Mapped[Optional["FileOCRMetadata"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_files_company_filename", "portfolio_company_id", "filename"),
        {"schema": APP_SCHEMA},
    )


PortfolioFile = File


class FileOCRMetadata(TimestampMixin, Base):
    __tablename__ = "file_ocr_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.files.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    ocr_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    auditor_opinion: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    audit_qualitative: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    # Per-file metric breakdown computed from the current mapping config.
    # Shape: {metric_key: {total, terms, missing_paths, config_signature, currency, computed_at}}
    # Written on every OCR sync and manual edit — independent of whether this file is the
    # primary AFS source, so it always reflects the latest extracted values for this file.
    metric_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    file: Mapped["File"] = relationship(back_populates="ocr_metadata")

    __table_args__ = {"schema": APP_SCHEMA}


class EmailTemplate(TimestampMixin, Base):
    __tablename__ = "email_templates"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    template_name: Mapped[Optional[str]] = mapped_column(String)
    body: Mapped[Optional[str]] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(String)
    version_id: Mapped[Optional[str]] = mapped_column(String)
    version_name: Mapped[Optional[str]] = mapped_column(String)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class DraftEmail(TimestampMixin, Base):
    __tablename__ = "draft_email"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    portfolio_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        index=True,
    )
    subject: Mapped[Optional[str]] = mapped_column(String)
    to_add: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String))
    cc: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String))
    email_body: Mapped[Optional[str]] = mapped_column(Text)
    template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.email_templates.id", ondelete="SET NULL"), index=True
    )
    attachments: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String))
    attachments_id: Mapped[Optional[str]] = mapped_column(String)
    affected_entity_ids: Mapped[Optional[list[int]]] = mapped_column(ARRAY(Integer))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    template: Mapped[Optional["EmailTemplate"]] = relationship()
    portfolio_company: Mapped[Optional["PortfolioCompany"]] = relationship()


class EmailHistory(Base):
    __tablename__ = "email_history"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    portfolio_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        index=True,
    )
    company_id: Mapped[Optional[str]] = mapped_column(String)
    company_pr_cycle_id: Mapped[Optional[str]] = mapped_column(String)
    sender: Mapped[Optional[str]] = mapped_column(String)
    recipients: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    cc: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    subject: Mapped[Optional[str]] = mapped_column(String)
    body: Mapped[Optional[str]] = mapped_column(Text)
    email_type: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_inbound: Mapped[Optional[bool]] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")

    associated_query_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String))
    bcc: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    reply_to: Mapped[Optional[str]] = mapped_column(String)
    body_html: Mapped[Optional[str]] = mapped_column(Text)
    template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.email_templates.id", ondelete="SET NULL"), index=True
    )
    affected_entity_ids: Mapped[Optional[list[int]]] = mapped_column(ARRAY(Integer))


class TempEmailHistory(Base):
    """
    Raw inbound email staging (parity with portfolio-review TempEmailHistory).
    Data is migrated into EmailHistory by the scheduled classify_incoming_emails job.
    """

    __tablename__ = "temp_email_history"
    __table_args__ = {"schema": APP_SCHEMA}


    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[Optional[str]] = mapped_column(String(200))
    to: Mapped[Optional[list[str]]] = mapped_column("to", ARRAY(Text))
    cc: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    email_type: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    created_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    updated_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    status: Mapped[Optional[int]] = mapped_column(SmallInteger)
    body_html: Mapped[Optional[str]] = mapped_column(Text)


_SOURCE_SCHEMA = "portfolioreview"


class SourceTempEmailHistory(Base):
    """
    Read-only view of portfolioreview.temp_email_history — the source staging table
    written by the third-party email ingestion system. Used by classify_incoming_emails
    so this app can process the same raw emails independently from pr-app-api.
    """

    __tablename__ = "temp_email_history"
    __table_args__ = {"schema": _SOURCE_SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[Optional[str]] = mapped_column(String(200))
    to: Mapped[Optional[list[str]]] = mapped_column("to", ARRAY(Text))
    cc: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    subject: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    email_type: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    created_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    updated_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    status: Mapped[Optional[int]] = mapped_column(SmallInteger)
    body_html: Mapped[Optional[str]] = mapped_column(Text)


class EmailClassificationBatchLog(Base):
    """Per-email batch outcome rows written by classify_incoming_emails (reference EmailProcessStatus)."""

    __tablename__ = "email_classification_batch_log"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    email_type: Mapped[Optional[str]] = mapped_column(String(255))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    email_payload: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(String(64))
    company_name: Mapped[Optional[str]] = mapped_column(String(512))
    company_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    email_message_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyViewAudit(Base):
    __tablename__ = "company_view_audit"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    company_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    action: Mapped[Optional[str]] = mapped_column(String, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReviewCycleViewAudit(Base):
    __tablename__ = "review_cycle_view_audit"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    review_cycle_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    action: Mapped[Optional[str]] = mapped_column(String, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ParameterThresholdViewAudit(Base):
    __tablename__ = "parameter_threshold_view_audit"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    action: Mapped[Optional[str]] = mapped_column(String, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FinancialExtractionMappingViewAudit(Base):
    __tablename__ = "financial_extraction_mapping_view_audit"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    action: Mapped[Optional[str]] = mapped_column(String, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SnowflakePRFinancialMappingViewAudit(Base):
    __tablename__ = "snowflake_pr_financial_mapping_view_audit"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    action: Mapped[Optional[str]] = mapped_column(String, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReviewCycle(TimestampMixin, Base):
    __tablename__ = "review_cycles"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ParameterThreshold(TimestampMixin, Base):
    __tablename__ = "parameter_thresholds"
    __table_args__ = (
        UniqueConstraint("key", name="uq_parameter_thresholds_key"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class ConfigTable(TimestampMixin, Base):
    __tablename__ = "config_table"
    __table_args__ = (
        UniqueConstraint("key", name="uq_config_table_key"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class FinancialDataSnowflake(TimestampMixin, Base):
    __tablename__ = "financial_data_snowflake"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Structured columns (present in some environments); keep optional so older
    # DBs can be upgraded via migration without breaking runtime imports.
    portfolio_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="CASCADE"),
        index=True,
    )
    entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.entities.id", ondelete="SET NULL"),
        index=True,
    )
    review_cycle: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), index=True)

    revenue: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    ebitda: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    pbt: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    pat: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    cash: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    debt: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))

    __table_args__ = (
        Index("ix_financial_data_snowflake_company_entity", "portfolio_company_id", "entity_id"),
        Index(
            "ix_financial_data_snowflake_company_review_cycle",
            "portfolio_company_id",
            "entity_id",
            "review_cycle",
        ),
        {"schema": APP_SCHEMA},
    )


class DailySyncProcessBatch(TimestampMixin, Base):
    __tablename__ = "daily_sync_process_batch"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ApschedulerJob(TimestampMixin, Base):
    __tablename__ = "apscheduler_jobs"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    schedule: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TemplateEmailHistory(TimestampMixin, Base):
    __tablename__ = "template_email_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.email_templates.id", ondelete="SET NULL"), index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = {"schema": APP_SCHEMA}


class EmailProcessStatus(TimestampMixin, Base):
    __tablename__ = "email_process_status"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    message: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class EmailProcessingCheckPoint(TimestampMixin, Base):
    __tablename__ = "email_processing_checkpoint"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checkpoint_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    checkpoint_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AuditEvent(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user','system','scheduler','api')",
            name="ck_audit_actor_type",
        ),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        {"schema": "audit"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    actor_id: Mapped[Optional[int]] = mapped_column(Integer)
    old_value_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    new_value_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    diff_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100))
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DataSyncProcessBatch(Base):
    """Tracks background data sync runs (Snowflake → raw → app tables)."""

    __tablename__ = "data_sync_process_batch"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    time_taken: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid_stdlib.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid_stdlib.uuid4
    )
    okta_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class CompanySyncNotification(TimestampMixin, Base):
    """One notification per new company_id surfaced during sync_company_master_to_portfolio."""

    __tablename__ = "company_sync_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    investment_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        Index("ix_company_sync_notifications_is_read_created_at", "is_read", "created_at"),
        {"schema": APP_SCHEMA},
    )


class FileUploadStatus:
    PENDING = "pending"
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    PROCESSED = "processed"  # e.g. after extraction / OCR pipeline
    FAILED = "failed"
    DELETED = "deleted"


class AuditActorType:
    USER = "user"
    SYSTEM = "system"
    SCHEDULER = "scheduler"
    API = "api"


class ExportJob(TimestampMixin, Base):
    """Tracks asynchronous XLSX audit-report export jobs."""

    __tablename__ = "export_jobs"
    __table_args__ = (
        Index("ix_export_jobs_status", "status"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    review_cycle_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    output_currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    storage_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class FxMonthlyRate(TimestampMixin, Base):
    """Month-end USD->X FX rates, one row per (from, to, month_end_date).

    Populated monthly (1st of month, for previous month-end) and via backfill.
    Always stored with ``from_currency == 'USD'``; cross rates are derived at
    lookup time by chaining through USD.
    """

    __tablename__ = "fx_monthly_rate"
    __table_args__ = (
        Index(
            "ix_fx_monthly_rate_lookup",
            "from_currency",
            "to_currency",
            "month_end_date",
            unique=True,
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    month_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    fx_timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class OrgChartUploadBatch(TimestampMixin, Base):
    """One batch = one ZIP upload for a review cycle."""

    __tablename__ = "org_chart_upload_batches"
    __table_args__ = (
        Index("ix_ocub_review_cycle", "review_cycle_id"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_cycle_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey(f"{APP_SCHEMA}.review_cycles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    original_zip_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # uploaded / partially_mapped / processing / completed / failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mapping_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    records: Mapped[list["OrgChartUploadRecord"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class OrgChartUploadRecord(TimestampMixin, Base):
    """One record = one file extracted from the ZIP."""

    __tablename__ = "org_chart_upload_records"
    __table_args__ = (
        Index("ix_ocur_batch", "batch_id"),
        Index("ix_ocur_review_cycle", "review_cycle_id"),
        Index("ix_ocur_portfolio_company", "portfolio_company_id"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.org_chart_upload_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_cycle_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    portfolio_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.portfolio_companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    extracted_org_chart: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    # pending_mapping / mapped / processing / completed / failed
    extraction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_mapping", index=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Reconciliation state: pending / applied / dismissed / failed
    reconciliation_status: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Stores mapping decisions + final entity map from reconcile endpoint
    reconciliation_payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    # DB ids of entities created/updated during apply/reconcile
    applied_entity_ids: Mapped[Optional[list[int]]] = mapped_column(ARRAY(Integer), nullable=True)

    batch: Mapped["OrgChartUploadBatch"] = relationship(back_populates="records")
    portfolio_company: Mapped[Optional["PortfolioCompany"]] = relationship()
    file: Mapped[Optional["File"]] = relationship()



# =============================================
#  (efront + raw data)
# =============================================



class PRSubmissionDataRaw(Base):
    """
    Raw PR Submission Data model matching PORTFOLIO_REVIEW_SS structure.
    Copied from portfolio-review-app-api-develop (schema override: portfolioauditreview).
    """

    __tablename__ = "pr_submission_data_raw"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    vlookup_value: Mapped[Optional[str]] = mapped_column(String(50))
    concatenate: Mapped[Optional[str]] = mapped_column(String(100))
    reporting_date: Mapped[Optional[date]] = mapped_column(Date) # YYYY-MM-DD
    status: Mapped[Optional[str]] = mapped_column(String(256))
    cid: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    fund_family: Mapped[Optional[str]] = mapped_column(String(300))
    company: Mapped[Optional[str]] = mapped_column(String(300))
    fye: Mapped[Optional[str]] = mapped_column(String(256))    # MMM-FY  Dec 24   MMM fy
    bookings_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    bookings_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    currency: Mapped[Optional[str]] = mapped_column(String(256))
    revenue_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    clarification: Mapped[Optional[str]] = mapped_column(Text)
    revenue_model: Mapped[Optional[str]] = mapped_column(String(256))
    deal_source: Mapped[Optional[str]] = mapped_column(Text)
    paid_in: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    post_money: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    last_fin_year: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    last_fin_month: Mapped[Optional[str]] = mapped_column(String(300))
    debt_total: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    headcount: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    cash_on_hand: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    next_fin_month: Mapped[Optional[str]] = mapped_column(String(300))
    next_fin_year: Mapped[Optional[str]] = mapped_column(String(300))
    round_size: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    time_to_exit: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    exit_multiple: Mapped[Optional[str]] = mapped_column(String(300))
    executive_seaches: Mapped[Optional[str]] = mapped_column(String(500))
    partner_comments: Mapped[Optional[str]] = mapped_column(Text)
    additional_comments: Mapped[Optional[str]] = mapped_column(Text)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)
    bod: Mapped[Optional[str]] = mapped_column(Text)
    investors: Mapped[Optional[str]] = mapped_column(Text)
    get_real_1: Mapped[Optional[str]] = mapped_column(String(4000))
    get_real_2: Mapped[Optional[str]] = mapped_column(String(4000))
    get_real_3: Mapped[Optional[str]] = mapped_column(String(4000))
    get_real_4: Mapped[Optional[str]] = mapped_column(String(4000))
    include: Mapped[Optional[str]] = mapped_column(String(4000))
    reporting_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 0))
    year_of_year_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 0))
    year: Mapped[Optional[str]] = mapped_column(String(50))
    debt_total_: Mapped[Optional[float]] = mapped_column(Numeric(20, 1))
    headcount_: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    cash_on_hand_: Mapped[Optional[float]] = mapped_column(Numeric(20, 1))
    revenue_model_: Mapped[Optional[str]] = mapped_column(String(100))
    deal_source_: Mapped[Optional[str]] = mapped_column(String(100))
    time_to_exit_: Mapped[Optional[float]] = mapped_column(Numeric(20, 1))
    expected_multiple: Mapped[Optional[str]] = mapped_column(String(300))
    fye_: Mapped[Optional[str]] = mapped_column(String(300))
    max_date: Mapped[Optional[date]] = mapped_column(Date)
    bookings_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    revenue_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    gpm_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pbt_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    pat_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    free_cash_flow_year: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    load_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    working_capital_change_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    working_capital_change_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ending_arr_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    is_saas_company: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    audited_ebitda: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    audited_revenue: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    reviewer_2: Mapped[Optional[str]] = mapped_column(String(100))
    financial_year_end_date: Mapped[Optional[date]] = mapped_column(Date)
    working_capital_file: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    group_structure_file: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    esop_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    esop_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    capex_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    depcr_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    int_cost_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    one_off_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_noncash_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    comment_for_ml: Mapped[Optional[str]] = mapped_column(Text)
    clarification_paid_in: Mapped[Optional[str]] = mapped_column(Text)
    clarification_post_money: Mapped[Optional[str]] = mapped_column(Text)
    audited_pbt: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_yr_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_yr_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_yr_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_q_1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_q_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_q_3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    ebitda_accepted_q_4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    total_ownership: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    acquisition_cost_yr1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_yr2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_yr3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_q1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_q2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_q3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    acquisition_cost_q4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_yr1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_yr2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_yr3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_q1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_q2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_q3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    other_income_q4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_currency_1: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_2: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_3: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_4: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_5: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_6: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_7: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_8: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_9: Mapped[Optional[str]] = mapped_column(String(100))
    billing_currency_10: Mapped[Optional[str]] = mapped_column(String(100))
    billing_revenue_perc1: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc2: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc3: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc4: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc5: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc6: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc7: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc8: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc9: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    billing_revenue_perc10: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    geo_l1: Mapped[Optional[str]] = mapped_column(String(1000))
    geo_l2: Mapped[Optional[str]] = mapped_column(String(1000))
    sector_l1: Mapped[Optional[str]] = mapped_column(String(1000))
    sector_l2: Mapped[Optional[str]] = mapped_column(String(1000))
    fmv_componding_expectation: Mapped[Optional[str]] = mapped_column(Text)
    time_involvement: Mapped[Optional[str]] = mapped_column(Text)
    help_needed: Mapped[Optional[str]] = mapped_column(Text)
    founders_or_company_employees: Mapped[Optional[str]] = mapped_column(Text)
    investor_nominee_on_board: Mapped[Optional[str]] = mapped_column(Text)
    independent_directors_on_board: Mapped[Optional[str]] = mapped_column(Text)
    nominee_on_board_internal_record: Mapped[Optional[str]] = mapped_column(Text)
    audit_completed: Mapped[Optional[str]] = mapped_column(Text)
    tentative_audit_completion_date: Mapped[Optional[date]] = mapped_column(Date)
    cash_runway: Mapped[Optional[float]] = mapped_column(Numeric(20, 3))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PortfolioReviewGroupsUsersEfront(Base):
    """Portfolio Review Groups Users — sourced from efront."""

    __tablename__ = "portfolio_review_groups_users_efront"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    group_name: Mapped[Optional[str]] = mapped_column(String(500))
    user_identifier: Mapped[Optional[str]] = mapped_column(String(500))
    first_name: Mapped[Optional[str]] = mapped_column(String(500))
    last_name: Mapped[Optional[str]] = mapped_column(String(500))
    email: Mapped[Optional[str]] = mapped_column(String(500))
    locked: Mapped[Optional[bool]] = mapped_column(Boolean)
    load_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PortfolioReviewGroupsReviewerEfront(Base):
    """Portfolio Review Groups Reviewer — sourced from efront."""

    __tablename__ = "portfolio_review_groups_reviewer_efront"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    entity: Mapped[Optional[str]] = mapped_column(String(500))
    submitter: Mapped[Optional[str]] = mapped_column(String(500))
    supervisor: Mapped[Optional[str]] = mapped_column(String(500))
    reviewer: Mapped[Optional[str]] = mapped_column(String(500))
    reviewer_2: Mapped[Optional[str]] = mapped_column(String(500))
    acceptor: Mapped[Optional[str]] = mapped_column(String(500))
    administrator: Mapped[Optional[str]] = mapped_column(String(500))
    entity_type: Mapped[Optional[str]] = mapped_column(String(100))
    load_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    category: Mapped[Optional[str]] = mapped_column(String(100))


class PortfolioReviewGroupsContactsEfront(Base):
    """Portfolio Review Groups Contacts — sourced from efront."""

    __tablename__ = "portfolio_review_groups_contacts_efront"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    group_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(String(500))
    last_name: Mapped[Optional[str]] = mapped_column(String(500))
    email: Mapped[Optional[str]] = mapped_column(String(500))
    contact_type: Mapped[Optional[str]] = mapped_column(String(100))


class Currency(Base):
    """Company currency lookup — sourced from efront."""

    __tablename__ = "currency"
    __table_args__ = {"schema": APP_SCHEMA}

    cid: Mapped[float] = mapped_column(Numeric(38, 0), primary_key=True, index=True)
    currency: Mapped[Optional[str]] = mapped_column(String(256))


class OwnershipRecords(Base):
    """Ownership records tracking company ownership by funds."""

    __tablename__ = "ownership_records"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str] = mapped_column(String, nullable=False)
    ownership_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    cid: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class CompanyDataRaw(Base):
    """Raw company master data sourced from Snowflake company master."""

    __tablename__ = "company_data_raw"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[float] = mapped_column(Numeric(38, 0), primary_key=True, index=True)
    cid: Mapped[Optional[float]] = mapped_column(Numeric(38, 0))
    name: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    short_description: Mapped[Optional[str]] = mapped_column(Text)
    is_venture: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_seed: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_growth: Mapped[Optional[bool]] = mapped_column(Boolean)
    display_name: Mapped[Optional[str]] = mapped_column(String(400))


"""
Note:
- Engine/session creation is handled by `src.db.session` (async engine).
- This module only defines ORM models.
"""
