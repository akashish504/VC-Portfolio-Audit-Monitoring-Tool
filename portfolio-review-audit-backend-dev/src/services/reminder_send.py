"""Reminder 1 / Reminder 2 send logic.

Reminders are sent in the same thread as the original discrepancy email.
If multiple eligible discrepancy threads exist for a company the caller must
supply ``thread_id``; otherwise a ``ThreadSelectionRequired`` exception is
raised with candidate thread metadata so the frontend can prompt the user.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import EmailHistory, Entity
from src.schema.portfolio import CompanyReviewStage
from src.services.company_audit_context import get_audit_actor_email
from src.services.company_audit_recorder import CompanyAuditRecorder
from src.services.email_template_config import (
    ConfigurationError,
    get_reminder_1_template_id,
    get_reminder_2_template_id,
    get_discrepancy_template_id,
)
from src.services.email_threads import EmailThreadsService

logger = logging.getLogger(__name__)


@dataclass
class ThreadCandidate:
    thread_id: str
    subject: Optional[str]
    latest_sent_at: Optional[datetime]
    affected_entity_ids: list[int]


class ThreadSelectionRequired(Exception):
    """Raised when multiple discrepancy threads exist and the caller must choose."""

    def __init__(self, candidates: list[ThreadCandidate]) -> None:
        self.candidates = candidates
        super().__init__(
            f"Multiple discrepancy threads found ({len(candidates)}). "
            "Please select a thread_id and retry."
        )


class DuplicateReminderError(ValueError):
    """Raised when Reminder 1 or 2 has already been sent for the same entities/thread."""


async def _find_discrepancy_threads(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    discrepancy_template_id: str,
) -> list[EmailHistory]:
    """Return all outbound EmailHistory rows for the discrepancy template."""
    return list(
        (
            await db.execute(
                select(EmailHistory).where(
                    EmailHistory.portfolio_company_id == portfolio_company_id,
                    EmailHistory.template_id == discrepancy_template_id,
                    EmailHistory.is_inbound.is_(False),
                )
            )
        ).scalars().all()
    )


def _group_by_thread(rows: list[EmailHistory]) -> dict[str, EmailHistory]:
    """Return the most-recent outbound discrepancy email per thread."""
    threads: dict[str, EmailHistory] = {}
    for row in rows:
        tid = row.thread_id or ""
        if not tid:
            continue
        if tid not in threads:
            threads[tid] = row
        else:
            existing = threads[tid]
            if row.sent_at and (existing.sent_at is None or row.sent_at > existing.sent_at):
                threads[tid] = row
    return threads


async def _check_duplicate_reminder(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    reminder_template_id: str,
    thread_id: str,
    affected_entity_ids: list[int],
    reminder_label: str,
) -> None:
    existing = list(
        (
            await db.execute(
                select(EmailHistory).where(
                    EmailHistory.portfolio_company_id == portfolio_company_id,
                    EmailHistory.template_id == reminder_template_id,
                    EmailHistory.thread_id == thread_id,
                    EmailHistory.is_inbound.is_(False),
                    EmailHistory.affected_entity_ids.is_not(None),
                )
            )
        ).scalars().all()
    )

    already_sent: set[int] = set()
    for rec in existing:
        if rec.affected_entity_ids:
            already_sent.update(int(e) for e in rec.affected_entity_ids)

    overlap = set(affected_entity_ids) & already_sent
    if overlap:
        entities = list(
            (
                await db.execute(
                    select(Entity.id, Entity.name).where(Entity.id.in_(overlap))
                )
            ).all()
        )
        id_to_name = {row.id: row.name for row in entities}
        overlap_display = ", ".join(
            f"{eid} ({id_to_name.get(eid, 'unknown')})" for eid in sorted(overlap)
        )
        raise DuplicateReminderError(
            f"{reminder_label} already sent for entities: {overlap_display} in thread {thread_id}."
        )


async def send_reminder(
    db: AsyncSession,
    *,
    portfolio_company_id: int,
    reminder_number: int,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    body_html: str,
    attachment_keys: list[str],
    attachments_id: Optional[str],
    thread_id: Optional[str] = None,
) -> dict:
    """Send Reminder 1 or Reminder 2 email.

    Args:
        reminder_number: 1 or 2.
        thread_id: If provided, send into that specific thread. If None and only
            one discrepancy thread exists, it is used automatically.  If None and
            multiple threads exist, raises ThreadSelectionRequired.

    Returns:
        dict with keys: thread_id, message_id, affected_entity_ids
    """
    if reminder_number not in (1, 2):
        raise ValueError("reminder_number must be 1 or 2")

    disc_template_id = await get_discrepancy_template_id(db)

    if reminder_number == 1:
        reminder_template_id = await get_reminder_1_template_id(db)
        reminder_label = "Reminder 1"
        stamp_field = "reminder_1_sent_at"
        reminder_status = CompanyReviewStage.QUERY_RESPONSE_REMINDER_1.value
    else:
        reminder_template_id = await get_reminder_2_template_id(db)
        reminder_label = "Reminder 2"
        stamp_field = "reminder_2_sent_at"
        reminder_status = CompanyReviewStage.QUERY_RESPONSE_REMINDER_2.value

    disc_rows = await _find_discrepancy_threads(
        db,
        portfolio_company_id=portfolio_company_id,
        discrepancy_template_id=disc_template_id,
    )
    if not disc_rows:
        raise ValueError(
            f"No discrepancy email found for company {portfolio_company_id}. "
            "Send the discrepancy email first before sending a reminder."
        )

    thread_map = _group_by_thread(disc_rows)

    if thread_id:
        if thread_id not in thread_map:
            raise ValueError(
                f"thread_id {thread_id!r} does not correspond to any known discrepancy thread "
                f"for company {portfolio_company_id}."
            )
        selected_disc_email = thread_map[thread_id]
    elif len(thread_map) == 1:
        thread_id, selected_disc_email = next(iter(thread_map.items()))
    else:
        # Multiple threads — ask the user to choose
        candidates: list[ThreadCandidate] = []
        for tid, row in sorted(
            thread_map.items(),
            key=lambda kv: kv[1].sent_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        ):
            candidates.append(
                ThreadCandidate(
                    thread_id=tid,
                    subject=row.subject,
                    latest_sent_at=row.sent_at,
                    affected_entity_ids=list(row.affected_entity_ids or []),
                )
            )
        raise ThreadSelectionRequired(candidates)

    affected_entity_ids = list(selected_disc_email.affected_entity_ids or [])

    await _check_duplicate_reminder(
        db,
        portfolio_company_id=portfolio_company_id,
        reminder_template_id=reminder_template_id,
        thread_id=thread_id,
        affected_entity_ids=affected_entity_ids,
        reminder_label=reminder_label,
    )

    svc = EmailThreadsService(db)
    sent_thread_id, message_id = await svc.send_email(
        portfolio_company_id=portfolio_company_id,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        subject=subject,
        body_html=body_html,
        thread_id=thread_id,
        reply_to_message_id=None,
        attachment_keys=attachment_keys,
        attachments_id=attachments_id,
        template_id=reminder_template_id,
        affected_entity_ids=affected_entity_ids,
    )

    # Stamp entity lifecycle timestamps
    now = datetime.now(timezone.utc)
    actor = get_audit_actor_email()
    recorder = CompanyAuditRecorder(db)
    if affected_entity_ids:
        entities = list(
            (
                await db.execute(
                    select(Entity).where(Entity.id.in_(affected_entity_ids))
                )
            ).scalars().all()
        )
        for ent in entities:
            current_val = getattr(ent, stamp_field)
            if current_val is None:
                setattr(ent, stamp_field, now)
                db.add(ent)
                await recorder.log_company_field_changes(
                    portfolio_company_id=portfolio_company_id,
                    changes={stamp_field: (None, now)},
                    entity_type="entity",
                    entity_id=ent.id,
                    actor_email=actor,
                )
            # Shift the affected entity's status to the reminder stage. State lives on
            # the entity now; only the reminded (affected) entities move.
            before_status = ent.status
            if before_status != reminder_status:
                ent.status = reminder_status
                db.add(ent)
                await recorder.log_company_field_changes(
                    portfolio_company_id=portfolio_company_id,
                    changes={"status": (before_status, reminder_status)},
                    entity_type="entity",
                    entity_id=ent.id,
                    actor_email=actor,
                )

    await db.commit()

    return {
        "thread_id": sent_thread_id,
        "message_id": message_id,
        "affected_entity_ids": affected_entity_ids,
    }
