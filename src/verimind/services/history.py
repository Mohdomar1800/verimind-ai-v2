"""History-aware ranking and the problem library.

The engine ranks one run in isolation. Here the ranking is adjusted with what the team already knows:

    new (never seen in an earlier run)        x1.5  - something recently changed; look first
    regression (triaged "fixed", but back)     x2.0  - the fix didn't hold
    known issue (triaged "known")              x0.5  - tracked elsewhere; let new problems surface
    ignored (triaged "ignore")                 x0.2
    recurring / first run of the project       x1.0

Each adjustment is added to the problem's reasons, so the ranking stays explainable. Triage changes
take effect the next time a run is analyzed ("Analyze again" re-ranks an existing run).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from verimind.db.models import Issue, Run, RunStatus, Triage, TriageStatus
from verimind.engine.report import AnalysisReport
from verimind.engine.report import Issue as ReportIssue
from verimind.services.results import triage_map

FACTORS = {"new": 1.5, "regression": 2.0, "known": 0.5, "ignored": 0.2, "recurring": 1.0, "first_run": 1.0}


def _earlier_runs(session: Session, run: Run) -> list[int]:
    query = select(Run.id).where(Run.status == RunStatus.DONE, Run.id != run.id, Run.created_at < run.created_at)
    return list(session.scalars(query))


def _priority(issue: ReportIssue) -> float:
    parts = issue.score_parts
    return float(parts["tests"] * parts["category_weight"] * parts["history_factor"])


def apply_history(session: Session, run: Run, report: AnalysisReport) -> None:
    """Label each issue (new / recurring / regression / known / ignored) and re-rank the report."""
    if not report.issues:
        return
    signatures = [i.issue_id for i in report.issues]
    earlier = _earlier_runs(session, run)
    seen_before = set(
        session.scalars(
            select(Issue.signature_id).where(Issue.run_id.in_(earlier), Issue.signature_id.in_(signatures))
        )
    ) if earlier else set()  # fmt: skip
    triage = triage_map(session, signatures)

    for issue in report.issues:
        t = triage.get(issue.issue_id)
        ticket = f" ({t.ticket})" if t and t.ticket else ""
        if t and t.status == TriageStatus.FIXED:
            label, reason = "regression", f"Regression: this was marked fixed{ticket}, but it is back."
        elif t and t.status == TriageStatus.KNOWN:
            label, reason = "known", f"Known issue{ticket}: ranked lower so new problems surface first."
        elif t and t.status == TriageStatus.IGNORED:
            label, reason = "ignored", "Marked as 'ignore' during triage."
        elif not earlier:
            label, reason = "first_run", ""
        elif issue.issue_id in seen_before:
            label, reason = "recurring", ""
        else:
            label, reason = "new", "New: not seen in any earlier run, so something probably changed recently."
        issue.history = label
        issue.score_parts["history_factor"] = FACTORS[label]
        if reason:
            issue.reasons.insert(1, reason)

    ordered = sorted(report.issues, key=lambda i: (-_priority(i), i.rank))
    top = _priority(ordered[0]) or 1.0
    for rank, issue in enumerate(ordered, start=1):
        issue.rank = rank
        issue.score = round(100 * _priority(issue) / top, 1)
    report.issues[:] = ordered


@dataclass(frozen=True)
class LibraryEntry:
    signature_id: str
    title: str
    category: str
    runs_seen: int
    total_tests: int
    first_seen: datetime
    last_seen: datetime
    last_run_id: int
    last_issue_id: int
    triage: Triage | None


def problem_library(session: Session) -> list[LibraryEntry]:
    """Every problem ever seen, with how often and when, plus its triage. Newest activity first."""
    stats = (
        select(
            Issue.signature_id,
            func.count(func.distinct(Issue.run_id)).label("runs_seen"),
            func.sum(Issue.test_count).label("total_tests"),
            func.min(Run.created_at).label("first_seen"),
            func.max(Run.created_at).label("last_seen"),
        )
        .join(Run, Run.id == Issue.run_id)
        .where(Run.status == RunStatus.DONE)
        .group_by(Issue.signature_id)
        .subquery()
    )
    # The most recent occurrence of each signature supplies the title, category and link.
    newest_first = func.row_number().over(
        partition_by=Issue.signature_id, order_by=(Run.created_at.desc(), Run.id.desc())
    )
    ranked = (
        select(Issue.signature_id, Issue.title, Issue.category, Issue.run_id, Issue.id, newest_first.label("n"))
        .join(Run, Run.id == Issue.run_id)
        .where(Run.status == RunStatus.DONE)
        .subquery()
    )
    latest = select(ranked).where(ranked.c.n == 1).subquery()
    rows = session.execute(
        select(stats, latest.c.title, latest.c.category, latest.c.run_id, latest.c.id)
        .join(latest, latest.c.signature_id == stats.c.signature_id)
        .order_by(stats.c.last_seen.desc(), stats.c.total_tests.desc())
    ).all()
    triage = triage_map(session, [r.signature_id for r in rows])
    return [
        LibraryEntry(
            signature_id=r.signature_id,
            title=r.title,
            category=r.category,
            runs_seen=int(r.runs_seen),
            total_tests=int(r.total_tests or 0),
            first_seen=r.first_seen,
            last_seen=r.last_seen,
            last_run_id=r.run_id,
            last_issue_id=r.id,
            triage=triage.get(r.signature_id),
        )
        for r in rows
    ]
