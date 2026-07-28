"""
Middleware definitions for the application
"""
import logging
import time
from typing import FrozenSet, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_access_log = logging.getLogger("src.http")


class AccessLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every API request and response status (and duration). Logger: src.http.
    """

    def __init__(self, app, skip_paths: Optional[FrozenSet[str]] = None):
        super().__init__(app)
        self._skip = skip_paths or frozenset()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._skip:
            return await call_next(request)

        start = time.perf_counter()
        path = request.url.path
        q = request.url.query
        if q:
            q = q if len(q) <= 300 else q[:300] + "…"
            path = f"{path}?{q}"
        client = request.client.host if request.client else "-"
        method = request.method

        try:
            response = await call_next(request)
        except Exception:
            ms = (time.perf_counter() - start) * 1000
            _access_log.info("%s %s %s → exception — %.1fms", client, method, path, ms)
            raise

        ms = (time.perf_counter() - start) * 1000
        status = getattr(response, "status_code", "?")
        ct = response.headers.get("content-type", "")
        ct_short = (ct.split(";")[0].strip() if ct else "-")[:60]
        _access_log.info(
            "%s %s %s → %s %.1fms %s",
            client,
            method,
            path,
            status,
            ms,
            ct_short,
        )
        return response


class ProcessTimeMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add X-Process-Time header to every response
    """
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(round(process_time, 4))
        return response
