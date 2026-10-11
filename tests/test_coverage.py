"""Coverage prioritization: hole ranking and greedy test grading."""

from __future__ import annotations

import io
from pathlib import Path

from verimind.intelligence.coverage import CoverageFile, analyze_coverage
from verimind.storage import Storage


def data(tests: list[tuple[str, float, list[str]]]) -> CoverageFile:
    return CoverageFile.model_validate(
        {
            "covergroups": [
                {"name": "dma_cg", "block": "dma", "coverpoints": [{"name": "len", "bins": ["a", "b", "c", "d"]}]},
                {"name": "uart_cg", "block": "uart", "coverpoints": [{"name": "lvl", "bins": ["x", "y"]}]},
            ],
            "tests": [{"test": n, "cpu_seconds": cpu, "hits": hits} for n, cpu, hits in tests],
        }
    )


def test_summary_and_holes() -> None:
    cov = data([("t1", 10, ["dma_cg.len.a", "dma_cg.len.b"]), ("t2", 10, [])])
    result = analyze_coverage(cov, {"dma": 0.5})
    assert (result.total_bins, result.hit_bins, result.percent) == (6, 2, 33.3)
    dma_hole, uart_hole = result.holes
    # dma: partly covered and its block is failing, so it ranks above the untouched uart coverpoint.
    assert dma_hole.block == "dma" and dma_hole.missing == ["c", "d"]
    assert dma_hole.priority == 2 * (1 + 2 * 0.5) * 1.5
    assert any("failing 50%" in r for r in dma_hole.reasons) and any("Partly covered" in r for r in dma_hole.reasons)
    assert uart_hole.missing == ["x", "y"] and any("Not covered at all" in r for r in uart_hole.reasons)


def test_grading_prefers_new_coverage_per_second() -> None:
    cov = data(
        [
            ("slow_everything", 100, ["dma_cg.len.a", "dma_cg.len.b", "dma_cg.len.c"]),
            ("fast_a_b", 5, ["dma_cg.len.a", "dma_cg.len.b"]),
            ("fast_c", 5, ["dma_cg.len.c"]),
            ("redundant", 10, ["dma_cg.len.a"]),  # 0.1 bins/s: worse than the fast tests
        ]
    )
    result = analyze_coverage(cov)
    picked = [g.test for g in result.graded]
    assert picked[:2] in (["fast_a_b", "fast_c"], ["fast_c", "fast_a_b"])  # 2 cheap tests replace the slow one
    assert "slow_everything" not in picked and "redundant" not in picked  # they add nothing new
    assert result.graded[-1].cumulative_percent == 100.0
    assert result.tests_for_target == 2 and result.cpu_for_target == 10.0


def test_unknown_bins_are_reported_not_counted() -> None:
    result = analyze_coverage(data([("t", 1, ["dma_cg.len.a", "typo_cg.len.zz"])]))
    assert result.hit_bins == 1 and result.unknown_hits == 1 and result.notes
    assert result.to_dict()["percent"] == result.percent


def test_storage_accepts_coverage_json(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    storage.save_files(1, [("t.log", io.BytesIO(b"x")), ("coverage.json", io.BytesIO(b"{}"))])
    assert storage.coverage_path(1) == storage.logs_dir(1) / "coverage.json"
    assert storage.coverage_path(2) is None
