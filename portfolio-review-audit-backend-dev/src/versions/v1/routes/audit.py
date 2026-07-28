"""Audit file routes — v1 (merged from portfolio-review-audit-backend-main)."""

import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schema.audit import (
    AuditFileListResponse,
    AuditFileResponse,
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    FileLinkRequest,
    FileLinkResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from src.services.audit_service import AuditService

logger = logging.getLogger(__name__)
router = APIRouter()


def _claims(request: Request) -> Optional[int]:
    claims = getattr(request.state, "jwt_claims", {})
    raw_uid = claims.get("user_id") or claims.get("internal_user_id")
    user_id: Optional[int] = None
    if isinstance(raw_uid, int):
        user_id = raw_uid
    elif isinstance(raw_uid, str) and raw_uid.isdigit():
        user_id = int(raw_uid)

    return user_id


@router.post(
    "/upload-url",
    response_model=UploadUrlResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Audit Files"],
    summary="Register pending file (no presigned URL; upload via POST /api/v1/files/{file_id}/upload)",
)
async def get_upload_url(
    request: Request,
    body: UploadUrlRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = _claims(request)
    try:
        return await AuditService(db).generate_upload_url(
            company_id=body.company_id,
            file_name=body.file_name,
            mime_type=body.mime_type,
            uploaded_by=user_id,
            checksum_sha256=body.checksum_sha256,
        )
    except Exception as e:
        logger.exception("generate_upload_url failed")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/{file_id}/confirm", response_model=ConfirmUploadResponse, tags=["Audit Files"])
async def confirm_upload(
    file_id: int,
    request: Request,
    body: ConfirmUploadRequest,
    db: AsyncSession = Depends(get_db),
):
    _claims(request)
    try:
        file_obj = await AuditService(db).confirm_upload(
            file_id=file_id,
            size_bytes=body.size_bytes,
            page_count=body.page_count,
            language=body.language,
        )
        return {"file_id": file_obj.id, "upload_status": file_obj.status or ""}
    except FileNotFoundError as e:
        logger.info("confirm_upload not found: %s", file_id)
        raise HTTPException(status_code=404, detail="Not found") from e
    except ValueError as e:
        logger.info("confirm_upload conflict: %s", e)
        raise HTTPException(status_code=409, detail="Conflict") from e
    except Exception as e:
        logger.exception("confirm_upload failed for file %s", file_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/upload", response_model=AuditFileResponse, status_code=status.HTTP_201_CREATED, tags=["Audit Files"])
async def upload_file(
    request: Request,
    company_id: int = Form(...),
    file: UploadFile = File(...),
    page_count: Optional[int] = Form(None),
    language: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user_id = _claims(request)
    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="File too large (max 50MB). Use a smaller file or split the document.",
        )
    try:
        return await AuditService(db).upload_file_direct(
            company_id=company_id,
            file_name=file.filename or "upload",
            file_bytes=file_bytes,
            mime_type=file.content_type,
            uploaded_by=user_id,
            page_count=page_count,
            language=language,
        )
    except Exception as e:
        logger.exception("upload_file_direct failed")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("", response_model=AuditFileListResponse, tags=["Audit Files"])
async def list_files(
    request: Request,
    company_id: Optional[int] = Query(None),
    upload_status: Optional[str] = Query(
        None, description="pending|uploaded|verified|failed|deleted"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    _claims(request)
    try:
        return await AuditService(db).list_files(
            company_id=company_id,
            upload_status=upload_status,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        logger.exception("list_files failed")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/{file_id}", response_model=AuditFileResponse, tags=["Audit Files"])
async def get_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _claims(request)
    try:
        return await AuditService(db).get_file(file_id)
    except FileNotFoundError as e:
        logger.info("get_file not found: %s", file_id)
        raise HTTPException(status_code=404, detail="Not found") from e


@router.get("/{file_id}/download", tags=["Audit Files"])
async def download_file(
    file_id: int,
    request: Request,
    expires_in: int = Query(default=3600, ge=60, le=86400),
    db: AsyncSession = Depends(get_db),
):
    _claims(request)
    try:
        return await AuditService(db).get_download_url(file_id, expires_in)
    except FileNotFoundError as e:
        logger.info("download_file not found: %s", file_id)
        raise HTTPException(status_code=404, detail="Not found") from e
    except ValueError as e:
        logger.info("download_file conflict: %s", e)
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Audit Files"])
async def delete_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = _claims(request)
    try:
        await AuditService(db).delete_file(file_id, deleted_by=user_id)
    except FileNotFoundError as e:
        logger.info("delete_file not found: %s", file_id)
        raise HTTPException(status_code=404, detail="Not found") from e
    except Exception as e:
        logger.exception("delete_file failed for %s", file_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/{file_id}/link",
    response_model=FileLinkResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Audit Files"],
)
async def link_file(
    file_id: int,
    request: Request,
    body: FileLinkRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = _claims(request)
    try:
        return await AuditService(db).link_file(
            file_id=file_id,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            linked_by=user_id,
        )
    except FileNotFoundError as e:
        logger.info("link_file not found: %s", file_id)
        raise HTTPException(status_code=404, detail="Not found") from e
    except ValueError as e:
        logger.info("link_file conflict: %s", e)
        raise HTTPException(status_code=409, detail="Conflict") from e
