from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
import traceback
from logging.config import fileConfig

import alembic
from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

# Alembic Config object (from alembic.ini)
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use a dedicated logger so it shows up in `kubectl logs`.
logger = logging.getLogger("alembic.env")

# Ensure `import src...` works when running Alembic from repo root.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass


SCHEMA_NAME = "portfolioauditreview"

# Keep in sync with Pipfile / Pipfile.lock (`alembic = "==x.y.z"`).
_EXPECTED_ALEMBIC = "1.16.5"


def _log_alembic_runtime() -> None:
    """Log installed Alembic so logs prove which code path (e.g. String(32) in impl.py) applies."""
    try:
        dist_ver = importlib.metadata.version("alembic")
    except importlib.metadata.PackageNotFoundError:
        dist_ver = "not installed (metadata)"
    mod_ver = getattr(alembic, "__version__", "unknown")
    logger.info("[alembic] runtime: module __version__=%s, distribution=%s", mod_ver, dist_ver)
    if dist_ver != _EXPECTED_ALEMBIC:
        msg = (
            f"[alembic] WARNING: distribution alembic {dist_ver!r} != expected {_EXPECTED_ALEMBIC!r} "
            "(see Pipfile). Behavior (e.g. version table DDL) may differ."
        )
        if os.environ.get("ALEMBIC_STRICT_VERSION", "").lower() in ("1", "true", "yes"):
            raise RuntimeError(msg)
        logger.warning(msg)


def _sync_db_url() -> str:
    """
    Build a *sync* SQLAlchemy URL for Alembic (psycopg2),
    based on the same POSTGRES_* env vars used by the app.
    """
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "postgres")
    # Match pgAdmin default behavior: try SSL if available, but don't require verification.
    sslmode = os.environ.get("POSTGRES_SSLMODE", "require")
    sslrootcert = os.environ.get("POSTGRES_SSLROOTCERT")

    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}?sslmode={sslmode}"
    if sslrootcert:
        url += f"&sslrootcert={sslrootcert}"
    return url


def _get_target_metadata():
    # Import models so Base.metadata contains tables (even if we only migrate one).
    from src.db.models import Base  # noqa: WPS433 (runtime import for Alembic)

    return Base.metadata


def _get_alembic_versions(connection) -> set[str]:
    rows = connection.execute(
        text(f'SELECT version_num FROM "{SCHEMA_NAME}".alembic_version'),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0] is not None}


def _verify_post_migration(
    connection,
    script: ScriptDirectory,
    *,
    versions_before: set[str],
    versions_after: set[str],
) -> None:
    """
    Fail loudly if migrate appeared to succeed but nothing was stamped or core DDL is missing.
    Catches: VARCHAR(32) stamp failure, split-transaction rollbacks, wrong DB, etc.
    """
    head_ids = frozenset(script.get_heads())
    if not head_ids:
        raise RuntimeError("[alembic] post-check: script has no revision heads — fix your migration graph.")

    if not versions_after:
        raise RuntimeError(
            "[alembic] post-check FAILED: portfolioauditreview.alembic_version has zero rows after "
            "migrate. The stamp step did not persist (common cause: version_num too short for revision id). "
            "Expected at least one of heads=%s." % (sorted(head_ids),)
        )

    migrated = versions_before != versions_after
    at_script_head = head_ids <= versions_after

    if migrated and not at_script_head:
        raise RuntimeError(
            "[alembic] post-check FAILED: migration ran but expected heads %s not in alembic_version; have %s. "
            "Migrate did not record expected head." % (sorted(head_ids), versions_after)
        )

    if not migrated and not at_script_head:
        logger.info(
            "[alembic] post-check: database is behind script head (have %s, heads=%s); "
            "skipping head stamp check (normal for `alembic current` when upgrades are pending)",
            versions_after,
            sorted(head_ids),
        )

    mx = connection.execute(
        text(
            """
            SELECT character_maximum_length::int
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = 'alembic_version'
              AND column_name = 'version_num'
            """
        ),
        {"schema": SCHEMA_NAME},
    ).scalar()
    if mx is not None and mx < 40:
        raise RuntimeError(
            "[alembic] post-check FAILED: alembic_version.version_num max length is %s; "
            "need >= 40 for revision id 0001_reset_portfolioauditreview_schema. "
            "Widen the column or shorten the revision id." % (mx,)
        )

    reg = connection.execute(
        text("SELECT to_regclass(:q)"),
        {"q": f"{SCHEMA_NAME}.portfolio_companies"},
    ).scalar()
    if reg is None:
        raise RuntimeError(
            "[alembic] post-check FAILED: portfolioauditreview.portfolio_companies is missing after "
            "migrate. The 0001_reset migration DDL did not persist (rolled back or wrong schema)."
        )

    logger.info(
        "[alembic] post-check OK: alembic_version=%s, version_num char max=%s, portfolio_companies=%s",
        versions_after,
        mx,
        reg,
    )


def run_migrations_offline() -> None:
    _log_alembic_runtime()
    url = _sync_db_url()
    script = ScriptDirectory.from_config(config)
    logger.info("[alembic] offline mode")
    logger.info("[alembic] script_location=%s", config.get_main_option("script_location"))
    logger.info("[alembic] target_heads=%s", script.get_heads())
    logger.info(
        "[alembic] db_target host=%s port=%s db=%s",
        os.environ.get("POSTGRES_HOST", "localhost"),
        os.environ.get("POSTGRES_PORT", "5432"),
        os.environ.get("POSTGRES_DB", "postgres"),
    )
    context.configure(
        url=url,
        target_metadata=_get_target_metadata(),
        literal_binds=True,
        include_schemas=True,
        version_table_schema=SCHEMA_NAME,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _log_alembic_runtime()
    url = _sync_db_url()
    script = ScriptDirectory.from_config(config)
    logger.info("[alembic] online mode")
    logger.info("[alembic] script_location=%s", config.get_main_option("script_location"))
    logger.info("[alembic] target_heads=%s", script.get_heads())
    logger.info(
        "[alembic] db_target host=%s port=%s db=%s",
        os.environ.get("POSTGRES_HOST", "localhost"),
        os.environ.get("POSTGRES_PORT", "5432"),
        os.environ.get("POSTGRES_DB", "postgres"),
    )

    connectable = create_engine(url, pool_pre_ping=True)

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=_get_target_metadata(),
                include_schemas=True,
                version_table_schema=SCHEMA_NAME,
            )

            # Alembic's DefaultImpl.version_table_impl() always uses String(32) for
            # version_num (see alembic/ddl/impl.py). Long revision ids like
            # 0001_reset_portfolioauditreview_schema overflow that and the stamp INSERT
            # fails, rolling back the whole migration transaction.
            #
            # configure(version_table=...) is only the *table name* (str), not a Table.
            # Pre-create / widen alembic_version in the SAME transaction as run_migrations().
            # Never connection.commit() before this block — that can leave an empty
            # alembic_version while app DDL rolls back.
            with context.begin_transaction():
                connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"'))
                connection.execute(
                    text(
                        f"""
                        CREATE TABLE IF NOT EXISTS "{SCHEMA_NAME}".alembic_version (
                            version_num VARCHAR(512) NOT NULL,
                            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        f'ALTER TABLE "{SCHEMA_NAME}".alembic_version '
                        "ALTER COLUMN version_num TYPE VARCHAR(512)"
                    )
                )
                logger.info(
                    "[alembic] alembic_version.version_num ensured VARCHAR(512) for long revision ids"
                )
                versions_before = _get_alembic_versions(connection)
                context.run_migrations()

            # After the transaction commits, verify — raises if stamp/DDL did not persist.
            versions_after = _get_alembic_versions(connection)
            _verify_post_migration(
                connection,
                script,
                versions_before=versions_before,
                versions_after=versions_after,
            )
    except Exception:
        logger.error("[alembic] migration failed — full traceback:\n%s", traceback.format_exc())
        raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
