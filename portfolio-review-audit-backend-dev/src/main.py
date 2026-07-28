"""
VC Audit Discrepancy Tool — Main Application Entry Point
"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response

from src.configs.env import APP_CONTEXT_PATH, settings
from src.utils.aws_credential_logging import log_bedrock_credential_audit
from src.configs.logging_config import setup_logging
from src.configs.csrf import configure_csrf, is_csrf_exempt_request, csrf_protect
from src.exceptions.base import BaseAppException
from src.exceptions.handlers import app_exception_handler, global_exception_handler
from src.middlewares import AccessLoggingMiddleware, ProcessTimeMiddleware
from src.scheduler.scheduler import JobScheduler
from src.scheduler.jobs import register_jobs
from src.utils.auth import verify_token
from src.services.company_audit_context import clear_audit_actor_email, set_audit_actor_email
from src.services.audit_log_context import REVIEW_CYCLE_ADJUSTMENTS, clear_audit_log_context, set_audit_log_context
from src.versions.v1 import main as v1_route
from fastapi_csrf_protect.exceptions import CsrfProtectError

# ── Logging ───────────────────────────────────────────────────────────────────
setup_logging()
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
# Interactive API docs (Swagger UI / ReDoc) and the OpenAPI schema expose every
# endpoint, parameter, and data schema. They are disabled on every deployed
# environment (dev, staging, prod) so the spec is never served unauthenticated,
# and only enabled for local development (LOCAL_DEV).
_DOCS_ENABLED = settings.LOCAL_DEV

app = FastAPI(
    title="VC Audit Discrepancy Tool",
    description="Backend API for detecting discrepancies in audit PDF files submitted by portfolio entities.",
    version="1.0.0",
    docs_url=f"{APP_CONTEXT_PATH}/docs" if _DOCS_ENABLED else None,
    redoc_url=f"{APP_CONTEXT_PATH}/redoc" if _DOCS_ENABLED else None,
    openapi_url=f"{APP_CONTEXT_PATH}/openapi.json" if _DOCS_ENABLED else None,
)

# ── CSRF ──────────────────────────────────────────────────────────────────────
csrf_protect = configure_csrf(app)

# ── Exception Handlers ────────────────────────────────────────────────────────
app.add_exception_handler(BaseAppException, app_exception_handler)
app.add_exception_handler(Exception,        global_exception_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CORS_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── GZip ──────────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── Process Time ──────────────────────────────────────────────────────────────
app.add_middleware(ProcessTimeMiddleware)

# ── HTTP access log (method, path, status, duration, content-type) — must be last add_middleware so it wraps the stack
app.add_middleware(
    AccessLoggingMiddleware,
    skip_paths=frozenset({f"{APP_CONTEXT_PATH}/v1/health"}),
)


# ── Security Headers ──────────────────────────────────────────────────────────
# Swagger UI needs inline scripts — omit CSP on docs/redoc HTML.
_DOCS_PATHS = frozenset(
    (
        f"{APP_CONTEXT_PATH}/docs",
        f"{APP_CONTEXT_PATH}/docs/",
        f"{APP_CONTEXT_PATH}/redoc",
        f"{APP_CONTEXT_PATH}/redoc/",
    )
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    if request.url.path not in _DOCS_PATHS:
        response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


# ── CSRF Middleware ───────────────────────────────────────────────────────────
CSRF_EXEMPT_PATHS = [
    f"{APP_CONTEXT_PATH}/v1/health",
    f"{APP_CONTEXT_PATH}/v1/dummy",
    f"{APP_CONTEXT_PATH}/v1/user/get-user",
    f"{APP_CONTEXT_PATH}/docs",
    f"{APP_CONTEXT_PATH}/redoc",
    f"{APP_CONTEXT_PATH}/openapi.json",
]


@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    # Always allow CORS preflight through. Browsers won't include CSRF/JWT headers
    # on OPTIONS, and blocking it breaks every cross-origin request.
    if request.method == "OPTIONS":
        return await call_next(request)
    # Local dev / explicit disable switch.
    if getattr(settings, "DISABLE_CSRF", False) is True:
        return await call_next(request)
    if is_csrf_exempt_request(request, CSRF_EXEMPT_PATHS):
        return await call_next(request)
    try:
        await csrf_protect.validate_csrf(request)
    except CsrfProtectError:
        # Return directly — raising HTTPException inside Starlette middleware
        # bypasses FastAPI's exception handlers and becomes a 500.
        return JSONResponse(
            status_code=403,
            content={"message": "CSRF token validation failed", "data": None, "error_code": "csrf_error"},
        )
    return await call_next(request)


# ── JWT Auth Middleware ───────────────────────────────────────────────────────
JWT_EXEMPT_PATHS = [
    f"{APP_CONTEXT_PATH}/v1/health",
    f"{APP_CONTEXT_PATH}/v1/dummy",
    f"{APP_CONTEXT_PATH}/docs",
    f"{APP_CONTEXT_PATH}/redoc",
    f"{APP_CONTEXT_PATH}/openapi.json",
]


@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    # Local dev / explicit disable switch.
    if getattr(settings, "DISABLE_AUTH", False) is True:
        # In local dev bypass, provide a minimal synthetic claims object so
        # downstream code can read basic identity fields without a real JWT.
        request.state.jwt_claims = {
            "email": "local-dev@peakxv.invalid",
            "sub": "local-dev",
            "groups": [],
        }
        set_audit_actor_email("local-dev@peakxv.invalid")
        try:
            return await call_next(request)
        finally:
            clear_audit_actor_email()
    if request.method == "OPTIONS" or request.url.path in JWT_EXEMPT_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"message": "Authorization header missing or malformed", "data": None, "error_code": "auth_error"},
        )

    token = auth_header.split(" ", 1)[1]
    try:
        claims = await verify_token(token)
        request.state.jwt_claims = claims
        set_audit_actor_email(claims.get("email") or claims.get("sub") or "")
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail), "data": None, "error_code": "auth_error"},
        )
    except Exception:
        logger.exception("JWT middleware: token verification failed")
        return JSONResponse(
            status_code=401,
            content={"message": "Token verification failed", "data": None, "error_code": "auth_error"},
        )

    # Enforce group-based authorisation when AUTHZ_REQUIRED_GROUPS is configured.
    required_groups_raw = getattr(settings, "AUTHZ_REQUIRED_GROUPS", "")
    if required_groups_raw:
        required = {g.strip() for g in required_groups_raw.split(",") if g.strip()}
        user_groups = set(claims.get("groups") or [])
        if not required.intersection(user_groups):
            logger.warning(
                "Access denied: user %s not in required groups %s (has: %s)",
                claims.get("sub"),
                required,
                user_groups,
            )
            return JSONResponse(
                status_code=403,
                content={"message": "Access denied: insufficient group membership", "data": None, "error_code": "authz_error"},
            )

    try:
        return await call_next(request)
    finally:
        clear_audit_actor_email()


@app.middleware("http")
async def audit_log_context_middleware(request: Request, call_next):
    """Route portfolio patches to cycle vs company audit (Option C) via ``X-Audit-Context``."""
    raw = (request.headers.get("X-Audit-Context") or "").strip().lower()
    if raw in (REVIEW_CYCLE_ADJUSTMENTS, "review-cycle-adjustments"):
        set_audit_log_context(REVIEW_CYCLE_ADJUSTMENTS)
    try:
        return await call_next(request)
    finally:
        clear_audit_log_context()


# ── Lifecycle ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("Starting VC Audit Discrepancy Tool...")
    log_bedrock_credential_audit(logger, context="FastAPI startup (before scheduler)")

    if settings.ENV == "prod":
        try:
            from src.db.session import get_pr_source_db
            from sqlalchemy import text
            _pr_db = get_pr_source_db()
            _pr_db.execute(text("SELECT 1"))
            _pr_db.close()
            logger.info("PR source DB (portfolioreview) reachable — classify_incoming_emails will run")
        except Exception as exc:
            logger.error("PR source DB (portfolioreview) NOT reachable — classify_incoming_emails will fail: %s", exc)

    scheduler = JobScheduler()
    scheduler.start()
    register_jobs()
    logger.info("Startup complete.")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down...")
    JobScheduler().shutdown()


# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(v1_route.api_router, prefix=APP_CONTEXT_PATH)
