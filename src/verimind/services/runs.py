"""Operations on regression runs, shared by the worker, the CLI, and (Phase 5) the web API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from verimind import jobs
from verimind.db.models import Issue, Job, Run, RunStatus, TestResult, utcnow
from verimind.engine.report import AnalysisReport
from verimind.storage import SavedFiles, Storage

ANALYZE_RUN = "analyze_run"


def create_run(session: Session, name: str, source: str = "upload") -> Run:
    run = Run(name=name.strip()[:200] or "Untitled run", source=source, status=RunStatus.QUEUED)
    session.add(run)
    session.flush()  # assigns run.id, needed for the storage folder
    return run


def default_run_name(filenames: list[str]) -> str:
    """'nightly_oct10.zip' -> 'nightly_oct10 (Oct 10 22:15)'; several files -> 'Upload of 3 files (...)'."""
    stamp = datetime.now(UTC).strftime("%b %d %H:%M")
    if len(filenames) == 1 and filenames[0]:
        return f"{PurePosixPath(filenames[0].replace(chr(92), '/')).name.split('.')[0]} ({stamp})"
    return f"Upload of {len(filenames)} files ({stamp})"


def add_run(session: Session, storage: Storage, name: str, source: str, save_files: Callable[[int], SavedFiles]) -> Run:
    """Create a run, store its files with `save_files(run_id)`, and queue it for analysis.

    If saving fails, the partly written files are removed. The caller's transaction rollback then
    removes the run row, so nothing is left half-created.
    """
    run = create_run(session, name, source)
    try:
        saved = save_files(run.id)
    except BaseException:
        storage.delete_run(run.id)
        raise
    run.file_count, run.size_bytes = saved.count, saved.size_bytes
    queue_analysis(session, run)
    return run


def queue_analysis(session: Session, run: Run) -> Job:
    run.status = RunStatus.QUEUED
    run.error = None
    return jobs.enqueue(session, run.id, ANALYZE_RUN)


def latest_job(session: Session, run_id: int) -> Job | None:
    return session.scalars(select(Job).where(Job.run_id == run_id).order_by(Job.id.desc()).limit(1)).first()


def list_runs(session: Session, limit: int = 100, offset: int = 0) -> list[Run]:
    query = select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(query))


def save_report(session: Session, run: Run, report: AnalysisReport) -> None:
    """Store an analysis, replacing any previous results for the run (re-analysis is safe)."""
    session.execute(delete(TestResult).where(TestResult.run_id == run.id))
    session.execute(delete(Issue).where(Issue.run_id == run.id))

    s = report.summary
    run.total_tests, run.passed, run.failed, run.unknown = s.total_tests, s.passed, s.failed, s.unknown
    run.total_lines, run.error_lines, run.issue_count = s.total_lines, s.error_lines, s.issue_count
    run.formats, run.by_category, run.analysis_seconds = s.formats, s.by_category, s.seconds
    run.status, run.error, run.analyzed_at = RunStatus.DONE, None, utcnow()

    issue_rows: dict[str, Issue] = {}
    for i in report.issues:
        row = Issue(
            run_id=run.id,
            signature_id=i.issue_id,
            rank=i.rank,
            title=i.title,
            kind=i.kind,
            msg_id=i.msg_id,
            location=i.location,
            category=i.category,
            category_reason=i.category_reason,
            severity=i.severity,
            score=i.score,
            score_parts=i.score_parts,
            reasons=i.reasons,
            test_count=i.test_count,
            occurrences=i.occurrences,
            modules=i.modules,
            example_message=i.example_message,
            followed_by=i.followed_by,
            history=i.history,
        )
        issue_rows[i.issue_id] = row
    session.add_all(issue_rows.values())
    session.flush()  # assigns issue ids for the test rows below

    rows = []
    for t in report.tests:
        rc = t.root_cause
        rows.append(
            TestResult(
                run_id=run.id,
                issue_id=issue_rows[t.issue_id].id if t.issue_id else None,
                test_name=t.test_name,
                seed=t.seed,
                path=t.path,
                format=t.format,
                status=t.status,
                status_reason=t.status_reason,
                module=t.module,
                total_lines=t.total_lines,
                error_count=t.error_count,
                follow_on_errors=t.follow_on_errors,
                root_line=rc.line if rc else None,
                root_end_line=rc.end_line if rc else None,
                root_message=rc.message if rc else None,
                root_kind=rc.kind if rc else None,
                root_severity=rc.severity if rc else None,
                root_msg_id=rc.msg_id if rc else None,
                root_source=rc.source if rc else None,
                root_location=rc.location if rc else None,
                root_time_ps=rc.time_ps if rc else None,
            )
        )
    session.add_all(rows)


def mark_failed(run: Run, message: str) -> None:
    run.status = RunStatus.FAILED
    run.error = message
