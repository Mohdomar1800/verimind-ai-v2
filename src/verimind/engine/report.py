"""The result of analyzing one regression run: a summary, ranked issues, and per-test results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from verimind.engine.classify import Category


@dataclass
class RootCause:
    line: int
    end_line: int
    message: str
    kind: str
    severity: str
    msg_id: str | None
    source: str | None
    location: str | None
    time_ps: int | None


@dataclass
class TestResult:
    __test__ = False  # stop pytest from treating this as a test class

    test_name: str
    seed: int | None
    path: str  # relative to the analyzed folder, forward slashes
    format: str
    status: str
    status_reason: str
    module: str
    total_lines: int
    error_count: int
    follow_on_errors: int  # errors after the root cause, usually consequences of it
    root_cause: RootCause | None = None
    issue_id: str | None = None


@dataclass
class IssueTest:
    test_name: str
    seed: int | None
    path: str
    line: int | None


@dataclass
class Issue:
    rank: int
    issue_id: str
    title: str
    kind: str
    msg_id: str | None
    location: str | None
    category: Category
    category_reason: str
    severity: str
    score: float  # 0-100, relative to the top issue in this run
    score_parts: dict[str, float]
    reasons: list[str]  # plain-language "why is this ranked here"
    test_count: int
    occurrences: int  # times this signature appears anywhere, including as a follow-on error
    modules: dict[str, int]
    example_message: str
    tests: list[IssueTest] = field(default_factory=list)
    followed_by: list[str] = field(default_factory=list)  # most common follow-on errors in these tests
    history: str = ""  # set by the service layer from earlier runs (new / recurring / regression / ...)


@dataclass
class RunSummary:
    name: str
    total_tests: int
    passed: int
    failed: int
    unknown: int
    formats: dict[str, int]
    total_lines: int
    error_lines: int  # error events in failing logs, i.e. what an engineer would otherwise read
    issue_count: int
    by_category: dict[str, int]
    seconds: float


@dataclass
class AnalysisReport:
    summary: RunSummary
    issues: list[Issue]
    tests: list[TestResult]

    def issue(self, issue_id: str) -> Issue | None:
        return next((i for i in self.issues if i.issue_id == issue_id), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        s = self.summary
        out = [
            f"# VeriMind report: {s.name}",
            "",
            f"- **Tests:** {s.total_tests:,} ({s.passed:,} passed, {s.failed:,} failed, {s.unknown:,} unclear)",
            f"- **Distinct issues:** {s.issue_count} (from {s.error_lines:,} error lines in failing logs)",
            "- **By category:** " + ", ".join(f"{k} {v}" for k, v in s.by_category.items()),
            "",
            "| # | Score | Tests | Category | Issue |",
            "|---|---|---|---|---|",
        ]
        for i in self.issues:
            out.append(f"| {i.rank} | {i.score:.0f} | {i.test_count} | {i.category} | {_md(i.title)} |")
        for i in self.issues:
            out += ["", f"## {i.rank}. {_md(i.title)}", ""]
            out += [f"- {r}" for r in i.reasons]
            if i.location:
                out.append(f"- Reported from `{i.location}`")
            out += ["", f"Example: `{_md(i.example_message)}`", "", "Affected tests:", ""]
            out += [f"- {t.test_name} (seed {t.seed}), `{t.path}` line {t.line}" for t in i.tests[:10]]
            if len(i.tests) > 10:
                out.append(f"- ... and {len(i.tests) - 10} more")
        return "\n".join(out) + "\n"


def _md(text: str) -> str:
    return text.replace("|", "\\|")
