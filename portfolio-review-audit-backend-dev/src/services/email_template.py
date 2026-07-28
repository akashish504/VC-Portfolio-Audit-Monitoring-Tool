from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import EmailTemplate, TemplateEmailHistory

logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_MAX_TEMPLATE_BODY_LEN = 200_000


def _find_raw_placeholder_tokens(body: str) -> list[str]:
    text = body or ""
    if len(text) > _MAX_TEMPLATE_BODY_LEN:
        raise ValueError(
            f"Template body exceeds maximum length ({_MAX_TEMPLATE_BODY_LEN} characters)"
        )
    tokens: list[str] = []
    idx = 0
    while True:
        start = text.find("{{", idx)
        if start < 0:
            break
        end = text.find("}}", start + 2)
        if end < 0:
            raise ValueError("Unclosed placeholder '{{' in template body")
        tokens.append(text[start + 2 : end])
        idx = end + 2
    return tokens


def _is_valid_placeholder_token(token: str) -> bool:
    inner = (token or "").strip()
    return bool(inner) and len(inner) <= 128 and inner.isidentifier()


def _invalid_placeholder_tokens(body: str) -> list[str]:
    return [tok for tok in _find_raw_placeholder_tokens(body) if not _is_valid_placeholder_token(tok)]


class EmailTemplateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all_versions(self) -> list[EmailTemplate]:
        result = await self.db.execute(select(EmailTemplate).order_by(EmailTemplate.created_at.desc()))
        return list(result.scalars().all())

    async def list_active(self) -> list[EmailTemplate]:
        result = await self.db.execute(
            select(EmailTemplate)
            .where(EmailTemplate.is_active.is_(True))
            .order_by(EmailTemplate.template_name.asc(), EmailTemplate.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active(self, template_name: str) -> Optional[EmailTemplate]:
        result = await self.db.execute(
            select(EmailTemplate).where(
                EmailTemplate.template_name == template_name,
                EmailTemplate.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, *, template_name: str, subject: str, body: str, version_name: Optional[str]) -> EmailTemplate:
        # Validate placeholders are well-formed (copying the other app’s safety).
        found = set(m.group(1) for m in _PLACEHOLDER_RE.finditer(body or ""))
        invalid = _invalid_placeholder_tokens(body or "")
        if invalid:
            invalid_render = ", ".join([f"{{{{{t}}}}}" for t in invalid])
            raise ValueError(f"Invalid placeholders: {invalid_render}")

        # Deactivate existing active versions for the same template_name.
        await self.db.execute(
            update(EmailTemplate)
            .where(EmailTemplate.template_name == template_name, EmailTemplate.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )

        now = datetime.now(timezone.utc)
        row = EmailTemplate(
            id=str(uuid.uuid4()),
            template_name=template_name,
            subject=subject,
            body=body,
            version_id=str(uuid.uuid4()),
            version_name=version_name or now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.add(
            TemplateEmailHistory(
                template_id=row.id,
                event="template_created",
                meta={
                    "template_name": template_name,
                    "version_id": row.version_id,
                    "version_name": row.version_name,
                },
            )
        )
        await self.db.commit()
        await self.db.refresh(row)
        logger.info("Created email template template_name=%s version_id=%s", template_name, row.version_id)
        return row

    async def update_version(self, *, template_id: str, subject: str, body: str, version_name: Optional[str]) -> EmailTemplate:
        # Validate placeholders are well-formed.
        invalid = _invalid_placeholder_tokens(body or "")
        if invalid:
            invalid_render = ", ".join([f"{{{{{t}}}}}" for t in invalid])
            raise ValueError(f"Invalid placeholders: {invalid_render}")

        result = await self.db.execute(select(EmailTemplate).where(EmailTemplate.id == template_id))
        row = result.scalar_one_or_none()
        if not row:
            raise ValueError("Template not found")

        row.subject = subject
        row.body = body
        if version_name is not None:
            row.version_name = version_name
        row.updated_at = datetime.now(timezone.utc)
        self.db.add(row)
        self.db.add(
            TemplateEmailHistory(
                template_id=row.id,
                event="template_updated",
                meta={
                    "template_name": row.template_name,
                    "version_id": row.version_id,
                    "version_name": row.version_name,
                },
            )
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def activate(self, *, template_name: str, version_id: str) -> None:
        # Deactivate current active
        await self.db.execute(
            update(EmailTemplate)
            .where(EmailTemplate.template_name == template_name, EmailTemplate.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )

        # Activate requested
        result = await self.db.execute(
            select(EmailTemplate).where(EmailTemplate.template_name == template_name, EmailTemplate.version_id == version_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise ValueError("Template version not found")

        row.is_active = True
        row.updated_at = datetime.now(timezone.utc)
        self.db.add(row)
        self.db.add(
            TemplateEmailHistory(
                template_id=row.id,
                event="template_activated",
                meta={
                    "template_name": template_name,
                    "version_id": row.version_id,
                    "version_name": row.version_name,
                },
            )
        )
        await self.db.commit()

    async def list_history_for_template(self, *, template_id: str) -> list[TemplateEmailHistory]:
        result = await self.db.execute(
            select(TemplateEmailHistory)
            .where(TemplateEmailHistory.template_id == template_id)
            .order_by(TemplateEmailHistory.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_history_for_template(self, *, template_id: str) -> list[TemplateEmailHistory]:
        result = await self.db.execute(
            select(TemplateEmailHistory)
            .where(TemplateEmailHistory.template_id == template_id)
            .order_by(TemplateEmailHistory.created_at.desc())
        )
        return list(result.scalars().all())

