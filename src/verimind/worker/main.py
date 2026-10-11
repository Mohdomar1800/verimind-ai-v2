"""Background worker: takes analysis jobs from the queue and runs them.

Long-running analysis (parsing gigabytes of logs) must not happen inside a web request. The web
app records a job; this separate process does the work. Several workers can run at once
(`docker compose up --scale worker=3`); the queue guarantees each job goes to exactly one of them.

Transactions are kept short. Claiming a job commits immediately, the analysis runs without any
open transaction, and progress updates and the final results each commit separately. That way
the web app can show live progress, and a crash never leaves rows locked.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from collections.abc import Callable
from datetime import timedelta
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from verimind import jobs
from verimind.config import get_settings
from verimind.db.models import Job, Run, RunStatus
from verimind.db.session import check_database, session_factory
from verimind.engine import Analyzer, FormatError, FormatRegistry, LogParser
from verimind.logging_setup import configure_logging
from verimind.services.coverage import coverage_for_run
from verimind.services.history import apply_history
from verimind.services.runs import ANALYZE_RUN, mark_failed, save_report
from verimind.storage import Storage, StorageError, default_storage

log = logging.getLogger("verimind.worker")

PROGRESS_INTERVAL_SECONDS = 1.0

# Errors the user can act on: shown as-is and not retried, because retrying won't help.
USER_ERRORS = (StorageError, FormatError, FileNotFoundError)


class Worker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: Storage,
        analyzer_factory: Callable[[], Analyzer],
        poll_interval: float = 2.0,
        stale_after: timedelta = timedelta(minutes=10),
        name: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.analyzer_factory = analyzer_factory
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self._running = True
        self.handlers: dict[str, Callable[[Job], None]] = {ANALYZE_RUN: self._analyze_run}

    # -- loop ------------------------------------------------------------------------------------

    def stop(self, signum: int | None = None, _frame: FrameType | None = None) -> None:
        log.info("stopping after the current job (signal %s)", signum)
        self._running = False

    def run(self) -> None:
        while self._running and not check_database():
            log.warning("database not reachable yet, retrying in %.0fs", self.poll_interval)
            time.sleep(self.poll_interval)
        log.info("worker %s ready", self.name)
        last_stale_check = 0.0
        while self._running:
            if time.monotonic() - last_stale_check > 60:
                with self.sessions() as session:
                    if n := jobs.requeue_stale(session, self.stale_after):
                        log.warning("requeued %d job(s) abandoned by a dead worker", n)
                last_stale_check = time.monotonic()
            if not self.run_once():
                time.sleep(self.poll_interval)
        log.info("worker stopped")

    def run_once(self) -> bool:
        """Process at most one job. Returns True if a job was processed."""
        with self.sessions() as session:
            job = jobs.claim_next(session, self.name)
        if job is None:
            return False
        log.info("job %d (%s) for run %d: started (attempt %d)", job.id, job.kind, job.run_id, job.attempts)
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                raise ValueError(f"unknown job kind '{job.kind}'")
            handler(job)
        except USER_ERRORS as exc:
            self._record_failure(job, str(exc), retryable=False)
        except Exception as exc:
            log.exception("job %d failed", job.id)
            self._record_failure(job, f"Unexpected error: {exc}", retryable=True)
        return True

    # -- handlers --------------------------------------------------------------------------------

    def _analyze_run(self, job: Job) -> None:
        with self.sessions() as session:
            run = session.get(Run, job.run_id)
            if run is None:
                raise FileNotFoundError(f"Run {job.run_id} no longer exists.")
            run.status = RunStatus.PROCESSING
            session.commit()
            run_name = run.name

        logs = self.storage.logs_dir(job.run_id)
        if not logs.is_dir():
            raise FileNotFoundError("The uploaded log files for this run are missing.")
        report = self.analyzer_factory().analyze_dir(logs, name=run_name, progress=self._progress_reporter(job.id))

        with self.sessions() as session:
            run = session.get(Run, job.run_id)
            claimed = session.get(Job, job.id)
            if run is None or claimed is None:
                return  # the run was deleted while it was being analyzed
            apply_history(session, run, report)
            save_report(session, run, report)
            run.coverage = coverage_for_run(self.storage, job.run_id, report)
            jobs.finish(session, claimed)
            session.commit()
        log.info("job %d done: %d tests, %d issues", job.id, report.summary.total_tests, report.summary.issue_count)

    def _progress_reporter(self, job_id: int) -> Callable[[int, int], None]:
        last = 0.0

        def report(done: int, total: int) -> None:
            nonlocal last
            now = time.monotonic()
            if done < total and now - last < PROGRESS_INTERVAL_SECONDS:
                return  # throttle database writes
            last = now
            with self.sessions() as session:
                # Parsing is ~95% of the work; the last 5% is grouping, ranking and saving.
                jobs.report_progress(
                    session, job_id, int(95 * done / max(total, 1)), f"Reading logs: {done:,} of {total:,}"
                )

        return report

    def _record_failure(self, job: Job, message: str, retryable: bool) -> None:
        with self.sessions() as session:
            claimed = session.get(Job, job.id)
            if claimed is None:
                return
            will_retry = jobs.fail(session, claimed, message, retryable)
            run = session.get(Run, job.run_id)
            if run is not None:
                if will_retry:
                    run.status = RunStatus.QUEUED
                else:
                    mark_failed(run, message)
            session.commit()
        log.warning("job %d failed%s: %s", job.id, " (will retry)" if will_retry else "", message)


def build_worker() -> Worker:
    settings = get_settings()
    formats_dir = settings.formats_dir

    def analyzer_factory() -> Analyzer:
        return Analyzer(LogParser(FormatRegistry.load([formats_dir] if formats_dir else None)))

    return Worker(
        sessions=session_factory(),
        storage=default_storage(),
        analyzer_factory=analyzer_factory,
        poll_interval=settings.worker_poll_interval,
        stale_after=timedelta(seconds=settings.worker_stale_seconds),
    )


def main() -> None:
    configure_logging(get_settings().log_level)
    worker = build_worker()
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
