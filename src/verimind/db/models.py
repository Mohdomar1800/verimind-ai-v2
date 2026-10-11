"""Database tables (SQLAlchemy 2.0 ORM).

    runs ──< jobs             a run is analyzed by a background job
    runs ──< issues ──< test_results
    runs ──< test_results     every test in the run, passing or failing

Deleting a run deletes everything that belongs to it (ON DELETE CASCADE). Schema changes go
through Alembic migrations in `verimind/db/migrations/`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class RunStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Run(Base):
    """One regression run: a set of uploaded log files and, once analyzed, its results."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | demo | import
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Upload facts.
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    # Summary of the analysis (copied from the engine's RunSummary).
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    unknown: Mapped[int] = mapped_column(Integer, default=0)
    total_lines: Mapped[int] = mapped_column(BigInteger, default=0)
    error_lines: Mapped[int] = mapped_column(Integer, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    formats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    by_category: Mapped[dict[str, Any]] = mapped_column(default=dict)
    analysis_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    # Coverage prioritization results, when the upload included a coverage.json (see intelligence/coverage.py).
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="run", passive_deletes=True)
    issues: Mapped[list[Issue]] = relationship(back_populates="run", passive_deletes=True, order_by="Issue.rank")
    tests: Mapped[list[TestResult]] = relationship(back_populates="run", passive_deletes=True)


class Job(Base):
    """A unit of background work. Workers claim jobs with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_queue", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    progress_message: Mapped[str] = mapped_column(String(300), default="Waiting to start")
    error: Mapped[str | None] = mapped_column(Text)
    worker: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="jobs")


class Issue(Base):
    """A distinct problem in one run: failing tests that share a root-cause signature."""

    __tablename__ = "issues"
    __table_args__ = (Index("ix_issues_run_rank", "run_id", "rank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    signature_id: Mapped[str] = mapped_column(String(40), index=True)  # same problem -> same id in every run
    rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    msg_id: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(20))
    category_reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float)
    score_parts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    test_count: Mapped[int] = mapped_column(Integer)
    occurrences: Mapped[int] = mapped_column(Integer)
    modules: Mapped[dict[str, Any]] = mapped_column(default=dict)
    example_message: Mapped[str] = mapped_column(Text)
    followed_by: Mapped[list[Any]] = mapped_column(default=list)
    # How this problem relates to earlier runs: first_run | new | recurring | regression | known | ignored
    history: Mapped[str] = mapped_column(String(20), default="", server_default="")

    run: Mapped[Run] = relationship(back_populates="issues")
    tests: Mapped[list[TestResult]] = relationship(back_populates="issue")


class TestResult(Base):
    """One test (one log file) in a run, with its root cause if it failed."""

    __test__ = False  # stop pytest from treating this as a test class
    __tablename__ = "test_results"
    __table_args__ = (Index("ix_test_results_run_status", "run_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), index=True)
    test_name: Mapped[str] = mapped_column(String(300))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    path: Mapped[str] = mapped_column(String(1000))  # relative to the run's log folder
    format: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10))
    status_reason: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(String(200), index=True)
    total_lines: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    follow_on_errors: Mapped[int] = mapped_column(Integer)
    root_line: Mapped[int | None] = mapped_column(Integer)
    root_end_line: Mapped[int | None] = mapped_column(Integer)
    root_message: Mapped[str | None] = mapped_column(Text)
    root_kind: Mapped[str | None] = mapped_column(String(20))
    root_severity: Mapped[str | None] = mapped_column(String(10))
    root_msg_id: Mapped[str | None] = mapped_column(String(200))
    root_source: Mapped[str | None] = mapped_column(String(500))
    root_location: Mapped[str | None] = mapped_column(String(500))
    root_time_ps: Mapped[int | None] = mapped_column(BigInteger)

    run: Mapped[Run] = relationship(back_populates="tests")
    issue: Mapped[Issue | None] = relationship(back_populates="tests")


class TriageStatus(StrEnum):
    NEW = "new"
    INVESTIGATING = "investigating"
    KNOWN = "known"  # understood, tracked elsewhere (e.g. a bug ticket)
    FIXED = "fixed"
    IGNORED = "ignored"  # not worth acting on (noise, expected failure)


class Triage(Base):
    """An engineer's notes on a problem. Keyed by signature, so it follows the issue into every run."""

    __tablename__ = "triage"

    signature_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default=TriageStatus.NEW)
    owner: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="")
    ticket: Mapped[str | None] = mapped_column(String(300))  # link or ID in the team's bug tracker
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AiSummary(Base):
    """The latest Claude-written explanation of a problem (optional feature). Keyed by signature."""

    __tablename__ = "ai_summaries"

    signature_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))  # evidence came from here
    model: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
