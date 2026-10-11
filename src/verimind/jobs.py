"""A job queue stored in Postgres.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`: each worker locks one queued row,
and other workers skip locked rows instead of waiting. Two workers can never take the same job,
and adding workers needs no coordination. A worker that dies mid-job stops sending heartbeats, and
`requeue_stale` hands its job to someone else.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from verimind.db.models import Job, JobStatus, utcnow


def enqueue(session: Session, run_id: int, kind: str) -> Job:
    job = Job(run_id=run_id, kind=kind)
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session, worker: str) -> Job | None:
    """Atomically take the oldest queued job, or return None if there is nothing to do."""
    job = session.scalars(
        select(Job)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    now = utcnow()
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.worker = worker
    job.started_at = job.heartbeat_at = now
    job.progress, job.progress_message = 0, "Starting"
    job.error = None
    session.commit()
    return job


def report_progress(session: Session, job_id: int, progress: int, message: str) -> None:
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(progress=max(0, min(progress, 100)), progress_message=message[:300], heartbeat_at=utcnow())
    )
    session.commit()


def finish(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.progress, job.progress_message = 100, "Done"
    job.finished_at = utcnow()


def fail(session: Session, job: Job, error: str, retryable: bool = True) -> bool:
    """Record a failure. Returns True if the job was put back in the queue for another attempt."""
    job.error = error[:5000]
    if retryable and job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.progress_message = f"Retrying after an error (attempt {job.attempts} of {job.max_attempts})"
        return True
    job.status = JobStatus.FAILED
    job.progress_message = "Failed"
    job.finished_at = utcnow()
    return False


def requeue_stale(session: Session, older_than: timedelta) -> int:
    """Put back jobs whose worker stopped sending heartbeats (it crashed or was killed)."""
    cutoff = utcnow() - older_than
    requeued = session.scalars(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.heartbeat_at < cutoff)
        .values(status=JobStatus.QUEUED, progress_message="Restarting: the previous worker stopped responding")
        .returning(Job.id)
    ).all()
    session.commit()
    return len(requeued)
