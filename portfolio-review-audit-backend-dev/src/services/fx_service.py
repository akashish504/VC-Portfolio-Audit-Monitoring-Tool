"""Foreign-exchange rates: month-end rates stored in DB (XE historic_rate writes) or bundled mock quotes."""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.configs.env import settings

logger = logging.getLogger(__name__)


class FxConversionUnavailable(Exception):
    """Raised when historical FX rate cannot be obtained after retries."""

    def __init__(self, from_ccy: str, to_ccy: str, at: datetime, detail: str = "") -> None:
        self.from_ccy = from_ccy
        self.to_ccy = to_ccy
        self.at = at
        super().__init__(
            f"FX conversion unavailable for {from_ccy}->{to_ccy} at {at.isoformat()}"
            + (f": {detail}" if detail else "")
        )

_MOCK_USD = {
    "from": {"quotecurrency": "USD", "mid": 1.0},
    "to": [
        {"quotecurrency": "INR", "mid": 83.512345},
        {"quotecurrency": "EUR", "mid": 0.921234},
    ],
    "timestamp": "2026-05-20T00:00:00Z",
}

# Approximate cross-rates derived from USD base for local/testing.
_MOCK_CROSS: dict[str, dict[str, float]] = {
    "USD": {"INR": 83.512345, "EUR": 0.921234},
    "INR": {"USD": 1.0 / 83.512345, "EUR": 0.921234 / 83.512345},
    "EUR": {"USD": 1.0 / 0.921234, "INR": 83.512345 / 0.921234},
}

_DEFAULT_QUOTE_CURRENCIES: tuple[str, ...] = (
    "USD",
    "INR",
    "EUR",
    "JPY",
    "GBP",
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "SGD",
    "HKD",
    "NZD",
    "IDR",
    "THB",
)


def _normalize_currency(code: str) -> str:
    c = (code or "").strip().upper()
    if len(c) != 3 or not c.isalpha():
        raise HTTPException(status_code=422, detail="Currency must be a 3-letter ISO-4217 code")
    return c


def mock_fx_rates(from_currency: str) -> dict[str, Any]:
    base = _normalize_currency(from_currency)
    targets = _MOCK_CROSS.get(base, _MOCK_CROSS["USD"])
    to_list = [{"quotecurrency": t, "mid": float(mid)} for t, mid in targets.items() if t != base]
    return {
        "from": {"quotecurrency": base, "mid": 1.0},
        "to": to_list,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _configured_quote_currency_list() -> list[str]:
    raw = getattr(settings, "FX_QUOTE_CURRENCIES", None) or ""
    s = str(raw).strip()
    if s:
        out: list[str] = []
        for part in s.split(","):
            p = part.strip()
            if len(p) == 3 and p.isalpha():
                out.append(p.upper())
        return sorted(set(out))
    return sorted(_DEFAULT_QUOTE_CURRENCIES)


def _xe_targets_for_base(base: str) -> list[str]:
    currencies = list(_configured_quote_currency_list())
    return sorted({c for c in currencies if c != base})


def _rates_from_mock_tables(base: str) -> dict[str, Any]:
    if base == "USD":
        out = dict(_MOCK_USD)
        out["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return out
    return mock_fx_rates(base)


def _xe_credentials_configured() -> bool:
    account_id = (getattr(settings, "XE_ACCOUNT_ID", None) or "").strip()
    api_key = (getattr(settings, "XE_API_KEY", None) or "").strip()
    return bool(account_id and api_key)


async def _latest_stored_month_end(db: AsyncSession) -> Optional[date]:
    """Return the most recent ``month_end_date`` present in ``fx_monthly_rate``."""
    from src.db.models import FxMonthlyRate  # lazy import — avoids circular dep

    return (
        await db.execute(
            select(FxMonthlyRate.month_end_date)
            .where(FxMonthlyRate.from_currency == _BASE_CURRENCY)
            .order_by(FxMonthlyRate.month_end_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _rates_from_latest_month(db: AsyncSession, base: str) -> dict[str, Any]:
    """Build the internal FX dict for ``base`` from the latest stored month-end.

    Cross rates are derived from the USD base rows: ``mid(base->X) =
    usd(X) / usd(base)``. Falls back to mock tables if nothing is stored yet.
    """
    from src.db.models import FxMonthlyRate  # lazy import — avoids circular dep

    month_end = await _latest_stored_month_end(db)
    if month_end is None:
        logger.warning("FX: no month-end rates stored yet — falling back to mock tables")
        return _rates_from_mock_tables(base)

    rows = (
        await db.execute(
            select(FxMonthlyRate).where(
                FxMonthlyRate.from_currency == _BASE_CURRENCY,
                FxMonthlyRate.month_end_date == month_end,
            )
        )
    ).scalars().all()
    usd_rates: dict[str, float] = {_BASE_CURRENCY: 1.0}
    fx_ts = month_end.isoformat()
    for row in rows:
        usd_rates[row.to_currency] = float(row.rate)
        if row.fx_timestamp:
            fx_ts = row.fx_timestamp

    base_usd = usd_rates.get(base)
    if base_usd is None or base_usd == 0:
        raise HTTPException(
            status_code=422, detail=f"No stored month-end rate for base currency {base}"
        )

    to_list = [
        {"quotecurrency": ccy, "mid": usd_rate / base_usd}
        for ccy, usd_rate in sorted(usd_rates.items())
        if ccy != base
    ]
    return {
        "from": {"quotecurrency": base, "mid": 1.0},
        "to": to_list,
        "timestamp": fx_ts,
    }


class FxService:
    @staticmethod
    async def fetch_rates(
        from_currency: str, *, db: Optional[AsyncSession] = None
    ) -> dict[str, Any]:
        """Return the internal FX-rate dict for ``from_currency``.

        When ``db`` is provided (the normal path), rates are read from the stored
        *latest* month-end in ``fx_monthly_rate`` — XE is never called. Cross
        rates are derived from the USD base rows. Falls back to bundled mock
        tables when mock mode is on, credentials are missing, or no ``db`` is
        supplied.
        """
        base = _normalize_currency(from_currency)

        use_mock = settings.FX_USE_MOCK
        if not use_mock and not _xe_credentials_configured():
            logger.warning(
                "XE_ACCOUNT_ID or XE_API_KEY unset — using bundled mock FX rates "
                "(set both for live XE, or set FX_USE_MOCK=true to force mock).",
            )
            use_mock = True

        if use_mock or db is None:
            return _rates_from_mock_tables(base)

        return await _rates_from_latest_month(db, base)

    @staticmethod
    def conversion_rate(rates: dict[str, Any], target_currency: str) -> tuple[float, str]:
        target = _normalize_currency(target_currency)
        from_block = rates.get("from") or {}
        source = _normalize_currency(str(from_block.get("quotecurrency") or ""))
        source_mid = float(from_block.get("mid") or 1.0)
        if source == target:
            raise HTTPException(status_code=422, detail="Target currency matches current currency")

        to_list = rates.get("to") or []
        target_mid: Optional[float] = None
        for item in to_list:
            if not isinstance(item, dict):
                continue
            if _normalize_currency(str(item.get("quotecurrency") or "")) == target:
                target_mid = float(item.get("mid"))
                break
        if target_mid is None:
            raise HTTPException(status_code=422, detail=f"No exchange rate available for {target}")

        rate = target_mid / source_mid if source_mid else target_mid
        fx_ts = str(rates.get("timestamp") or "")
        return rate, fx_ts


# --- Month-end FX (DB-backed) ----------------------------------------------
#
# All financial conversions resolve to a *month-end* rate stored in the
# ``fx_monthly_rate`` table — XE is never called on the request path. Rates are
# stored only as ``USD -> X``; any cross pair (e.g. INR -> EUR) is derived by
# chaining through USD: ``rate(A->B) = usd_rate(B) / usd_rate(A)``.
#
# The table is populated by the monthly scheduler job and the backfill script,
# both via ``fetch_monthly_rate`` below (the only place that calls XE).

_BASE_CURRENCY = "USD"
_HISTORIC_RETRY_ATTEMPTS = 3
_HISTORIC_RETRY_BACKOFF_S = 1.0


def _month_end(d: date) -> date:
    """Return the last calendar day of the month containing ``d``."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last_day)


def previous_month_end(today: date) -> date:
    """Return the last day of the month *before* the one containing ``today``."""
    first_of_this_month = date(today.year, today.month, 1)
    return _month_end(first_of_this_month - timedelta(days=1))


async def _usd_rate_for_month(
    db: AsyncSession, *, to_ccy: str, month_end: date
) -> Optional[tuple[float, str, date]]:
    """Look up the stored USD->``to_ccy`` rate for ``month_end``.

    Falls back to the nearest *earlier* stored month-end if the exact month is
    missing. Returns ``(rate, fx_timestamp, resolved_month_end)`` or ``None``.
    """
    if to_ccy == _BASE_CURRENCY:
        return 1.0, month_end.isoformat(), month_end

    from src.db.models import FxMonthlyRate  # lazy import — avoids circular dep

    row = (
        await db.execute(
            select(FxMonthlyRate)
            .where(
                FxMonthlyRate.from_currency == _BASE_CURRENCY,
                FxMonthlyRate.to_currency == to_ccy,
                FxMonthlyRate.month_end_date <= month_end,
            )
            .order_by(FxMonthlyRate.month_end_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return float(row.rate), row.fx_timestamp or row.month_end_date.isoformat(), row.month_end_date


def _mock_usd_rate(to_ccy: str) -> Optional[float]:
    return _MOCK_CROSS.get(_BASE_CURRENCY, {}).get(to_ccy)


async def _resolve_month_end_rate(
    db: AsyncSession, *, from_ccy: str, to_ccy: str, month_end: date
) -> tuple[float, str]:
    """Resolve ``from_ccy``->``to_ccy`` for ``month_end`` from stored USD rates."""
    use_mock = settings.FX_USE_MOCK or not _xe_credentials_configured()

    if use_mock:
        usd_from = 1.0 if from_ccy == _BASE_CURRENCY else _mock_usd_rate(from_ccy)
        usd_to = 1.0 if to_ccy == _BASE_CURRENCY else _mock_usd_rate(to_ccy)
        if usd_from is None or usd_to is None or usd_from == 0:
            raise FxConversionUnavailable(
                from_ccy, to_ccy, datetime.combine(month_end, time()), "no mock rate"
            )
        return usd_to / usd_from, month_end.isoformat()

    from_lookup = await _usd_rate_for_month(db, to_ccy=from_ccy, month_end=month_end)
    to_lookup = await _usd_rate_for_month(db, to_ccy=to_ccy, month_end=month_end)
    if from_lookup is None or to_lookup is None:
        missing = from_ccy if from_lookup is None else to_ccy
        raise FxConversionUnavailable(
            from_ccy,
            to_ccy,
            datetime.combine(month_end, time()),
            f"no stored USD->{missing} rate at or before {month_end.isoformat()}",
        )

    usd_from, _from_ts, _ = from_lookup
    usd_to, to_ts, _ = to_lookup
    if usd_from == 0:
        raise FxConversionUnavailable(
            from_ccy, to_ccy, datetime.combine(month_end, time()), "zero source rate"
        )
    return usd_to / usd_from, to_ts


async def fetch_historical_rate(
    db: AsyncSession,
    *,
    from_currency: str,
    to_currency: str,
    at: datetime,
) -> tuple[float, str]:
    """Return ``(rate, fx_timestamp)`` for ``from_currency`` -> ``to_currency``.

    The rate is the stored *month-end* rate for the month containing ``at``
    (e.g. ``at`` anywhere in March 2025 -> the 2025-03-31 rate), derived from the
    ``USD`` base rows. If that month is missing, the nearest earlier stored
    month-end is used. XE is never called here — ``fetch_monthly_rate`` populates
    the table out-of-band. Raises ``FxConversionUnavailable`` when no stored rate
    (and no mock fallback) is available.
    """
    from_ccy = _normalize_currency(from_currency)
    to_ccy = _normalize_currency(to_currency)
    if from_ccy == to_ccy:
        return 1.0, at.isoformat()

    return await _resolve_month_end_rate(
        db, from_ccy=from_ccy, to_ccy=to_ccy, month_end=_month_end(at.date())
    )


# --- XE writer (scheduler + backfill only) ---------------------------------


async def _fetch_xe_historic_usd_rates(*, at: datetime) -> tuple[dict[str, float], str]:
    """Fetch USD-> all configured quote currencies for ``at`` in a single XE call.

    The XE ``historic_rate`` endpoint accepts a comma-separated ``to`` list, so
    one request covers every currency. Returns ``({ccy: rate}, fx_timestamp)``.
    Raises HTTPException on transport/HTTP error.
    """
    account_id = (getattr(settings, "XE_ACCOUNT_ID", None) or "").strip()
    api_key = (getattr(settings, "XE_API_KEY", None) or "").strip()
    if not account_id or not api_key:
        raise HTTPException(status_code=503, detail="Exchange rate provider is not configured")

    url = (getattr(settings, "XE_HISTORIC_URL", None) or "").strip()
    if not url:
        raise HTTPException(status_code=503, detail="XE_HISTORIC_URL is not configured")

    targets = _xe_targets_for_base(_BASE_CURRENCY)
    if not targets:
        raise HTTPException(status_code=422, detail="No counter-currencies configured for FX quotes")

    rate_date = at.date()
    rate_time = at.time().replace(microsecond=0).isoformat(timespec="minutes")
    params = {
        "from": _BASE_CURRENCY,
        "to": ",".join(targets),
        "date": rate_date.isoformat(),
        "time": rate_time,
        "amount": 1,
    }
    timeout = float(settings.EXTERNAL_API_TIMEOUT or 30)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            url,
            params=params,
            auth=(account_id, api_key),
            headers={"Accept": "application/json", "Accept-Charset": "UTF-8"},
        )
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="XE response was not a JSON object")

    out: dict[str, float] = {}
    for item in data.get("to") or []:
        if not isinstance(item, dict):
            continue
        qc = item.get("quotecurrency") or item.get("quote_currency")
        mid = item.get("mid") if item.get("mid") is not None else item.get("rate")
        if qc and mid is not None:
            out[_normalize_currency(str(qc))] = float(mid)
    if not out:
        raise HTTPException(status_code=502, detail="XE historic_rate returned no quotes")

    fx_ts = str(data.get("timestamp") or at.isoformat())
    return out, fx_ts


async def fetch_monthly_rate(
    db: AsyncSession,
    *,
    month_end: date,
    overwrite: bool = False,
) -> int:
    """Fetch & upsert USD-> all quote-currency rates for ``month_end`` into the DB.

    This is the ONLY function that calls XE on the write path. One XE call covers
    all currencies. Idempotent: skips months already stored unless ``overwrite``.
    Returns the number of rows written. End-of-day is anchored at 23:59 UTC.
    """
    from src.db.models import FxMonthlyRate  # lazy import — avoids circular dep

    if not overwrite:
        existing = (
            await db.execute(
                select(FxMonthlyRate.id)
                .where(
                    FxMonthlyRate.from_currency == _BASE_CURRENCY,
                    FxMonthlyRate.month_end_date == month_end,
                )
                .limit(1)
            )
        ).first()
        if existing is not None:
            logger.info("FX monthly: %s already stored — skipping", month_end.isoformat())
            return 0

    at = datetime.combine(month_end, time(23, 59, 0), tzinfo=timezone.utc)

    last_err: Optional[Exception] = None
    rates: Optional[dict[str, float]] = None
    fx_ts = at.isoformat()
    for attempt in range(1, _HISTORIC_RETRY_ATTEMPTS + 1):
        try:
            rates, fx_ts = await _fetch_xe_historic_usd_rates(at=at)
            break
        except (httpx.HTTPError, HTTPException) as exc:
            last_err = exc
            logger.warning(
                "FX monthly attempt %d/%d failed for %s: %s",
                attempt,
                _HISTORIC_RETRY_ATTEMPTS,
                month_end.isoformat(),
                exc,
            )
            if attempt < _HISTORIC_RETRY_ATTEMPTS:
                await asyncio.sleep(_HISTORIC_RETRY_BACKOFF_S * attempt)

    if rates is None:
        raise FxConversionUnavailable(
            _BASE_CURRENCY,
            "*",
            at,
            detail=str(last_err) if last_err else "all retries failed",
        )

    written = 0
    for to_ccy, rate in sorted(rates.items()):
        if to_ccy == _BASE_CURRENCY:
            continue
        row = (
            await db.execute(
                select(FxMonthlyRate).where(
                    FxMonthlyRate.from_currency == _BASE_CURRENCY,
                    FxMonthlyRate.to_currency == to_ccy,
                    FxMonthlyRate.month_end_date == month_end,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                FxMonthlyRate(
                    from_currency=_BASE_CURRENCY,
                    to_currency=to_ccy,
                    month_end_date=month_end,
                    rate=rate,
                    fx_timestamp=fx_ts,
                    source="xe_historic",
                )
            )
        elif overwrite:
            row.rate = rate
            row.fx_timestamp = fx_ts
            row.source = "xe_historic"
        written += 1

    await db.flush()
    logger.info("FX monthly: stored %d USD rates for %s", written, month_end.isoformat())
    return written
