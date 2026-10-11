from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from verimind import jobs
from verimind.db.models import Job, JobStatus, utcnow
from verimind.services.runs import create_run


def _queue_two(sessions: sessionmaker[Session]) -> tuple[int, int]:
    with sessions() as s:
        run = create_run(s, "r")
        a, b = jobs.enqueue(s, run.id, "analyze_run"), jobs.enqueue(s, run.id, "analyze_run")
        s.commit()
        return a.id, b.id


def test_jobs_are_claimed_oldest_first_and_only_once(sessions: sessionmaker[Session]) -> None:
    first, second = _queue_two(sessions)
    with sessions() as s1, sessions() as s2, sessions() as s3:
        a = jobs.claim_next(s1, "w1")
        b = jobs.claim_next(s2, "w2")
        assert jobs.claim_next(s3, "w3") is None
    assert a is not None and b is not None
    assert (a.id, b.id) == (first, second)
    assert (a.status, a.attempts, a.worker) == (JobStatus.RUNNING, 1, "w1")


def test_locked_job_is_skipped_not_waited_for(sessions: sessionmaker[Session]) -> None:
    """SKIP LOCKED: while one worker holds the oldest job's row lock, another gets the next job."""
    first, second = _queue_two(sessions)
    with sessions() as holder, sessions() as other:
        locked = holder.scalars(select(Job).where(Job.id == first).with_for_update()).one()  # lock, don't commit
        claimed = jobs.claim_next(other, "w2")
        assert claimed is not None and claimed.id == second
        assert locked.status == JobStatus.QUEUED
        holder.rollback()


def test_failures_are_retried_then_marked_failed(sessions: sessionmaker[Session]) -> None:
    _queue_two(sessions)
    with sessions() as s:
        job = jobs.claim_next(s, "w")
        assert job is not None
        job.max_attempts = 2
        assert jobs.fail(s, job, "boom") is True and job.status == JobStatus.QUEUED
        s.commit()
        job = jobs.claim_next(s, "w")
        assert job is not None and job.attempts == 2
        assert jobs.fail(s, job, "boom again") is False
        assert (job.status, job.error) == (JobStatus.FAILED, "boom again")
        assert jobs.fail(s, job, "user error", retryable=False) is False


def test_jobs_of_dead_workers_are_requeued(sessions: sessionmaker[Session]) -> None:
    _queue_two(sessions)
    with sessions() as s:
        job = jobs.claim_next(s, "w")
        assert job is not None
        assert jobs.requeue_stale(s, timedelta(minutes=10)) == 0  # fresh heartbeat
        job.heartbeat_at = utcnow() - timedelta(hours=1)
        s.commit()
        assert jobs.requeue_stale(s, timedelta(minutes=10)) == 1
        s.refresh(job)
        assert job.status == JobStatus.QUEUED


def test_progress_updates_heartbeat(sessions: sessionmaker[Session]) -> None:
    _queue_two(sessions)
    with sessions() as s:
        job = jobs.claim_next(s, "w")
        assert job is not None
        before = job.heartbeat_at
        jobs.report_progress(s, job.id, 150, "Reading logs")
        s.refresh(job)
        assert (job.progress, job.progress_message) == (100, "Reading logs")
        assert before is not None and job.heartbeat_at is not None and job.heartbeat_at >= before
