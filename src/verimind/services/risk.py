"""Risk predictions from the runs stored in the database (shown on the Trends page)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from verimind.db.models import Run, RunStatus, TestResult
from verimind.intelligence.risk import (
    Prediction,
    RiskModel,
    RollingBacktest,
    RunTests,
    outcomes_by_test,
    rolling_backtest,
)

MIN_RUNS = 3


@dataclass(frozen=True)
class RiskReport:
    runs_used: int
    predictions: list[Prediction]  # for the next run, most likely failure first
    backtest: RollingBacktest | None
    coefficients: dict[str, float]


def history_from_db(session: Session, limit: int = 30) -> list[RunTests]:
    runs = list(
        session.scalars(
            select(Run.id)
            .where(Run.status == RunStatus.DONE)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(limit)
        )
    )[::-1]
    out: list[RunTests] = []
    for run_id in runs:
        rows = session.execute(
            select(TestResult.test_name, TestResult.module, TestResult.status).where(TestResult.run_id == run_id)
        )
        out.append(outcomes_by_test((name, module, status == "fail") for name, module, status in rows))
    return out


def risk_report(session: Session, top: int = 15) -> RiskReport | None:
    """None when there isn't enough history yet (fewer than MIN_RUNS analyzed runs)."""
    history = history_from_db(session)
    if len(history) < MIN_RUNS:
        return None
    model = RiskModel().fit(history)
    # Predict for the tests of the latest run, which is the regression's current test list.
    predictions = model.score(history, list(history[-1].values()))
    return RiskReport(len(history), predictions[:top], rolling_backtest(history), model.coefficients())
