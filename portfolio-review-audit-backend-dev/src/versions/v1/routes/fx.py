from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.db.models import Entity, PortfolioFile
from src.services.fx_service import FxConversionUnavailable, FxService, fetch_historical_rate
from src.services.fy_end import normalize_fy_end, fy_end_from_legacy_date, fy_end_last_day

router = APIRouter()


@router.get("/fx/rates", tags=["FX"])
async def get_fx_rates(
    from_currency: str = Query(..., alias="from", min_length=3, max_length=3, description="ISO-4217 base currency"),
    db: AsyncSession = Depends(get_db),
):
    """FX quotes from the latest stored month-end rates (mock when none stored or FX_USE_MOCK=true)."""
    return await FxService.fetch_rates(from_currency, db=db)


@router.get("/files/{file_id}/fx/preview-rate", tags=["FX"])
async def get_file_fx_preview_rate(
    file_id: int,
    to: str = Query(..., min_length=3, max_length=3, description="ISO-4217 target currency"),
    db: AsyncSession = Depends(get_db),
):
    """Return the historical FX rate for a file's entity fy_end date.

    Used by the FX preview UI so the preview rate matches what Apply will use.
    The rate is fetched at the last day of the entity's fy_end at 12:00 UTC.
    """
    file_obj = await db.get(PortfolioFile, file_id)
    if file_obj is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file_obj.entity_id is None:
        raise HTTPException(status_code=422, detail="File has no linked entity")

    entity = await db.get(Entity, file_obj.entity_id)
    if entity is None:
        raise HTTPException(status_code=422, detail="Linked entity not found")

    fy_end = normalize_fy_end(entity.fy_end) or fy_end_from_legacy_date(entity.fy_end)
    if not fy_end:
        raise HTTPException(
            status_code=422,
            detail="Entity has no financial year end (fy_end) set — cannot determine FX rate date.",
        )

    try:
        fy_date = fy_end_last_day(fy_end)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Entity fy_end '{fy_end}' is invalid.")

    # Get the from-currency from the file's extraction metadata currency.
    from_ccy: str | None = None
    if file_obj.ocr_metadata is not None and isinstance(file_obj.ocr_metadata.ocr_json, dict):
        raw = file_obj.ocr_metadata.ocr_json.get("currency")
        if isinstance(raw, str) and len(raw.strip()) == 3:
            from_ccy = raw.strip().upper()

    if not from_ccy:
        raise HTTPException(status_code=422, detail="File has no reporting currency set yet.")

    tgt = to.strip().upper()
    if from_ccy == tgt:
        raise HTTPException(status_code=422, detail="Target currency matches file reporting currency.")

    at_dt = datetime.combine(fy_date, time(12, 0, 0), tzinfo=timezone.utc)
    try:
        rate, fx_ts = await fetch_historical_rate(db, from_currency=from_ccy, to_currency=tgt, at=at_dt)
    except FxConversionUnavailable:
        raise HTTPException(
            status_code=422,
            detail=f"Historical FX rate unavailable for {from_ccy}→{tgt} at {fy_date.isoformat()}.",
        )

    return {
        "from": {"quotecurrency": from_ccy, "mid": 1.0},
        "to": [{"quotecurrency": tgt, "mid": rate}],
        "timestamp": fx_ts,
        "fy_end": fy_end,
        "rate_date": fy_date.isoformat(),
    }
