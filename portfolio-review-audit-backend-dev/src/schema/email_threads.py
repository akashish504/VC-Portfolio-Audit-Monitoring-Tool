from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class EmailMessageRead(BaseModel):
    id: str
    thread_id: Optional[str] = None
    portfolio_company_id: Optional[int] = None
    sender: Optional[str] = None
    recipients: Optional[list[str]] = None
    cc: Optional[list[str]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachments: Optional[list[str]] = None
    sent_at: Optional[datetime] = None
    is_inbound: Optional[bool] = None

    class Config:
        from_attributes = True


class EmailThreadRead(BaseModel):
    thread_id: str
    latest_sent_at: Optional[datetime] = None
    subject: Optional[str] = None
    email_count: int
    emails: list[EmailMessageRead]


class EmailThreadSummary(BaseModel):
    """Lightweight thread card — no email bodies."""
    thread_id: str
    subject: Optional[str] = None
    latest_sent_at: Optional[datetime] = None
    email_count: int
    sender: Optional[str] = None


class CompanyContactsRead(BaseModel):
    poc_email_ids: list[str] = Field(default_factory=list)
    poc_cc_email_ids: list[str] = Field(default_factory=list)


class SuggestedEmailsRead(BaseModel):
    emails: list[str] = Field(default_factory=list)


class SuggestedEmailsWrite(BaseModel):
    emails: list[str] = Field(default_factory=list)


class SendEmailRequest(BaseModel):
    portfolio_company_id: int = Field(..., ge=1)
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    subject: str = Field(..., min_length=1)
    body_html: str = Field(..., min_length=1)
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    attachment_keys: list[str] = Field(default_factory=list)
    attachments_id: Optional[str] = None


class SendEmailResponse(BaseModel):
    success: bool
    thread_id: str
    message_id: str


class ReminderSendRequest(BaseModel):
    portfolio_company_id: int = Field(..., ge=1)
    reminder_number: int = Field(..., ge=1, le=2)
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    subject: str = Field(..., min_length=1)
    body_html: str = Field(..., min_length=1)
    thread_id: Optional[str] = None
    attachment_keys: list[str] = Field(default_factory=list)
    attachments_id: Optional[str] = None


class ThreadCandidateRead(BaseModel):
    thread_id: str
    subject: Optional[str] = None
    latest_sent_at: Optional[datetime] = None
    affected_entity_ids: list[int] = Field(default_factory=list)


class ReminderThreadSelectionRequired(BaseModel):
    selection_required: bool = True
    candidates: list[ThreadCandidateRead]


class ReminderSendResponse(BaseModel):
    success: bool
    thread_id: str
    message_id: str
    affected_entity_ids: list[int] = Field(default_factory=list)


class TagThreadRequest(BaseModel):
    portfolio_company_id: int = Field(..., ge=1)
    thread_id: Optional[str] = None
    message_id: Optional[str] = None


class TagThreadResponse(BaseModel):
    success: bool
    thread_id: Optional[str] = None
    portfolio_company_id: int
    affected_rows: int
    message: Optional[str] = None


class TagWithEntityRequest(BaseModel):
    """Attach an audited-financials email to a company + optional entity and trigger extraction."""
    email_id: str
    portfolio_company_id: int = Field(..., ge=1)
    entity_id: Optional[int] = None
    review_cycle_id: Optional[str] = None
    force_reprocess: bool = False


class AttachmentIngestResult(BaseModel):
    s3_key: str
    filename: str
    status: str  # "success" | "skipped" | "failed"
    file_id: Optional[int] = None
    error: Optional[str] = None
    skip_reason: Optional[str] = None


class TagWithEntityResponse(BaseModel):
    success: bool
    email_id: str
    portfolio_company_id: int
    entity_id: Optional[int] = None
    review_cycle_id: Optional[str] = None
    attachments_processed: int
    attachment_results: list[AttachmentIngestResult] = Field(default_factory=list)
    message: Optional[str] = None


class AuditedFinancialsFileRead(BaseModel):
    id: int
    filename: Optional[str] = None
    status: Optional[str] = None
    content_type: Optional[str] = None
    created_at: Optional[datetime] = None
    source_attachment_key: Optional[str] = None

    class Config:
        from_attributes = True


class AttachmentDiagnostic(BaseModel):
    """Per-attachment storage state, so the UI can show whether the PDF is fetchable."""
    key: str
    filename: Optional[str] = None
    in_app_storage: bool = False
    issue: Optional[str] = None


class EmailDiagnostics(BaseModel):
    """Read-only explanation of why an audited-financials email does / doesn't have a file.

    Computed from data already on the row (no server logs needed). ``status`` is a coarse
    bucket for colour-coding; ``reason_code`` is stable for logic; ``message`` is human-readable.
    """
    status: str          # processed | pending | blocked | file_failed | no_attachments | unknown
    reason_code: str     # file_created | pending_extraction | attachment_not_in_storage |
    #                      subject_unparseable | extraction_failed | no_attachments | unknown
    message: str
    file_count: int = 0
    attachments: list[AttachmentDiagnostic] = Field(default_factory=list)


class AttachmentStorageProbe(BaseModel):
    """Result of a LIVE S3 read attempt for one attachment — AWS's verbatim answer."""
    key: str
    bucket: str
    accessible: bool
    size_bytes: Optional[int] = None
    aws_error_code: Optional[str] = None     # e.g. 'AccessDenied', 'NoSuchKey', '403'
    aws_message: Optional[str] = None
    verdict: str


class StorageCheckResponse(BaseModel):
    """Definitive, live storage check for an email's attachments (no inference)."""
    email_id: str
    bucket: str
    overall: str          # 'all_accessible' | 'access_denied' | 'missing' | 'mixed' | 'no_attachments'
    summary: str
    probes: list[AttachmentStorageProbe] = Field(default_factory=list)


class AuditedFinancialsEmailRead(BaseModel):
    id: str
    thread_id: Optional[str] = None
    portfolio_company_id: Optional[int] = None
    portfolio_company_name: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    email_type: Optional[str] = None
    sent_at: Optional[datetime] = None
    is_inbound: Optional[bool] = None
    attachments: Optional[list[str]] = None
    classified_by: str = "system"
    files: list[AuditedFinancialsFileRead] = Field(default_factory=list)
    diagnostics: Optional[EmailDiagnostics] = None

