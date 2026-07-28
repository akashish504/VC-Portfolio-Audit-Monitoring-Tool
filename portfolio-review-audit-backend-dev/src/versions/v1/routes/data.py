"""
Data sync enqueue — aligns with portfolio-review-app-api-develop `routes/data.py`.
"""

import logging
import uuid

from typing import Any, Dict

from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.db.models import DataSyncProcessBatch
from src.schema.data import DataRequest
from src.services.data_service import DataService

router = APIRouter()
logger = logging.getLogger(__name__)


class ProcessPayload(BaseModel):
    data: DataRequest


@router.post("/process")
async def process_data(
    payload: ProcessPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Queue migration scripts (Snowflake → raw), then manipulation scripts (raw → app tables).
    Request body mirrors the reference API: `{ "data": { "migration_type": [...], "manipulation_type": [...] } }`.
    """
    try:
        data_request = payload.data

        existing_batch_query = select(DataSyncProcessBatch).where(
            DataSyncProcessBatch.status != "SUCCESS"
        ).order_by(DataSyncProcessBatch.created_at.desc())
        existing_batch_result = await db.execute(existing_batch_query)
        existing_batch = existing_batch_result.scalars().first()

        if existing_batch:
            return {
                "success": False,
                "data": {"batch_id": existing_batch.id},
                "message": f"A batch is already {existing_batch.status.lower()}. Please wait for it to complete.",
            }

        batch_id = str(uuid.uuid4())
        batch = DataSyncProcessBatch(
            id=batch_id,
            status="QUEUED",
        )
        db.add(batch)
        await db.flush()
        await db.refresh(batch)
        await db.commit()

        background_tasks.add_task(DataService.run_in_background, batch_id, data_request)

        return {
            "success": True,
            "data": {"batch_id": batch_id},
            "message": "Data processing queued",
        }
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.exception("Failed to queue data processing")
        return {"success": False, "data": None, "message": "Failed to queue data processing"}

