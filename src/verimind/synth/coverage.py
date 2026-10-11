"""Synthetic functional coverage for a regression run, written as `coverage.json`.

Each design block has a covergroup with coverpoints such as burst length or FIFO level, each split
into bins. Each test scenario exercises different bins: stress tests reach full FIFOs, reset tests
reach reset-during-traffic, error-injection tests reach error responses. Some bins are hard to hit
and some are never hit (coverage holes), which is what coverage prioritization has to find.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

COVERAGE_FILE = "coverage.json"
FORMAT = "verimind-coverage/1"

# coverpoint -> bins, and which scenarios tend to reach each bin (empty tuple: any scenario).
COVERPOINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "op_type": {"read": (), "write": (), "read_modify_write": ("random", "stress"), "broadcast": ("perf",)},
    "burst_len": {
        "len_1": (),
        "len_4": (),
        "len_16": ("random", "stress", "perf"),
        "len_64": ("stress", "perf"),
        "len_256": ("stress",),
    },
    "error_resp": {
        "okay": (),
        "slverr": ("error_inject",),
        "decerr": ("error_inject",),
        "timeout": ("error_inject", "stress"),
    },
    "fifo_level": {"empty": (), "half": ("random", "stress"), "full": ("stress",), "overflow_attempt": ("stress",)},
    "reset_during": {"idle": ("reset",), "active_burst": ("reset",), "error_pending": ("reset", "error_inject")},
    "concurrency": {"single": (), "dual": ("random", "perf"), "quad": ("perf", "stress")},
}


@dataclass(frozen=True)
class CoverageTest:
    test_name: str
    seed: int
    block: str
    scenario: str
    lines: int


def write_coverage(run_dir: Path, tests: Iterable[CoverageTest], blocks: Iterable[str], seed: str) -> Path:
    rng = random.Random(f"{seed}:coverage")
    blocks = sorted(set(blocks))
    # Per block: a few bins are effectively unreachable with the current tests (true holes), and the
    # rest get a per-bin difficulty (chance a matching test actually hits it).
    # Coverage maturity differs a lot between blocks, so each gets its own share of holes.
    hole_rate = {b: rng.uniform(0.03, 0.3) for b in blocks}
    holes = {
        b: {(cp, bn) for cp, bins in COVERPOINTS.items() for bn in bins if rng.random() < hole_rate[b]} for b in blocks
    }
    difficulty = {
        (b, cp, bn): rng.choice((0.9, 0.7, 0.4, 0.15))
        for b in blocks
        for cp, bins in COVERPOINTS.items()
        for bn in bins
    }

    covergroups = [
        {
            "name": f"{b}_cg",
            "block": b,
            "coverpoints": [{"name": cp, "bins": list(bins)} for cp, bins in COVERPOINTS.items()],
        }
        for b in blocks
    ]
    test_rows = []
    for t in tests:
        trng = random.Random(f"{seed}:coverage:{t.test_name}:{t.seed}")
        hits = []
        for cp, bins in COVERPOINTS.items():
            for bn, scenarios in bins.items():
                if (cp, bn) in holes[t.block] or (scenarios and t.scenario not in scenarios):
                    continue
                if trng.random() < difficulty[(t.block, cp, bn)]:
                    hits.append(f"{t.block}_cg.{cp}.{bn}")
        # Simulation time grows with log length (a stand-in for cycles simulated), plus fixed startup cost.
        cpu = round(5 + t.lines * 0.12 * trng.uniform(0.8, 1.25), 1)
        test_rows.append({"test": t.test_name, "seed": t.seed, "cpu_seconds": cpu, "hits": hits})

    path = run_dir / COVERAGE_FILE
    path.write_text(json.dumps({"format": FORMAT, "covergroups": covergroups, "tests": test_rows}, indent=1))
    return path
