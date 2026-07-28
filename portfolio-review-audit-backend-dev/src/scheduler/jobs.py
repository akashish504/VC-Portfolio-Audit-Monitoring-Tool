"""
Scheduled jobs (portfolio-review parity).
"""
import logging
import os
from typing import Any, Dict

from src.scheduler.scheduler import JobScheduler
from src.scripts.data_manipulation.audited_financials_email_ingestion import process_audited_financials_emails
from src.scripts.data_manipulation.classifying_incoming_emails import classify_incoming_emails
from src.scripts.data_manipulation.fx_monthly_refresh import run_fx_monthly_refresh
from src.scripts.data_manipulation.rollover_review_cycle import run_rollover_review_cycle
from src.scripts.data_manipulation.scheduled_data_sync import run_scheduled_data_sync

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict[str, Any]] = {
    "classify_incoming_emails": {
        "func": classify_incoming_emails,
        "trigger": "interval",
        "minutes": 10,
        "replace_existing": True,
        "max_instances": 1,
        "description": "Classify TempEmailHistory → EmailHistory every 10 minutes",
    },
    "process_audited_financials_emails": {
        "func": process_audited_financials_emails,
        "trigger": "interval",
        "minutes": 10,
        "replace_existing": True,
        "max_instances": 1,
        "description": "Audited-financials email ingestion side-flow every 10 minutes",
    },
    "rollover_review_cycle": {
        "func": run_rollover_review_cycle,
        "trigger": "cron",
        "month": 6,
        "day": 1,
        "hour": 3,
        "minute": 0,
        "timezone": "Asia/Kolkata",
        "replace_existing": True,
        "max_instances": 1,
        "description": "Yearly review-cycle rollover (3:00 AM IST on 1 June)",
    },
    "scheduled_data_sync": {
        "func": run_scheduled_data_sync,
        "trigger": "cron",
        "hour": 21,
        "minute": 0,
        "timezone": "Asia/Kolkata",
        "replace_existing": True,
        "max_instances": 1,
        "description": "Automatic data sync at 21:00 IST (reads enabled/frequency from config_table)",
    },
    "fx_monthly_refresh": {
        "func": run_fx_monthly_refresh,
        "trigger": "cron",
        "day": 1,
        "hour": 0,
        "minute": 5,
        "timezone": "Asia/Kolkata",
        "replace_existing": True,
        "max_instances": 1,
        "description": "Store previous month-end USD FX rates (00:05 IST on the 1st)",
    },
}


def register_jobs() -> None:
    try:
        scheduler = JobScheduler()
        if not scheduler.is_primary_worker():
            logger.info(
                "Worker %s: skipping job registration (not primary scheduler worker)",
                os.getpid(),
            )
            return

        logger.info("Worker %s: registering scheduled jobs", os.getpid())

        for job_id, cfg in JOBS.items():
            config = dict(cfg)
            description = config.pop("description", "")
            func = config.pop("func")
            trigger = config.pop("trigger")
            logger.info("Registering job %s — %s", job_id, description)
            job = scheduler.add_job(
                func,
                trigger,
                id=job_id,
                **config,
            )
            if job:
                logger.info(
                    "Job %s registered — next run: %s",
                    job_id,
                    getattr(job, "next_run_time", None),
                )
            else:
                logger.error("Failed to register job %s", job_id)

        logger.info("Worker %s: job registration complete", os.getpid())
    except Exception as e:
        logger.error("Job registration failed: %s", e)
        raise
