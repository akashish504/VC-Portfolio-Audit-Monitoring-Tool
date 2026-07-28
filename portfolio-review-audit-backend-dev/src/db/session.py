"""
Database session management
"""
import logging
import ssl
import urllib.parse
from typing import Optional, Union

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from src.configs.env import settings

logger = logging.getLogger(__name__)


def _postgres_password_for_url() -> str:
    return urllib.parse.quote_plus(settings.POSTGRES_PASSWORD or "")


DATABASE_URL = (
    f"postgresql+asyncpg://{settings.POSTGRES_USER}:{_postgres_password_for_url()}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

SYNC_DATABASE_URL = (
    f"postgresql+psycopg2://{settings.POSTGRES_USER}:{_postgres_password_for_url()}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

def _asyncpg_ssl_context() -> Optional[Union[ssl.SSLContext, bool]]:
    """
    Build an asyncpg-compatible SSL configuration from POSTGRES_SSLMODE/POSTGRES_SSLROOTCERT.

    - asyncpg uses `ssl=` (SSLContext / True / None), not libpq-style `sslmode=`.
    - Default here is "require" in settings, which corresponds to encrypted transport without cert verification.
    """
    mode = str(getattr(settings, "POSTGRES_SSLMODE", "") or "").strip().lower()
    if not mode or mode in ("disable", "allow", "prefer"):
        return None

    cafile = getattr(settings, "POSTGRES_SSLROOTCERT", None)

    if mode == "require":
        # Encrypt the connection but do not validate the server certificate.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # verify-ca / verify-full: validate server cert; verify-full also checks hostname.
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = mode == "verify-full"
    return ctx


_async_connect_args: dict = {}
_ssl_ctx = _asyncpg_ssl_context()
if _ssl_ctx is not None:
    _async_connect_args["ssl"] = _ssl_ctx

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args=_async_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Alias for background tasks (matches portfolio-review-app-api-develop naming).
async_session = AsyncSessionLocal

_sync_connect_args: dict = {}
if getattr(settings, "POSTGRES_SSLMODE", None):
    _sync_connect_args["sslmode"] = settings.POSTGRES_SSLMODE
if getattr(settings, "POSTGRES_SSLROOTCERT", None):
    _sync_connect_args["sslrootcert"] = settings.POSTGRES_SSLROOTCERT

sync_engine = create_engine(
    SYNC_DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=_sync_connect_args,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
)


def get_sync_db() -> Session:
    """Sync Session for APScheduler jobs / data scripts. Caller must ``close()`` when done."""
    return SyncSessionLocal()


# ---------------------------------------------------------------------------
# Source-DB session — portfolio-review server (prod only)
# ---------------------------------------------------------------------------

_pr_source_engine = None
_pr_source_session_factory = None


def _build_pr_source_engine():
    """
    Build a psycopg2 engine pointing at the portfolio-review Postgres server.
    Called once on first use (lazy) so non-prod deployments never open a
    connection to the source server.

    Reuses POSTGRES_SSLMODE / POSTGRES_SSLROOTCERT from settings for SSL parity
    with the main engine.  Raises RuntimeError if any required PR_POSTGRES_*
    variable is missing.
    """
    import urllib.parse as _urlparse
    from sqlalchemy import create_engine as _create_engine
    from src.configs.env import settings as _s

    missing = [
        v for v in ("PR_POSTGRES_USER", "PR_POSTGRES_PASSWORD", "PR_POSTGRES_HOST", "PR_POSTGRES_DB")
        if not getattr(_s, v, None)
    ]
    if missing:
        raise RuntimeError(
            f"Cannot connect to PR source DB — missing env vars: {', '.join(missing)}"
        )

    password = _urlparse.quote_plus(_s.PR_POSTGRES_PASSWORD or "")
    url = (
        f"postgresql+psycopg2://{_s.PR_POSTGRES_USER}:{password}"
        f"@{_s.PR_POSTGRES_HOST}:{_s.PR_POSTGRES_PORT}/{_s.PR_POSTGRES_DB}"
    )

    connect_args: dict = {
        "options": "-c search_path=portfolioreview",
    }
    if getattr(_s, "POSTGRES_SSLMODE", None):
        connect_args["sslmode"] = _s.POSTGRES_SSLMODE
    if getattr(_s, "POSTGRES_SSLROOTCERT", None):
        connect_args["sslrootcert"] = _s.POSTGRES_SSLROOTCERT

    return _create_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        connect_args=connect_args,
    )


def get_pr_source_db() -> Session:
    """
    Return a sync Session connected to the portfolio-review source DB.
    Used exclusively by classify_incoming_emails (prod only) to read
    portfolioreview.temp_email_history.  Caller must ``close()`` when done.
    Engine is created lazily on first call.
    """
    global _pr_source_engine, _pr_source_session_factory
    if _pr_source_engine is None:
        _pr_source_engine = _build_pr_source_engine()
        _pr_source_session_factory = sessionmaker(
            bind=_pr_source_engine,
            class_=Session,
            autoflush=False,
            autocommit=False,
        )
    return _pr_source_session_factory()


class Base(DeclarativeBase):
    """Base class for all ORM models"""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields one async DB session per request"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
