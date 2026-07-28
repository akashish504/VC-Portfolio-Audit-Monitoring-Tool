"""
Data sync orchestration — same structure as portfolio-review-app-api-develop
`src/services/data_service.py`: migration scripts, then manipulation scripts, with
batch status persisted on `data_sync_process_batch`.

Snowflake connection test / retry hooks can be wired when Snowflake-backed
scripts land; placeholders log only today.
"""

from typing import Tuple, Optional, Dict, List
from datetime import datetime
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from src.schema.data import DataRequest, DataResponse, ScriptResult
import logging

from src.db.session import async_session
from src.db.models import APP_SCHEMA, DataSyncProcessBatch

from src.scripts.data_migration.audit_raw_from_snowflake import audit_raw_from_snowflake_migration
from src.scripts.data_manipulation.audit_application_tables import audit_application_tables_sync

logger = logging.getLogger(__name__)


class DataService:
    MIGRATION_SCRIPT_MAPPING = {
        "audit_raw_from_snowflake": (
            "audit_raw_from_snowflake_migration",
            audit_raw_from_snowflake_migration,
        ),
    }
    MANIPULATION_SCRIPT_MAPPING = {
        "audit_application_tables": (
            "audit_application_tables_sync",
            audit_application_tables_sync,
        ),
    }

    def __init__(self, db: Optional[AsyncSession]):
        self.db = db

    async def _execute_scripts(self, script_types: List[str], script_mapping: Dict) -> ScriptResult:
        """Execute scripts sequentially; optional Snowflake token retry matches reference."""
        results: dict[str, str] = {}
        executed_scripts: List[str] = []

        for script_type in script_types:
            if script_type not in script_mapping:
                results[f"unknown_script_{script_type}"] = f"Failed: Invalid script type: {script_type}"
                continue

            script_name, script_func = script_mapping[script_type]
            max_retries = 3
            retry_count = 0
            success = False

            while retry_count < max_retries and not success:
                try:
                    script_result = script_func()
                    if script_result is False:
                        results[script_name] = "Failed: script returned False"
                        logger.error(
                            "Script failed (returned False): name=%s type=%s",
                            script_name,
                            script_type,
                        )
                    else:
                        results[script_name] = "Success"
                        executed_scripts.append(script_name)
                        logger.info(
                            "Script executed successfully: name=%s type=%s",
                            script_name,
                            script_type,
                        )
                    success = True

                except Exception as e:
                    error_msg = str(e)
                    retry_count += 1

                    if (
                        "Authentication token has expired" in error_msg
                        or "390114" in error_msg
                    ):
                        logger.warning(
                            "Token expiration detected in script %s, attempt %d/%d: %s",
                            script_name,
                            retry_count,
                            max_retries,
                            error_msg,
                        )

                        if retry_count < max_retries:
                            try:
                                from src.db.snowflake import close_snowflake_connection  # type: ignore[attr-defined]

                                close_snowflake_connection()
                                logger.info(
                                    "Closed expired Snowflake connection for script %s, will retry",
                                    script_name,
                                )
                                await asyncio.sleep(2)
                            except ImportError:
                                logger.warning(
                                    "Snowflake helpers not configured; skipping connection refresh."
                                )
                            except Exception as refresh_error:
                                logger.warning(
                                    "Error during connection refresh for script %s: %s",
                                    script_name,
                                    str(refresh_error),
                                )
                        else:
                            results[
                                script_name
                            ] = f"Failed: Token expiration after {max_retries} attempts: {error_msg}"
                            logger.error(
                                "Script failed after %d attempts due to token expiration: name=%s type=%s error=%s",
                                max_retries,
                                script_name,
                                script_type,
                                error_msg,
                            )
                            success = True
                    else:
                        results[script_name] = f"Failed: {error_msg}"
                        logger.exception(
                            "Script raised exception: name=%s type=%s error=%s",
                            script_name,
                            script_type,
                            error_msg,
                        )
                        success = True

        return ScriptResult(executed_scripts=executed_scripts, results=results)

    @staticmethod
    async def run_in_background(batch_id: str, data_request: DataRequest) -> None:
        start_time = datetime.utcnow()

        async with async_session() as session:
            try:
                await session.execute(
                    text(f'SET search_path TO "{APP_SCHEMA}", public')
                )
                batch_obj = await session.get(DataSyncProcessBatch, batch_id)
                if batch_obj:
                    batch_obj.status = "IN_PROGRESS"
                    await session.commit()
            except Exception:
                logger.exception("Failed to mark batch IN_PROGRESS")
                await session.rollback()

        # Reference API tests Snowflake here before scripts; audit placeholders run without SF.

        error_messages: List[str] = []
        success = False
        _result: Optional[DataResponse] = None
        try:
            service = DataService(None)
            success, _result, _error = await service.process_data(data_request)
            if _result is not None:
                if _result.migration_results is not None:
                    for script_name, outcome in _result.migration_results.results.items():
                        if isinstance(outcome, str) and outcome.startswith("Failed"):
                            error_messages.append(f"{script_name}: {outcome}")
                if _result.manipulation_results is not None:
                    for script_name, outcome in _result.manipulation_results.results.items():
                        if isinstance(outcome, str) and outcome.startswith("Failed"):
                            error_messages.append(f"{script_name}: {outcome}")
        except Exception as _e:
            success = False
            error_messages.append(str(_e))
            logger.exception("process_data crashed in background task")

        async with async_session() as session:
            try:
                await session.execute(
                    text(f'SET search_path TO "{APP_SCHEMA}", public')
                )
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                batch_obj = await session.get(DataSyncProcessBatch, batch_id)
                if batch_obj:
                    batch_obj.status = "SUCCESS" if success else "FAILED"
                    batch_obj.time_taken = elapsed
                    if error_messages:
                        batch_obj.error_message = " | ".join(error_messages)
                    await session.commit()
                    logger.info(
                        "Background task: batch %s status=%s",
                        batch_id,
                        batch_obj.status,
                    )
            except Exception:
                logger.exception("Failed to finalize batch row")
                await session.rollback()

    async def process_data(
        self, data_request: DataRequest
    ) -> Tuple[bool, Optional[DataResponse], Optional[str]]:
        try:
            migration_results = None
            migration_types = list(data_request.migration_type)
            if migration_types:
                if migration_types == ["all"]:
                    migration_types = ["audit_raw_from_snowflake"]
                migration_results = await self._execute_scripts(
                    migration_types,
                    self.MIGRATION_SCRIPT_MAPPING,
                )

            manipulation_results = None
            manipulation_types = list(data_request.manipulation_type)
            if manipulation_types:
                if manipulation_types == ["all"]:
                    manipulation_types = ["audit_application_tables"]
                manipulation_results = await self._execute_scripts(
                    manipulation_types,
                    self.MANIPULATION_SCRIPT_MAPPING,
                )

            response = DataResponse(
                migration_results=migration_results,
                manipulation_results=manipulation_results,
            )

            all_results: List[str] = []
            if migration_results is not None:
                all_results.extend(list(migration_results.results.values()))
            if manipulation_results is not None:
                all_results.extend(list(manipulation_results.results.values()))
            overall_success = (
                all(r == "Success" for r in all_results) if all_results else True
            )

            return overall_success, response, None

        except Exception as e:
            logger.exception("Error processing data: %s", str(e))
            if self.db:
                await self.db.rollback()
            return False, None, f"Error processing data: {str(e)}"
