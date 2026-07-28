"""
Dependency connectivity checks (protected — see require_infra_probe).
"""
import logging

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.configs.env import settings
from src.db.session import engine
from src.utils.authz import require_infra_probe
from src.utils.s3 import get_s3_client

logger = logging.getLogger(__name__)
router = APIRouter()


async def _is_db_connected() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.exception("Database connectivity check failed")
        return False
    except Exception:
        logger.exception("Unexpected database connectivity error")
        return False


def _is_s3_connected() -> bool:
    try:
        client = get_s3_client()
        client.head_bucket(Bucket=settings.S3_BUCKET)
        return True
    except Exception:
        logger.exception("S3 connectivity check failed")
        return False


def _is_snowflake_connected() -> bool:
    try:
        import snowflake.connector

        conn = snowflake.connector.connect(
            user=settings.SNOWFLAKE_USER,
            password=settings.SNOWFLAKE_PASSWORD,
            account=settings.SNOWFLAKE_ACCOUNT,
            warehouse=settings.SNOWFLAKE_WAREHOUSE,
            database=settings.SNOWFLAKE_DATABASE,
            schema=settings.SNOWFLAKE_SCHEMA,
            login_timeout=5,
            network_timeout=5,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Snowflake connectivity check failed")
        return False


@router.get("/dummy")
async def dummy_connectivity(request: Request):
    require_infra_probe(request)
    return {
        "database_connected": await _is_db_connected(),
        "s3_connected": _is_s3_connected(),
        "snowflake_connected": _is_snowflake_connected(),
    }
