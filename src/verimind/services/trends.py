"""Trends across runs: is the regression getting healthier, and where are failures concentrated?"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from verimind.db.models import Issue, Run, RunStatus, TestResult


@dataclass(frozen=True)
class RunPoint:
    run: Run
    new_issues: int  # new problems or regressions in this run


@dataclass
class Trend:
    points: list[RunPoint]  # oldest first
    modules: list[str] = field(default_factory=list)  # most failures first
    cells: dict[tuple[str, int], tuple[int, int]] = field(default_factory=dict)  # (module, run_id) -> (tests, failed)

    @property
    def max_failed(self) -> int:
        return max((p.run.failed for p in self.points), default=0)


def run_trend(session: Session, limit: int = 20) -> Trend:
    runs = list(
        session.scalars(
            select(Run).where(Run.status == RunStatus.DONE).order_by(Run.created_at.desc(), Run.id.desc()).limit(limit)
        )
    )[::-1]
    if not runs:
        return Trend(points=[])
    ids = [r.id for r in runs]

    new_counts = dict(
        session.execute(
            select(Issue.run_id, func.count())
            .where(Issue.run_id.in_(ids), Issue.history.in_(("new", "regression")))
            .group_by(Issue.run_id)
        ).all()
    )
    failed = func.sum(case((TestResult.status == "fail", 1), else_=0)).cast(Integer)
    rows = session.execute(
        select(TestResult.module, TestResult.run_id, func.count(), failed)
        .where(TestResult.run_id.in_(ids))
        .group_by(TestResult.module, TestResult.run_id)
    ).all()
    cells = {(m, run_id): (int(n), int(f or 0)) for m, run_id, n, f in rows}
    totals: dict[str, int] = {}
    for (module, _), (_, f) in cells.items():
        totals[module] = totals.get(module, 0) + f
    modules = sorted(totals, key=lambda m: (-totals[m], m))
    return Trend(
        points=[RunPoint(r, int(new_counts.get(r.id, 0))) for r in runs],
        modules=modules,
        cells=cells,
    )
