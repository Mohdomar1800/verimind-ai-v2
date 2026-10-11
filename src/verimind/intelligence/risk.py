"""Predict which tests are likely to fail in the next run, from the history of earlier runs.

Honest by construction:
- **Labels are real outcomes:** did this test actually fail in the following run? Nothing is derived
  from the features themselves (the hackathon version's labels were, so its metrics were circular).
- **Features only look backwards:** for run t, they use runs 0..t-1 only.
- **Evaluation is a time-based backtest:** train on earlier runs, score the next one, and compare with
  the obvious baseline ("it failed last time") and with the historical failure rate.
- **The model is logistic regression on a handful of named signals,** so every prediction can be
  explained ("failed in 4 of the last 5 runs; 40% of block dma's tests failed last run").
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RECENT = 5  # how many earlier runs count as "recent"

FEATURES = (
    "fail_rate_recent",
    "failed_last_run",
    "fail_streak",
    "ever_failed",
    "block_fail_rate_last",
    "block_fail_rate_recent",
)


@dataclass(frozen=True)
class Observation:
    test: str
    module: str
    failed: bool


RunTests = dict[str, Observation]  # one run: test name -> outcome


def outcomes_by_test(rows: Iterable[tuple[str, str, bool]]) -> RunTests:
    """Collapse a run's rows (test, module, failed) by test name. A test that ran with several seeds
    counts as failed if any seed failed."""
    out: RunTests = {}
    for test, module, failed in rows:
        prev = out.get(test)
        out[test] = Observation(test, module, failed or (prev.failed if prev else False))
    return out


def _block_rate(run: RunTests, module: str) -> float:
    tests = [o for o in run.values() if o.module == module]
    return sum(o.failed for o in tests) / len(tests) if tests else 0.0


def features(history: Sequence[RunTests], test: str, module: str) -> list[float]:
    """Signals about `test`, computed only from the runs in `history` (all earlier than the target)."""
    recent = history[-RECENT:]
    seen = [run[test].failed for run in recent if test in run]
    streak = 0
    for run in reversed(history):
        if test in run and run[test].failed:
            streak += 1
        else:
            break
    last = history[-1] if history else {}
    return [
        sum(seen) / len(seen) if seen else 0.0,
        1.0 if test in last and last[test].failed else 0.0,
        min(streak, RECENT) / RECENT,
        1.0 if any(test in run and run[test].failed for run in history) else 0.0,
        _block_rate(last, module) if last else 0.0,
        float(np.mean([_block_rate(run, module) for run in recent])) if recent else 0.0,
    ]


def build_dataset(runs: Sequence[RunTests], start: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """One row per (test, run t >= start): features from runs before t, label = failed in run t."""
    x, y = [], []
    for t in range(start, len(runs)):
        for test, obs in runs[t].items():
            x.append(features(runs[:t], test, obs.module))
            y.append(int(obs.failed))
    return np.array(x, dtype=float).reshape(-1, len(FEATURES)), np.array(y, dtype=int)


@dataclass(frozen=True)
class Prediction:
    test: str
    module: str
    probability: float
    reasons: list[str]


def _explain(values: list[float], contributions: np.ndarray, module: str) -> list[str]:
    phrases = {
        "fail_rate_recent": lambda v: f"failed in {v:.0%} of its recent runs",
        "failed_last_run": lambda v: "failed in the last run",
        "fail_streak": lambda v: f"failing {round(v * RECENT)} run(s) in a row",
        "ever_failed": lambda v: "has failed before",
        "block_fail_rate_last": lambda v: f"{v:.0%} of block {module}'s tests failed last run",
        "block_fail_rate_recent": lambda v: f"block {module} has averaged {v:.0%} failing tests recently",
    }
    order = np.argsort(-contributions)
    return [phrases[FEATURES[i]](values[i]) for i in order[:2] if contributions[i] > 0 and values[i] > 0]


class RiskModel:
    """Logistic regression over the named history signals."""

    def __init__(self) -> None:
        self.pipeline = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000))
        self.trained = False

    def fit(self, runs: Sequence[RunTests]) -> RiskModel:
        x, y = build_dataset(runs)
        if len(set(y.tolist())) < 2:
            return self  # every test always passed (or always failed): nothing to learn; use the fallback
        self.pipeline.fit(x, y)
        self.trained = True
        return self

    def score(self, history: Sequence[RunTests], targets: Sequence[Observation]) -> list[Prediction]:
        """Probability that each target test fails in the run after `history`, most likely first."""
        if not targets:
            return []
        x = np.array([features(history, o.test, o.module) for o in targets])
        if self.trained:
            probs = self.pipeline.predict_proba(x)[:, 1]
            scaler, clf = self.pipeline[0], self.pipeline[1]
            contributions = scaler.transform(x) * clf.coef_[0]
        else:  # fallback: recent failure rate
            probs = x[:, 0]
            contributions = x
        preds = [
            Prediction(o.test, o.module, float(p), _explain(list(row), c, o.module))
            for o, p, row, c in zip(targets, probs, x, contributions, strict=True)
        ]
        return sorted(preds, key=lambda p: (-p.probability, p.test))

    def coefficients(self) -> dict[str, float]:
        if not self.trained:
            return {}
        return dict(zip(FEATURES, self.pipeline[1].coef_[0].round(3).tolist(), strict=True))


@dataclass(frozen=True)
class Backtest:
    """How well the model, trained on runs before the last one, predicted the last run."""

    test_run_failures: int
    k: int
    model_hits: int
    baseline_last_run_hits: int  # baseline: rank tests by "failed in the previous run"
    baseline_rate_hits: int  # baseline: rank tests by recent failure rate
    model_auc: float | None
    baseline_auc: float | None

    @property
    def model_precision(self) -> float:
        return self.model_hits / self.k if self.k else 0.0

    @property
    def baseline_precision(self) -> float:
        return self.baseline_last_run_hits / self.k if self.k else 0.0


def backtest(runs: Sequence[RunTests]) -> Backtest | None:
    """Train on runs[:-1], predict runs[-1]. k = the number of tests that really failed (R-precision)."""
    if len(runs) < 3:
        return None
    history, target = runs[:-1], runs[-1]
    model = RiskModel().fit(history)
    observations = list(target.values())
    preds = model.score(history, observations)
    actual = {o.test for o in observations if o.failed}
    k = len(actual)
    if k == 0:
        return Backtest(0, 0, 0, 0, 0, None, None)

    def hits(ranked: list[str]) -> int:
        return len(set(ranked[:k]) & actual)

    feats = {o.test: features(history, o.test, o.module) for o in observations}
    by_last = sorted(observations, key=lambda o: (-feats[o.test][1], -feats[o.test][0], o.test))
    by_rate = sorted(observations, key=lambda o: (-feats[o.test][0], o.test))
    labels = [int(o.failed) for o in observations]
    prob = {p.test: p.probability for p in preds}
    both_classes = 0 < sum(labels) < len(labels)
    return Backtest(
        test_run_failures=k,
        k=k,
        model_hits=hits([p.test for p in preds]),
        baseline_last_run_hits=hits([o.test for o in by_last]),
        baseline_rate_hits=hits([o.test for o in by_rate]),
        model_auc=float(roc_auc_score(labels, [prob[o.test] for o in observations])) if both_classes else None,
        baseline_auc=float(roc_auc_score(labels, [feats[o.test][1] for o in observations])) if both_classes else None,
    )


@dataclass(frozen=True)
class RollingBacktest:
    """Backtests repeated for every run that has enough history before it, then pooled."""

    evaluated_runs: int
    failures: int
    model_hits: int
    last_run_hits: int
    rate_hits: int
    model_auc: float | None
    baseline_auc: float | None
    failure_rate: float  # share of tests that fail: the hit rate of random guessing

    @property
    def model_precision(self) -> float:
        return self.model_hits / self.failures if self.failures else 0.0

    @property
    def last_run_precision(self) -> float:
        return self.last_run_hits / self.failures if self.failures else 0.0

    @property
    def rate_precision(self) -> float:
        return self.rate_hits / self.failures if self.failures else 0.0


def rolling_backtest(runs: Sequence[RunTests], min_history: int = 3) -> RollingBacktest | None:
    """For each run t >= min_history: train on runs[:t], predict run t. Needs min_history + 1 runs."""
    results = [b for end in range(min_history + 1, len(runs) + 1) if (b := backtest(runs[:end])) is not None]
    results = [b for b in results if b.k]
    if not results:
        return None
    model_aucs = [b.model_auc for b in results if b.model_auc is not None]
    base_aucs = [b.baseline_auc for b in results if b.baseline_auc is not None]
    total_tests = sum(len(r) for r in runs[min_history:])
    return RollingBacktest(
        evaluated_runs=len(results),
        failures=sum(b.k for b in results),
        model_hits=sum(b.model_hits for b in results),
        last_run_hits=sum(b.baseline_last_run_hits for b in results),
        rate_hits=sum(b.baseline_rate_hits for b in results),
        model_auc=float(np.mean(model_aucs)) if model_aucs else None,
        baseline_auc=float(np.mean(base_aucs)) if base_aucs else None,
        failure_rate=sum(b.k for b in results) / total_tests if total_tests else 0.0,
    )
