"""
APScheduler job store — PostgreSQL in ``portfolioauditreview`` schema.
Uses a dedicated table name to avoid collision with `"apscheduler_jobs"` (app metadata in 0001).
"""
import urllib.parse

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from src.configs.env import settings

SCHEMA = "portfolioauditreview"


def _sync_dsn() -> str:
    pwd = urllib.parse.quote_plus(settings.POSTGRES_PASSWORD or "")
    return (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:{pwd}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


jobstores = {
    "default": SQLAlchemyJobStore(
        url=_sync_dsn(),
        tablename="apscheduler_daemon_jobstore",
        tableschema=SCHEMA,
    )
}

executors = {
    "default": AsyncIOExecutor(),
}

job_defaults = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": None,
}
