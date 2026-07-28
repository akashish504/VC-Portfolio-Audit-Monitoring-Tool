"""
Scheduler — primary-worker lock so only one Gunicorn worker runs APScheduler;
uses PostgreSQL job store in portfolioauditreview (portfolio-review parity).
"""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: TCH004
from apscheduler.events import (
    EVENT_JOB_ADDED,
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_REMOVED,
    EVENT_JOB_SUBMITTED,
    EVENT_SCHEDULER_SHUTDOWN,
    EVENT_SCHEDULER_STARTED,
)
from apscheduler.job import Job

from src.scheduler.config import executors, job_defaults, jobstores

logger = logging.getLogger(__name__)


class JobScheduler:
    """
    Singleton: only one scheduler process across workers (bound port 37337).
    Mirrors portfolio-review-app-api-develop `scheduler.py` — uses BackgroundScheduler here
    since the app runs BackgroundScheduler-compatible workers.
    """

    _instance: Optional["JobScheduler"] = None
    _scheduler: Optional[AsyncIOScheduler] = None
    _is_primary_worker = False
    _health_status: Dict[str, Any] = {"is_running": False, "last_error": None, "worker_pid": None}

    SCHEDULER_PORT = 37337
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY = 0.1

    def __new__(cls) -> "JobScheduler":
        if cls._instance is None:
            cls._instance = super(JobScheduler, cls).__new__(cls)
            cls._instance._initialize_scheduler()
        return cls._instance

    def _is_port_available(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("localhost", self.SCHEDULER_PORT))
            sock.listen(1)
            return True
        except socket.error:
            return False
        finally:
            sock.close()

    def _initialize_scheduler(self) -> None:
        if self._scheduler is not None:
            return

        for attempt in range(self.MAX_RETRY_ATTEMPTS):
            try:
                if attempt > 0:
                    time.sleep(self.RETRY_DELAY)

                if not self._is_port_available():
                    logger.debug(
                        "Worker %s: scheduler already running in another worker",
                        os.getpid(),
                    )
                    self._is_primary_worker = False
                    return

                logger.info("Worker %s: initializing as primary scheduler worker", os.getpid())

                self._scheduler = AsyncIOScheduler(
                    jobstores=jobstores,
                    executors=executors,
                    job_defaults=job_defaults,
                    timezone="UTC",
                )
                self._setup_listeners()
                self._health_status["worker_pid"] = os.getpid()
                self._is_primary_worker = True

                self._lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._lock_socket.bind(("localhost", self.SCHEDULER_PORT))
                self._lock_socket.listen(1)

                logger.info("Worker %s: primary scheduler acquired lock", os.getpid())
                break

            except socket.error:
                if attempt == self.MAX_RETRY_ATTEMPTS - 1:
                    logger.debug(
                        "Worker %s: could not acquire scheduler lock after %s attempts",
                        os.getpid(),
                        self.MAX_RETRY_ATTEMPTS,
                    )
                    self._cleanup()
                continue
            except Exception as e:
                logger.error("Worker %s: failed to initialize scheduler: %s", os.getpid(), e)
                self._cleanup()
                break

    def _cleanup(self) -> None:
        self._is_primary_worker = False
        if hasattr(self, "_lock_socket"):
            try:
                self._lock_socket.close()
            except Exception:
                pass
        self._scheduler = None
        self._health_status["is_running"] = False
        self._health_status["last_error"] = None

    def is_primary_worker(self) -> bool:
        return self._is_primary_worker

    def _setup_listeners(self) -> None:
        if not self._scheduler:
            return
        self._scheduler.add_listener(self._job_executed_listener, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(self._job_error_listener, EVENT_JOB_ERROR)
        self._scheduler.add_listener(self._job_added_listener, EVENT_JOB_ADDED)
        self._scheduler.add_listener(self._job_removed_listener, EVENT_JOB_REMOVED)
        self._scheduler.add_listener(self._scheduler_started_listener, EVENT_SCHEDULER_STARTED)
        self._scheduler.add_listener(self._scheduler_shutdown_listener, EVENT_SCHEDULER_SHUTDOWN)
        self._scheduler.add_listener(self._job_submitted_listener, EVENT_JOB_SUBMITTED)

    def _job_executed_listener(self, event: Any) -> None:
        logger.info("Worker %s: job %s executed successfully", os.getpid(), event.job_id)

    def _job_error_listener(self, event: Any) -> None:
        msg = (
            f"Worker {os.getpid()}: job {event.job_id} failed with exception: {event.exception}"
        )
        logger.error(msg)
        self._health_status["last_error"] = msg

    def _job_added_listener(self, event: Any) -> None:
        logger.info("Worker %s: job %s added", os.getpid(), event.job_id)

    def _job_removed_listener(self, event: Any) -> None:
        logger.info("Worker %s: job %s removed", os.getpid(), event.job_id)

    def _scheduler_started_listener(self, event: Any) -> None:
        self._health_status["is_running"] = True
        logger.info("Worker %s: scheduler started", os.getpid())

    def _scheduler_shutdown_listener(self, event: Any) -> None:
        logger.info("Worker %s: scheduler shutting down", os.getpid())
        self._cleanup()

    def _job_submitted_listener(self, event: Any) -> None:
        logger.info("Worker %s: job %s submitted", os.getpid(), event.job_id)

    def start(self) -> None:
        if not self.is_primary_worker():
            logger.info("Worker %s: not primary — scheduler not started", os.getpid())
            return
        if self._scheduler and not self._scheduler.running:
            try:
                self._scheduler.start()
                self._health_status["is_running"] = True
                logger.info("Worker %s: scheduler.start() OK", os.getpid())
            except Exception as e:
                msg = f"Worker {os.getpid()}: failed to start scheduler: {e}"
                logger.error(msg)
                self._health_status["last_error"] = msg
                self._cleanup()

    def shutdown(self, wait: bool = True) -> None:
        if not self.is_primary_worker():
            return
        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=wait)
                logger.info("Worker %s: scheduler shutdown", os.getpid())
            except Exception as e:
                logger.error("Worker %s: scheduler shutdown error: %s", os.getpid(), e)
            finally:
                self._cleanup()

    def add_job(self, *args: Any, **kwargs: Any) -> Optional[Job]:
        if not self._scheduler:
            logger.info(
                "Worker %s: cannot add job — not primary scheduler",
                os.getpid(),
            )
            return None
        try:
            kwargs.setdefault("replace_existing", True)
            job = self._scheduler.add_job(*args, **kwargs)
            logger.info("Worker %s: add_job OK id=%s", os.getpid(), kwargs.get("id", "?"))
            return job
        except Exception as e:
            msg = f"Worker {os.getpid()}: failed to add job: {e}"
            logger.error(msg)
            self._health_status["last_error"] = msg
            return None

    def get_all_jobs(self) -> Dict[str, Dict[str, Any]]:
        if not self._scheduler:
            return {}
        try:
            jobs: Dict[str, Dict[str, Any]] = {}
            for job in self._scheduler.get_jobs():
                jobs[job.id] = {
                    "next_run_time": job.next_run_time,
                    "trigger": str(job.trigger),
                    "function": job.func.__name__,
                }
            return jobs
        except Exception as e:
            logger.error("Worker %s: get_all_jobs error: %s", os.getpid(), e)
            return {}
