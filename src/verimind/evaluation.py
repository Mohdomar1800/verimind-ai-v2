"""Score the analyzer against a synthetic dataset's answer key.

Metrics (per regression run):
- **Root-cause line accuracy:** share of failing tests where VeriMind's root-cause line is the true
  first-failure line.
- **Status accuracy:** share of tests whose pass/fail status is right.
- **Clustering precision / recall / F1 (pairwise):** over all pairs of failing tests, "same issue?"
  compared with "same true bug?". Precision drops when different bugs are merged into one issue;
  recall drops when one bug is split into several issues.
- **Category accuracy:** design / testbench / infrastructure, compared with the bug's true category.
- **Reduction:** error lines an engineer would otherwise read vs. issues VeriMind reports.

Across runs:
- **Signature stability:** each design/testbench bug should get the same signature id in every
  run, even when runs use different simulators. Otherwise run comparison would report one
  bug as both "new" and "resolved". (Infrastructure failures are excluded: each simulator prints
  its own license or crash message, so they are legitimately different signatures.)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import comb
from pathlib import Path

from verimind.engine.analyzer import Analyzer
from verimind.intelligence.risk import RollingBacktest, RunTests, outcomes_by_test, rolling_backtest
from verimind.synth.answer_key import AnswerKey


@dataclass(frozen=True)
class RunScore:
    run_id: str
    tests: int
    failing_tests: int
    true_causes: int
    issues_found: int
    error_lines: int
    status_accuracy: float
    root_cause_accuracy: float
    cluster_precision: float
    cluster_recall: float
    cluster_f1: float
    category_accuracy: float


@dataclass
class EvaluationResult:
    runs: list[RunScore]
    signatures_per_bug: dict[str, set[str]] = field(default_factory=dict)
    risk: RollingBacktest | None = None  # failure prediction, backtested on the analyzed runs

    @property
    def stable_bugs(self) -> int:
        return sum(1 for ids in self.signatures_per_bug.values() if len(ids) == 1)


def pairwise_scores(predicted: list[str], truth: list[str]) -> tuple[float, float, float]:
    """Pairwise clustering precision/recall/F1, computed from the contingency table (no O(n^2) loop)."""
    together_both = sum(comb(n, 2) for n in Counter(zip(predicted, truth, strict=True)).values())
    together_pred = sum(comb(n, 2) for n in Counter(predicted).values())
    together_true = sum(comb(n, 2) for n in Counter(truth).values())
    precision = together_both / together_pred if together_pred else 1.0
    recall = together_both / together_true if together_true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def evaluate_dataset(dataset_dir: Path, analyzer: Analyzer | None = None) -> EvaluationResult:
    analyzer = analyzer or Analyzer()
    key = AnswerKey.load(dataset_dir)
    categories = {b.bug_id: b.category for b in key.bugs}
    scores = []
    signatures_per_bug: dict[str, set[str]] = {}
    history: list[RunTests] = []

    for run in key.runs:
        run_dir = dataset_dir / "runs" / run.run_id
        report = analyzer.analyze_dir(run_dir, name=run.run_id)
        results = {f"runs/{run.run_id}/{t.path}": t for t in report.tests}
        history.append(outcomes_by_test((t.test_name, t.module, t.status == "fail") for t in report.tests))
        issue_category = {i.issue_id: i.category for i in report.issues}

        status_ok = sum(1 for t in run.tests if results[t.log_file].status == t.status)
        failing = run.failing
        line_ok = 0
        predicted, truth = [], []
        category_ok = 0
        for t in failing:
            r = results[t.log_file]
            if r.root_cause and r.root_cause.line == t.first_failure_line:
                line_ok += 1
            predicted.append(r.issue_id or "none")
            truth.append(t.root_cause or "none")
            if issue_category.get(r.issue_id or "") == categories.get(t.root_cause or ""):
                category_ok += 1
            if t.root_cause and categories.get(t.root_cause) != "infrastructure" and r.issue_id:
                signatures_per_bug.setdefault(t.root_cause, set()).add(r.issue_id)

        precision, recall, f1 = pairwise_scores(predicted, truth)
        n_fail = max(len(failing), 1)
        scores.append(
            RunScore(
                run_id=run.run_id,
                tests=len(run.tests),
                failing_tests=len(failing),
                true_causes=len(set(truth)),
                issues_found=report.summary.issue_count,
                error_lines=report.summary.error_lines,
                status_accuracy=status_ok / max(len(run.tests), 1),
                root_cause_accuracy=line_ok / n_fail,
                cluster_precision=precision,
                cluster_recall=recall,
                cluster_f1=f1,
                category_accuracy=category_ok / n_fail,
            )
        )
    return EvaluationResult(scores, signatures_per_bug, rolling_backtest(history) if len(history) >= 4 else None)
