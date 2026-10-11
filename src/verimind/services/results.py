"""Read-side queries over analysis results, shared by the JSON API and the HTML pages."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from itertools import islice
from typing import Literal

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from verimind.db.models import Issue, Run, RunStatus, TestResult, Triage, TriageStatus, utcnow
from verimind.engine import report as rpt
from verimind.engine.parser import LogParser, open_log
from verimind.storage import Storage

MAX_LOG_WINDOW = 2000  # lines per request
MAX_LINE_CHARS = 2000

# -- triage ----------------------------------------------------------------------------------------


def triage_map(session: Session, signature_ids: list[str]) -> dict[str, Triage]:
    if not signature_ids:
        return {}
    rows = session.scalars(select(Triage).where(Triage.signature_id.in_(signature_ids)))
    return {t.signature_id: t for t in rows}


def save_triage(
    session: Session, signature_id: str, status: str, owner: str | None, note: str, ticket: str | None
) -> Triage:
    triage = session.get(Triage, signature_id) or Triage(signature_id=signature_id)
    triage.status, triage.owner, triage.note, triage.ticket = status, owner, note, ticket
    triage.updated_at = utcnow()
    session.add(triage)
    session.flush()
    return triage


# -- issues and tests -------------------------------------------------------------------------------


def run_issues(session: Session, run_id: int) -> list[Issue]:
    return list(session.scalars(select(Issue).where(Issue.run_id == run_id).order_by(Issue.rank)))


def get_issue(session: Session, run_id: int, issue_id: int) -> Issue | None:
    issue = session.get(Issue, issue_id)
    return issue if issue is not None and issue.run_id == run_id else None


@dataclass
class TestFilter:
    __test__ = False  # stop pytest from treating this as a test class

    status: Literal["pass", "fail", "unknown"] | None = None
    module: str | None = None
    issue_id: int | None = None
    search: str | None = None


def run_tests(
    session: Session, run_id: int, flt: TestFilter, limit: int = 100, offset: int = 0
) -> tuple[int, list[TestResult]]:
    query = select(TestResult).where(TestResult.run_id == run_id)
    if flt.status:
        query = query.where(TestResult.status == flt.status)
    if flt.module:
        query = query.where(TestResult.module == flt.module)
    if flt.issue_id is not None:
        query = query.where(TestResult.issue_id == flt.issue_id)
    if flt.search:
        query = query.where(TestResult.test_name.ilike(f"%{flt.search}%"))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    # Failing tests first, then by name, so the interesting ones are on page one.
    order = case((TestResult.status == "fail", 0), (TestResult.status == "unknown", 1), else_=2)
    rows = session.scalars(query.order_by(order, TestResult.test_name, TestResult.id).limit(limit).offset(offset))
    return total, list(rows)


def get_test(session: Session, run_id: int, test_id: int) -> TestResult | None:
    test = session.get(TestResult, test_id)
    return test if test is not None and test.run_id == run_id else None


@dataclass(frozen=True)
class ModuleStats:
    module: str
    tests: int
    failed: int
    issues: int

    @property
    def pass_rate(self) -> float:
        return 1 - self.failed / self.tests if self.tests else 1.0


def module_stats(session: Session, run_id: int) -> list[ModuleStats]:
    failed = func.sum(case((TestResult.status == "fail", 1), else_=0)).cast(Integer)
    rows = session.execute(
        select(TestResult.module, func.count(), failed, func.count(func.distinct(TestResult.issue_id)))
        .where(TestResult.run_id == run_id)
        .group_by(TestResult.module)
        .order_by(failed.desc(), TestResult.module)
    )
    return [ModuleStats(m, int(n), int(f or 0), int(i)) for m, n, f, i in rows]


# -- log viewer --------------------------------------------------------------------------------------


@dataclass
class LogWindow:
    start: int
    end: int
    total_lines: int
    lines: list[tuple[int, str]]
    error_lines: list[int] = field(default_factory=list)


def read_log_window(
    storage: Storage, parser: LogParser, test: TestResult, around: int | None, start: int | None, count: int
) -> LogWindow:
    """A slice of a stored log. `around` centers the window on a line (e.g. the root cause)."""
    path = storage.log_path(test.run_id, test.path)
    count = max(1, min(count, MAX_LOG_WINDOW))
    if start is None:
        center = around or test.root_line or 1
        start = max(1, center - count // 3)
    with open_log(path) as stream:
        window = [
            (n, line.rstrip("\r\n")[:MAX_LINE_CHARS])
            for n, line in enumerate(islice(stream, start - 1, start - 1 + count), start=start)
        ]
    # Re-parse to list every error line, so the viewer can offer "jump to next error".
    errors = [e.line for e in parser.parse_file(path).errors]
    end = window[-1][0] if window else start
    return LogWindow(start=start, end=end, total_lines=test.total_lines, lines=window, error_lines=errors)


# -- run comparison --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparedIssue:
    signature_id: str
    title: str
    category: str
    change: Literal["new", "recurring", "resolved"]
    base_tests: int
    head_tests: int
    head_issue_id: int | None
    base_issue_id: int | None


def compare_runs(session: Session, base: Run, head: Run) -> list[ComparedIssue]:
    """Match issues by signature: same problem -> same signature in both runs."""
    base_by_sig = {i.signature_id: i for i in run_issues(session, base.id)}
    head_by_sig = {i.signature_id: i for i in run_issues(session, head.id)}
    out: list[ComparedIssue] = []
    for sig, h in head_by_sig.items():
        b = base_by_sig.get(sig)
        change: Literal["new", "recurring"] = "recurring" if b else "new"
        out.append(
            ComparedIssue(
                sig, h.title, h.category, change, b.test_count if b else 0, h.test_count, h.id, b.id if b else None
            )
        )
    for sig, b in base_by_sig.items():
        if sig not in head_by_sig:
            out.append(ComparedIssue(sig, b.title, b.category, "resolved", b.test_count, 0, None, b.id))
    order = {"new": 0, "recurring": 1, "resolved": 2}
    out.sort(key=lambda c: (order[c.change], -max(c.head_tests, c.base_tests)))
    return out


# -- export ----------------------------------------------------------------------------------------


def report_from_db(session: Session, run: Run) -> rpt.AnalysisReport:
    """Rebuild the engine's report object from stored rows, so exports reuse the engine's formatting."""
    issues = run_issues(session, run.id)
    tests = list(session.scalars(select(TestResult).where(TestResult.run_id == run.id).order_by(TestResult.id)))
    sig_of = {i.id: i.signature_id for i in issues}
    by_issue: dict[int, list[TestResult]] = {}
    for t in tests:
        if t.issue_id is not None:
            by_issue.setdefault(t.issue_id, []).append(t)

    summary = rpt.RunSummary(
        name=run.name,
        total_tests=run.total_tests,
        passed=run.passed,
        failed=run.failed,
        unknown=run.unknown,
        formats=run.formats,
        total_lines=run.total_lines,
        error_lines=run.error_lines,
        issue_count=run.issue_count,
        by_category=run.by_category,
        seconds=run.analysis_seconds,
    )
    report_issues = [
        rpt.Issue(
            rank=i.rank,
            issue_id=i.signature_id,
            title=i.title,
            kind=i.kind,
            msg_id=i.msg_id,
            location=i.location,
            category=i.category,  # type: ignore[arg-type]
            category_reason=i.category_reason,
            severity=i.severity,
            score=i.score,
            score_parts=i.score_parts,
            reasons=list(i.reasons),
            test_count=i.test_count,
            occurrences=i.occurrences,
            modules=i.modules,
            example_message=i.example_message,
            tests=[rpt.IssueTest(t.test_name, t.seed, t.path, t.root_line) for t in by_issue.get(i.id, [])],
            followed_by=list(i.followed_by),
            history=i.history,
        )
        for i in issues
    ]
    report_tests = [
        rpt.TestResult(
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
            root_cause=rpt.RootCause(
                t.root_line,
                t.root_end_line or t.root_line,
                t.root_message or "",
                t.root_kind or "",
                t.root_severity or "",
                t.root_msg_id,
                t.root_source,
                t.root_location,
                t.root_time_ps,
            )
            if t.root_line is not None
            else None,
            issue_id=sig_of.get(t.issue_id) if t.issue_id is not None else None,
        )
        for t in tests
    ]
    return rpt.AnalysisReport(summary, report_issues, report_tests)


def issues_csv(session: Session, run: Run) -> str:
    issues = run_issues(session, run.id)
    triage = triage_map(session, [i.signature_id for i in issues])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "rank",
            "score",
            "tests",
            "category",
            "severity",
            "title",
            "location",
            "triage_status",
            "owner",
            "signature_id",
        ]
    )
    for i in issues:
        t = triage.get(i.signature_id)
        writer.writerow(
            [
                i.rank,
                i.score,
                i.test_count,
                i.category,
                i.severity,
                i.title,
                i.location or "",
                t.status if t else TriageStatus.NEW,
                (t.owner or "") if t else "",
                i.signature_id,
            ]
        )
    return buf.getvalue()


def signature_history(session: Session, signature_id: str, limit: int = 20) -> list[tuple[Run, Issue]]:
    """Every run in which this problem appeared, newest first ("seen before?" on the issue page)."""
    rows = session.execute(
        select(Run, Issue)
        .join(Issue, Issue.run_id == Run.id)
        .where(Issue.signature_id == signature_id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(limit)
    )
    return [(run, issue) for run, issue in rows]


def default_comparison(session: Session) -> tuple[Run | None, Run | None]:
    """The two most recent analyzed runs (older, newer), used when the compare page has no selection."""
    done = list(
        session.scalars(
            select(Run).where(Run.status == RunStatus.DONE).order_by(Run.created_at.desc(), Run.id.desc()).limit(2)
        )
    )
    if len(done) < 2:
        return None, done[0] if done else None
    return done[1], done[0]
