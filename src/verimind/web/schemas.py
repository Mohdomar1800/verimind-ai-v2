"""Request and response models for the JSON API (these also generate the docs at /api/docs)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from verimind.db.models import TriageStatus

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class JobOut(ORMModel):
    id: int
    status: str
    progress: int = Field(description="0-100")
    progress_message: str
    attempts: int
    error: str | None


class RunOut(ORMModel):
    id: int
    name: str
    source: str
    status: str = Field(description="queued | processing | done | failed")
    error: str | None
    created_at: datetime
    analyzed_at: datetime | None
    file_count: int
    size_bytes: int
    total_tests: int
    passed: int
    failed: int
    unknown: int
    total_lines: int
    error_lines: int
    issue_count: int
    formats: dict[str, Any]
    by_category: dict[str, Any]
    analysis_seconds: float
    job: JobOut | None = None


class RunCreated(BaseModel):
    run: RunOut
    message: str


class Page(BaseModel, Generic[T]):
    total: int
    items: list[T]


class TriageOut(BaseModel):
    status: str = TriageStatus.NEW
    owner: str | None = None
    note: str = ""
    ticket: str | None = None
    updated_at: datetime | None = None


class TriageIn(BaseModel):
    status: TriageStatus
    owner: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=10_000)
    ticket: str | None = Field(default=None, max_length=300)


class IssueOut(ORMModel):
    id: int
    signature_id: str
    rank: int
    title: str
    kind: str
    msg_id: str | None
    location: str | None
    category: str
    category_reason: str
    severity: str
    score: float
    reasons: list[str]
    test_count: int
    occurrences: int
    modules: dict[str, int]
    example_message: str
    followed_by: list[str]
    history: str = Field(description="first_run | new | recurring | regression | known | ignored")
    triage: TriageOut = Field(default_factory=TriageOut)


class TestOut(ORMModel):
    __test__ = False  # stop pytest from treating this as a test class

    id: int
    test_name: str
    seed: int | None
    path: str
    format: str
    status: str
    status_reason: str
    module: str
    total_lines: int
    error_count: int
    follow_on_errors: int
    issue_id: int | None
    root_line: int | None
    root_end_line: int | None
    root_message: str | None
    root_kind: str | None
    root_severity: str | None
    root_location: str | None


class IssueDetailOut(IssueOut):
    tests: list[TestOut]


class LogLine(BaseModel):
    n: int
    text: str


class LogWindowOut(BaseModel):
    test_id: int
    path: str
    start: int
    end: int
    total_lines: int
    root_line: int | None
    root_end_line: int | None
    error_lines: list[int]
    lines: list[LogLine]


class ModuleOut(BaseModel):
    module: str
    tests: int
    failed: int
    issues: int
    pass_rate: float


class ComparedIssueOut(BaseModel):
    signature_id: str
    title: str
    category: str
    change: Literal["new", "recurring", "resolved"]
    base_tests: int
    head_tests: int
    base_issue_id: int | None
    head_issue_id: int | None
    triage: TriageOut = Field(default_factory=TriageOut)


class CompareOut(BaseModel):
    base: RunOut
    head: RunOut
    new: int
    recurring: int
    resolved: int
    issues: list[ComparedIssueOut]
