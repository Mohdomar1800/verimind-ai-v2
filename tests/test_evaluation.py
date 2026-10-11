from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from verimind.evaluation import evaluate_dataset, pairwise_scores
from verimind.synth import PRESETS, generate_dataset


def test_pairwise_scores() -> None:
    assert pairwise_scores(["a", "a", "b"], ["x", "x", "y"]) == (1.0, 1.0, 1.0)
    # Merging two true groups into one issue hurts precision, not recall.
    precision, recall, _ = pairwise_scores(["a", "a", "a"], ["x", "x", "y"])
    assert (round(precision, 3), recall) == (0.333, 1.0)
    # Splitting one true group hurts recall, not precision.
    precision, recall, _ = pairwise_scores(["a", "b"], ["x", "x"])
    assert (precision, recall) == (1.0, 0.0)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_analysis_quality_on_synthetic_regressions(tmp_path: Path, seed: int) -> None:
    spec = replace(PRESETS["demo"], seed=seed, tests_per_run=90, lines_per_test=60, infra_rate=0.05)
    generate_dataset(spec, tmp_path)
    result = evaluate_dataset(tmp_path)
    # Every real bug keeps one signature id across all four runs, although each run uses a different
    # simulator log format. Run comparison depends on this.
    assert result.stable_bugs == len(result.signatures_per_bug)
    for score in result.runs:
        assert score.status_accuracy == 1.0, score
        assert score.root_cause_accuracy == 1.0, score
        assert score.cluster_f1 >= 0.95, score
        assert score.category_accuracy >= 0.95, score
