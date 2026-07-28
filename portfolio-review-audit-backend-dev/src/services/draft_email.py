from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from html import escape
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    DraftEmail,
    EmailTemplate,
    Entity,
    FinancialMetricReconciliation,
    ManualReconciliationQuery,
    PortfolioCompany,
)
from src.services.financial_reconciliation import (
    fetch_canonical_snowflake_map,
    fetch_usd_to_inr_rate,
    load_variance_threshold_maps,
    reconciliation_row_qualifies_email_html,
    resolve_mis_for_recon_row,
)
from src.schema.draft_email import DraftEmailRead
from src.services.company_audit_recorder import CompanyAuditRecorder, FINANCIAL_METRIC_LABELS
from src.services.discrepancy_send_post_process import is_discrepancy_template_id


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _fy_year_from_text(s: str) -> str:
    # Try to pull YYYY out of strings like "Q4 2024", "FY 2025", etc.
    m = re.search(r"\b(20\d{2})\b", s or "")
    return m.group(1) if m else ""


def _render(text: str, *, variables: dict[str, str]) -> str:
    src = text or ""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(variables.get(key, match.group(0)))

    return _PLACEHOLDER_RE.sub(repl, src)


# When 2+ entities are queried in one email, the company name and FY differ per
# entity and are filled in manually — so they are removed from the subject. These
# match the {{fy_year}} / {{company_name}} placeholders together with an adjacent
# "FY" label and any leading/trailing separators or brackets, so nothing is left
# dangling. Separators between *kept* words are not touched.
_SUBJECT_FY_TOKEN_RE = re.compile(
    r"[\s\-–—|:,(]*(?:f\.?y\.?[\s\-:.]*)?\{\{\s*fy_year\s*\}\}[\s)]*",
    re.IGNORECASE,
)
_SUBJECT_COMPANY_TOKEN_RE = re.compile(
    r"[\s\-–—|:,(]*\{\{\s*company_name\s*\}\}[\s)]*",
    re.IGNORECASE,
)


def _strip_company_and_fy_from_subject(subject: str) -> str:
    """Remove the company-name and FY placeholders from a subject template, tidying
    any separators/empty brackets left behind. Operates on the raw template (before
    variable substitution) so it targets the tokens precisely, not rendered values."""
    s = subject or ""
    s = _SUBJECT_FY_TOKEN_RE.sub(" ", s)
    s = _SUBJECT_COMPANY_TOKEN_RE.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)  # drop brackets emptied by the removal
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = s.strip(" -–—|:,")  # trim any separator left at the very start/end
    return re.sub(r"\s{2,}", " ", s).strip()


_TABLE_STYLE = (
    'border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:13px;'
)
_TH_STYLE = (
    'border:1px solid #d1d5db;padding:6px 10px;background:#2563eb;color:#fff;'
    'text-align:left;white-space:nowrap;'
)
_TD_STYLE = 'border:1px solid #d1d5db;padding:5px 10px;vertical-align:top;'
_TD_NUM = 'border:1px solid #d1d5db;padding:5px 10px;text-align:right;vertical-align:top;'
_TH_NUM = (
    'border:1px solid #d1d5db;padding:6px 10px;background:#2563eb;color:#fff;'
    'text-align:right;white-space:nowrap;'
)

_METRIC_ORDER = ("revenue", "ebitda", "pbt", "pat", "cash", "debt")
_INR_CR_DIVISOR = 10_000_000.0
_USD_MN_DIVISOR = 1_000_000.0


def _normalize_currency(currency: Optional[str]) -> str:
    return (currency or "").strip().upper()


def _denomination_header(currency: Optional[str]) -> str:
    c = _normalize_currency(currency)
    if c == "INR":
        return "INR CR"
    if c == "USD":
        return "USD Mn"
    return c or "Amount"


def _scale_amount(val: float, currency: str) -> float:
    if currency == "INR":
        return val / _INR_CR_DIVISOR
    if currency == "USD":
        return val / _USD_MN_DIVISOR
    return val


def _fmt_scaled_cell(val: Any, currency: Optional[str]) -> str:
    if val is None:
        return "—"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    scaled = _scale_amount(num, _normalize_currency(currency))
    rounded = round(scaled, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    text = f"{rounded:.1f}"
    if text.endswith(".0"):
        return text[:-2]
    return text


def _fmt_difference(afs: Any, mis: Any, currency: Optional[str]) -> str:
    if afs is None or mis is None:
        return "—"
    try:
        diff = float(afs) - float(mis)
    except (TypeError, ValueError):
        return "—"
    return _fmt_scaled_cell(diff, currency)


def _resolve_row_currency(row: FinancialMetricReconciliation, mis_currency: Optional[str] = None) -> str:
    return _normalize_currency(row.afs_currency or mis_currency)


def _build_financials_html(
    recon_rows: list[FinancialMetricReconciliation],
    manual_rows: list[ManualReconciliationQuery],
    *,
    entity_names: Optional[dict[int, str]] = None,
    mis_values: Optional[dict[int, tuple[Optional[float], Optional[str]]]] = None,
) -> str:
    """Build the {{financials}} HTML block: denomination table(s) + manual queries list.

    mis_values maps row.id → (mis_amount, mis_currency) resolved from FinancialDataSnowflake.
    """
    parts: list[str] = []
    names = entity_names or {}
    mv: dict[int, tuple[Optional[float], Optional[str]]] = mis_values or {}

    if recon_rows:
        by_entity: dict[int, list[FinancialMetricReconciliation]] = {}
        for row in recon_rows:
            by_entity.setdefault(int(row.entity_id), []).append(row)

        for entity_id in sorted(by_entity.keys()):
            entity_rows = by_entity[entity_id]
            first_mis_currency = mv.get(entity_rows[0].id, (None, None))[1]
            currency = _resolve_row_currency(entity_rows[0], first_mis_currency)
            denom = _denomination_header(currency)
            entity_label = names.get(entity_id) or f"Entity {entity_id}"

            ordered = sorted(
                entity_rows,
                key=lambda r: (
                    _METRIC_ORDER.index(r.metric_key)
                    if (r.metric_key or "") in _METRIC_ORDER
                    else len(_METRIC_ORDER)
                ),
            )

            header = (
                "<tr>"
                f"<th style='{_TH_STYLE}'>{escape(denom)}</th>"
                f"<th style='{_TH_NUM}'>AFS</th>"
                f"<th style='{_TH_NUM}'>MIS</th>"
                f"<th style='{_TH_NUM}'>Difference</th>"
                "</tr>"
            )
            rows_html: list[str] = []
            for r in ordered:
                mk = (r.metric_key or "").strip().lower()
                label = FINANCIAL_METRIC_LABELS.get(mk, mk.replace("_", " ").title())
                mis_amt, _ = mv.get(r.id, (None, None))
                row_cells = [
                    f"<td style='{_TD_STYLE}'><b>{escape(label)}</b></td>",
                    f"<td style='{_TD_NUM}'>{escape(_fmt_scaled_cell(r.afs_amount, currency))}</td>",
                    f"<td style='{_TD_NUM}'>{escape(_fmt_scaled_cell(mis_amt, currency))}</td>",
                    f"<td style='{_TD_NUM}'>{escape(_fmt_difference(r.afs_amount, mis_amt, currency))}</td>",
                ]
                rows_html.append("<tr>" + "".join(row_cells) + "</tr>")

            parts.append(
                f"<p style='margin:8px 0 4px 0;font-weight:bold;'>{escape(entity_label)}</p>"
                f"<table style='{_TABLE_STYLE}'>"
                f"<thead>{header}</thead>"
                f"<tbody>{''.join(rows_html)}</tbody>"
                "</table>"
            )
    else:
        parts.append(
            "<p style='font-style:italic;color:#6b7280;margin:4px 0;'>"
            "No financial discrepancies to report."
            "</p>"
        )

    if manual_rows:
        items_html = "\n".join(
            f'<li style="margin:4px 0;">{escape(mq.discrepency_text or "")}</li>'
            for mq in manual_rows
        )
        parts.append(
            "<p style='margin:12px 0 4px 0;font-weight:bold;'>Additional Queries</p>"
            '<ol style="list-style-type:decimal;padding-left:20px;margin:4px 0;">'
            f"{items_html}"
            "</ol>"
        )

    return "\n".join(parts)


class DraftEmailService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def draft_to_read(self, row: DraftEmail, *, template_name: Optional[str] = None) -> DraftEmailRead:
        name = template_name
        if name is None and row.template_id:
            r = await self.db.execute(select(EmailTemplate).where(EmailTemplate.id == row.template_id))
            tpl = r.scalar_one_or_none()
            if tpl:
                name = tpl.template_name
        return DraftEmailRead(
            id=row.id,
            portfolio_company_id=row.portfolio_company_id,
            subject=row.subject,
            to_add=row.to_add,
            cc=row.cc,
            email_body=row.email_body,
            template_id=row.template_id,
            template_name=name,
            attachments=row.attachments,
            attachments_id=row.attachments_id,
            affected_entity_ids=row.affected_entity_ids,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_by_id(self, *, draft_id: str) -> Optional[DraftEmail]:
        result = await self.db.execute(select(DraftEmail).where(DraftEmail.id == draft_id))
        return result.scalar_one_or_none()

    async def get_for_company(self, *, portfolio_company_id: int) -> Optional[DraftEmail]:
        result = await self.db.execute(
            select(DraftEmail).where(DraftEmail.portfolio_company_id == portfolio_company_id)
        )
        return result.scalar_one_or_none()

    async def generate_for_company(
        self,
        *,
        portfolio_company_id: int,
        template_name: Optional[str] = None,
        overwrite: bool = False,
    ) -> DraftEmailRead:
        company_result = await self.db.execute(
            select(PortfolioCompany).where(PortfolioCompany.id == portfolio_company_id)
        )
        company = company_result.scalar_one_or_none()
        if not company:
            raise ValueError("Company not found")

        existing = await self.get_for_company(portfolio_company_id=portfolio_company_id)
        if existing and not overwrite:
            return await self.draft_to_read(existing)

        # Pick an active template.
        # Default to the discrepancy template if not explicitly provided.
        template_name = template_name or "Discrepancy Template"
        if template_name:
            tpl_result = await self.db.execute(
                select(EmailTemplate).where(
                    EmailTemplate.template_name == template_name,
                    EmailTemplate.is_active.is_(True),
                )
            )
        tpl = tpl_result.scalars().first()
        if not tpl:
            raise ValueError("No active email template found")

        is_disc = await is_discrepancy_template_id(self.db, tpl.id)

        if is_disc:
            # Build financial reconciliation HTML table for {{financials}}.
            pct_by_metric, abs_by_metric, pct_by_label, abs_by_label = await load_variance_threshold_maps(self.db)
            usd_inr = await fetch_usd_to_inr_rate(self.db)

            recon_result = await self.db.execute(
                select(FinancialMetricReconciliation)
                .where(
                    FinancialMetricReconciliation.portfolio_company_id == portfolio_company_id,
                    FinancialMetricReconciliation.enable.is_(True),
                    FinancialMetricReconciliation.status == "Open",
                )
                .order_by(
                    FinancialMetricReconciliation.entity_id,
                    FinancialMetricReconciliation.review_cycle,
                    FinancialMetricReconciliation.metric_key,
                )
            )
            recon_rows = list(recon_result.scalars().all())
            sf_map = await fetch_canonical_snowflake_map(
                self.db,
                {
                    (r.portfolio_company_id, (r.review_cycle or "").strip())
                    for r in recon_rows
                    if (r.review_cycle or "").strip()
                },
            )
            qualifying: list[FinancialMetricReconciliation] = []
            qualifying_mis: dict[int, tuple[Optional[float], Optional[str]]] = {}
            for r in recon_rows:
                sf = sf_map.get((r.portfolio_company_id, (r.review_cycle or "").strip()))
                mis_amount, mis_currency = resolve_mis_for_recon_row(r, sf)
                if not reconciliation_row_qualifies_email_html(
                    r,
                    mis_amount=mis_amount,
                    mis_currency=mis_currency,
                    pct_by_metric=pct_by_metric,
                    abs_by_metric=abs_by_metric,
                    pct_by_label=pct_by_label,
                    abs_by_label=abs_by_label,
                    usd_to_inr_rate=usd_inr,
                ):
                    continue
                qualifying.append(r)
                qualifying_mis[r.id] = (mis_amount, mis_currency)

            manual_result = await self.db.execute(
                select(ManualReconciliationQuery)
                .where(
                    ManualReconciliationQuery.portfolio_company_id == portfolio_company_id,
                    ManualReconciliationQuery.enable.is_(True),
                )
                .order_by(ManualReconciliationQuery.id)
            )
            manual_rows = list(manual_result.scalars().all())

            entity_name_rows = (
                await self.db.execute(select(Entity).where(Entity.portfolio_company_id == portfolio_company_id))
            ).scalars().all()
            entity_names = {int(e.id): e.name for e in entity_name_rows}

            financials_html = _build_financials_html(qualifying, manual_rows, entity_names=entity_names, mis_values=qualifying_mis)

            # Derive affected entity IDs from the exact rows rendered into the email body.
            # This is the authoritative snapshot — send validation, EmailHistory, and post-send
            # entity stamping all read from this field rather than recomputing independently.
            affected_entity_ids = sorted({
                int(row.entity_id)
                for row in qualifying
                if row.entity_id is not None
            } | {
                int(row.entity_id)
                for row in manual_rows
                if row.entity_id is not None
            })
            if not affected_entity_ids:
                raise ValueError(
                    "Cannot generate draft: no entities with qualifying discrepancies found. "
                    "Ensure at least one enabled open discrepancy or manual query exists for this company."
                )
        else:
            financials_html = ""
            affected_entity_ids = []
            entity_name_rows = (
                await self.db.execute(select(Entity).where(Entity.portfolio_company_id == portfolio_company_id))
            ).scalars().all()

        # Use entity fy_end (e.g. "Dec-24") directly as the fy_year template variable.
        # If all affected entities share the same fy_end, use it; otherwise fall back
        # to company-level audit period (subject is stripped for multi-entity anyway).
        entity_fy_map = {int(e.id): e.fy_end for e in entity_name_rows}
        affected_fy_ends = {
            entity_fy_map[eid]
            for eid in affected_entity_ids
            if entity_fy_map.get(eid)
        }
        fy_year = affected_fy_ends.pop() if len(affected_fy_ends) == 1 else ""

        variables = {
            "company_name": company.name,
            "poc_name": company.contact_name or "",
            "fy_year": fy_year,
            "financials": financials_html,
        }

        now = datetime.now(timezone.utc)
        # Multi-entity drafts: company name + FY differ per entity and are edited
        # manually, so strip them from the subject (body still lists each entity).
        subject_template = tpl.subject or ""
        if len(affected_entity_ids) >= 2:
            subject_template = _strip_company_and_fy_from_subject(subject_template)
        rendered_subject = _render(subject_template, variables=variables)
        rendered_body = _render(tpl.body or "", variables=variables)
        to_add = [company.contact_email_id] if getattr(company, "contact_email_id", None) else []

        if existing:
            existing.template_id = tpl.id
            existing.subject = rendered_subject
            existing.email_body = rendered_body
            existing.to_add = to_add
            existing.cc = []
            existing.affected_entity_ids = affected_entity_ids
            # Regenerate wipes attachments (matches "overwrite from template" expectation).
            existing.attachments = []
            existing.attachments_id = ""
            existing.updated_at = now
            self.db.add(existing)
            await CompanyAuditRecorder(self.db).log_company(
                portfolio_company_id=portfolio_company_id,
                action=f'Query email draft regenerated using template "{tpl.template_name}"',
                meta={
                    "event": "email.draft_generated",
                    "draft_id": existing.id,
                    "template_name": tpl.template_name,
                    "affected_entity_ids": affected_entity_ids,
                },
            )
            await self.db.commit()
            await self.db.refresh(existing)
            return await self.draft_to_read(existing, template_name=tpl.template_name)

        row = DraftEmail(
            id=str(uuid.uuid4()),
            portfolio_company_id=portfolio_company_id,
            template_id=tpl.id,
            subject=rendered_subject,
            email_body=rendered_body,
            to_add=to_add,
            cc=[],
            attachments=[],
            attachments_id="",
            affected_entity_ids=affected_entity_ids,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        await CompanyAuditRecorder(self.db).log_company(
            portfolio_company_id=portfolio_company_id,
            action=f'Query email draft created using template "{tpl.template_name}"',
            meta={
                "event": "email.draft_generated",
                "draft_id": row.id,
                "template_name": tpl.template_name,
                "affected_entity_ids": affected_entity_ids,
            },
        )
        await self.db.commit()
        await self.db.refresh(row)
        return await self.draft_to_read(row, template_name=tpl.template_name)

    async def update_draft(
        self,
        *,
        draft_id: str,
        subject: Optional[str],
        to_add: Optional[list[str]],
        cc: Optional[list[str]],
        email_body: Optional[str],
        attachments: Optional[list[str]],
        attachments_id: Optional[str],
    ) -> DraftEmailRead:
        result = await self.db.execute(select(DraftEmail).where(DraftEmail.id == draft_id))
        row = result.scalar_one_or_none()
        if not row:
            raise ValueError("Draft not found")

        if subject is not None:
            row.subject = subject
        if to_add is not None:
            row.to_add = to_add
        if cc is not None:
            row.cc = cc
        if email_body is not None:
            row.email_body = email_body
        if attachments is not None:
            row.attachments = attachments
        if attachments_id is not None:
            row.attachments_id = attachments_id

        row.updated_at = datetime.now(timezone.utc)
        self.db.add(row)
        if row.portfolio_company_id:
            await CompanyAuditRecorder(self.db).log_company(
                portfolio_company_id=int(row.portfolio_company_id),
                action=f'Query email draft updated (draft id {draft_id})',
                meta={"event": "email.draft_updated", "draft_id": draft_id},
            )
        await self.db.commit()
        await self.db.refresh(row)
        return await self.draft_to_read(row)

