"""The failure-prediction model: no look-ahead, sensible fallbacks, and better than guessing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from verimind.intelligence.risk import FEATURES, RiskModel, build_dataset, features, outcomes_by_test, rolling_backtest
from verimind.synth import PRESETS, generate_dataset


def run(*rows: tuple[str, str, bool]):  # noqa: ANN201
    return outcomes_by_test(rows)


def test_features_use_only_earlier_runs() -> None:
    history = [run(("t1", "dma", True), ("t2", "dma", False)), run(("t1", "dma", True), ("t2", "dma", False))]
    f = dict(zip(FEATURES, features(history, "t1", "dma"), strict=True))
    assert f["fail_rate_recent"] == 1.0 and f["failed_last_run"] == 1.0 and f["ever_failed"] == 1.0
    assert f["fail_streak"] == 2 / 5 and f["block_fail_rate_last"] == 0.5
    # The dataset for run t never sees run t's own outcome.
    x, y = build_dataset([*history, run(("t1", "dma", False), ("t2", "dma", True))])
    assert y.tolist() == [1, 0, 0, 1]  # labels for runs 1 and 2
    assert x.shape == (4, len(FEATURES))


def test_seeds_of_one_test_count_as_one_outcome() -> None:
    r = run(("t1", "dma", False), ("t1", "dma", True), ("t1", "dma", False))
    assert r["t1"].failed is True


def test_untrainable_history_falls_back_to_failure_rate() -> None:
    history = [run(("t1", "aes", False), ("t2", "aes", False))] * 3
    model = RiskModel().fit(history)
    assert model.trained is False and model.coefficients() == {}
    preds = model.score(history, list(history[-1].values()))
    assert all(p.probability == 0.0 for p in preds)


def test_model_beats_random_and_ranks_better_than_last_run(tmp_path: Path) -> None:
    key = generate_dataset(replace(PRESETS["history"], runs=10, lines_per_test=20), tmp_path).answer_key
    runs = [outcomes_by_test((t.test_name, t.block, t.status == "fail") for t in r.tests) for r in key.runs]
    result = rolling_backtest(runs)
    assert result is not None and result.evaluated_runs >= 5
    assert result.model_precision > 2 * result.failure_rate  # far better than guessing
    assert result.model_auc is not None and result.baseline_auc is not None
    assert result.model_auc > result.baseline_auc  # ranks the whole list better than "failed last time"

    model = RiskModel().fit(runs)
    top = model.score(runs, list(runs[-1].values()))[0]
    assert top.reasons and 0 < top.probability <= 1
