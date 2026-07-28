from __future__ import annotations

import re
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.configs.env import settings
from src.utils.s3 import generate_presigned_url, upload_file

router = APIRouter()


_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9.\-_]+")


def _safe_filename(name: str) -> str:
    name = (name or "").strip() or "attachment"
    name = name.replace(" ", "_")
    name = _FILENAME_SAFE_RE.sub("_", name)
    return name[:180]  # keep key sizes sane


def _s3_url_for_key(key: str) -> str:
    # Same shape as the legacy app: direct S3 URL.
    # (Frontend will typically use presign, but we keep this for debugging/UI.)
    bucket = settings.AWS_S3_EMAIL_BUCKET
    region = settings.AWS_REGION
    if bucket and region:
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    return key


@router.post("/email/attachments/upload")
async def upload_email_attachments(
    attachments_id: Optional[str] = Form(None),
    attachments: list[UploadFile] = File(...),
):
    if not settings.AWS_S3_EMAIL_BUCKET:
        raise HTTPException(status_code=400, detail="AWS_S3_EMAIL_BUCKET is not configured")
    if not settings.AWS_REGION:
        raise HTTPException(status_code=400, detail="AWS_REGION is not configured")
    if not settings.AWS_S3_EMAIL_ATTACH_PATH:
        raise HTTPException(status_code=400, detail="AWS_S3_EMAIL_ATTACH_PATH is not configured")

    if not attachments:
        raise HTTPException(status_code=400, detail="No attachments provided")
    if len(attachments) > settings.MAX_ATTACHMENT_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Max {settings.MAX_ATTACHMENT_FILES}.",
        )

    folder_id = attachments_id or str(uuid.uuid4())
    keys: list[str] = []
    urls: list[str] = []

    max_bytes = settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
    for f in attachments:
        filename = _safe_filename(f.filename or "attachment")
        content_type = f.content_type or "binary/octet-stream"
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f'File "{filename}" exceeds max size {settings.MAX_ATTACHMENT_SIZE_MB}MB',
            )

        key = f"{settings.AWS_S3_EMAIL_ATTACH_PATH}/{folder_id}/{filename}"
        upload_file(
            data,
            key,
            content_type=content_type,
            bucket=settings.AWS_S3_EMAIL_BUCKET,
            bucket_env_var_name="AWS_S3_EMAIL_BUCKET",
        )
        keys.append(key)
        urls.append(_s3_url_for_key(key))

    return {"attachments_id": folder_id, "attachment_keys": keys, "attachment_urls": urls}


@router.get("/email/attachments/presign")
async def presign_email_attachment(object_path: str):
    if not object_path:
        raise HTTPException(status_code=400, detail="object_path is required")
    try:
        return {
            "url": generate_presigned_url(
                object_path,
                bucket=settings.AWS_S3_EMAIL_BUCKET,
                bucket_env_var_name="AWS_S3_EMAIL_BUCKET",
            )
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

