"""Coverage prioritization: which coverage holes to close first, and which tests to keep.

Input is `coverage.json` (format "verimind-coverage/1", see docs/coverage-format.md). It's a
tool-neutral export that a short script can produce from VCS (URG), Questa (vcover) or Xcelium (IMC)
reports.

Two outputs:
1. **Ranked holes.** A coverpoint with never-hit bins is a gap in verification. It ranks higher when:
   - its design block is currently failing tests (gaps there can hide more bugs),
   - it is partly covered (existing stimulus already reaches the area, so a targeted test is cheap),
   - many of its bins are missing.
2. **Test grading** (what commercial tools call test ranking). Greedily pick the test that adds the
   most not-yet-covered bins per CPU second, until nothing new is added. The result shows how few
   tests keep 95% / 100% of the coverage the full regression achieves: a fast "smoke" set.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field

FORMAT = "verimind-coverage/1"
GRADING_TARGET = 0.95


class Coverpoint(BaseModel):
    name: str
    bins: list[str]


class Covergroup(BaseModel):
    name: str
    block: str
    coverpoints: list[Coverpoint]


class CoverageTestRow(BaseModel):
    test: str
    seed: int | None = None
    cpu_seconds: float = Field(default=0.0, ge=0)
    hits: list[str] = Field(default_factory=list)


class CoverageFile(BaseModel):
    format: str = FORMAT
    covergroups: list[Covergroup]
    tests: list[CoverageTestRow]


@dataclass
class BlockCoverage:
    block: str
    total: int
    hit: int

    @property
    def percent(self) -> float:
        return round(100 * self.hit / self.total, 1) if self.total else 100.0


@dataclass
class Hole:
    block: str
    covergroup: str
    coverpoint: str
    missing: list[str]
    total_bins: int
    priority: float
    reasons: list[str]


@dataclass
class GradedTest:
    test: str
    seed: int | None
    cpu_seconds: float
    new_bins: int
    cumulative_percent: float  # of the coverage the whole regression achieved


@dataclass
class CoverageAnalysis:
    total_bins: int
    hit_bins: int
    blocks: list[BlockCoverage]
    holes: list[Hole]
    graded: list[GradedTest]  # in selection order; tests that add nothing are left out
    tests_total: int
    cpu_total: float
    tests_for_target: int  # tests needed to keep GRADING_TARGET of achieved coverage
    cpu_for_target: float
    unknown_hits: int = 0  # hits naming bins that aren't declared (usually an export problem)
    target: float = GRADING_TARGET
    notes: list[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        return round(100 * self.hit_bins / self.total_bins, 1) if self.total_bins else 100.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["percent"] = self.percent
        for block, row in zip(self.blocks, data["blocks"], strict=True):
            row["percent"] = block.percent
        return data


def analyze_coverage(data: CoverageFile, block_fail_rate: dict[str, float] | None = None) -> CoverageAnalysis:
    fail_rate = block_fail_rate or {}
    declared: dict[str, tuple[str, str, str]] = {}  # bin id -> (block, covergroup, coverpoint)
    for g in data.covergroups:
        for cp in g.coverpoints:
            for b in cp.bins:
                declared[f"{g.name}.{cp.name}.{b}"] = (g.block, g.name, cp.name)

    hit_by_test = [(t, {h for h in t.hits if h in declared}) for t in data.tests]
    unknown = sum(len(set(t.hits)) for t in data.tests) - sum(len(h) for _, h in hit_by_test)
    covered = set().union(*(h for _, h in hit_by_test)) if hit_by_test else set()

    blocks: dict[str, BlockCoverage] = {}
    for bin_id, (block, _, _) in declared.items():
        bc = blocks.setdefault(block, BlockCoverage(block, 0, 0))
        bc.total += 1
        bc.hit += bin_id in covered

    holes = []
    for g in data.covergroups:
        for cp in g.coverpoints:
            missing = [b for b in cp.bins if f"{g.name}.{cp.name}.{b}" not in covered]
            if not missing:
                continue
            rate = fail_rate.get(g.block, 0.0)
            partly = len(missing) < len(cp.bins)
            priority = len(missing) * (1 + 2 * rate) * (1.5 if partly else 1.0)
            reasons = [f"{len(missing)} of {len(cp.bins)} bins never hit: {', '.join(missing)}."]
            if rate > 0:
                reasons.append(
                    f"Block {g.block} is failing {rate:.0%} of its tests, and gaps there can hide more bugs."
                )
            if partly:
                reasons.append(
                    "Partly covered: existing tests already reach this area, so a targeted test should be cheap."
                )
            else:
                reasons.append(
                    "Not covered at all: may need new stimulus, or a review of whether these bins are reachable."
                )
            holes.append(Hole(g.block, g.name, cp.name, missing, len(cp.bins), round(priority, 2), reasons))
    holes.sort(key=lambda h: (-h.priority, h.block, h.coverpoint))

    graded, tests_for_target, cpu_for_target = _grade(hit_by_test, len(covered))
    return CoverageAnalysis(
        total_bins=len(declared),
        hit_bins=len(covered),
        blocks=sorted(blocks.values(), key=lambda b: (b.percent, b.block)),
        holes=holes,
        graded=graded,
        tests_total=len(data.tests),
        cpu_total=round(sum(t.cpu_seconds for t in data.tests), 1),
        tests_for_target=tests_for_target,
        cpu_for_target=cpu_for_target,
        unknown_hits=unknown,
        notes=[f"{unknown} hit(s) name bins that are not declared in any covergroup."] if unknown else [],
    )


def _grade(hit_by_test: list[tuple[CoverageTestRow, set[str]]], achieved: int) -> tuple[list[GradedTest], int, float]:
    """Greedy weighted set cover: most new bins per CPU second first."""
    remaining = list(hit_by_test)
    covered: set[str] = set()
    graded: list[GradedTest] = []
    tests_for_target, cpu_for_target, cpu = 0, 0.0, 0.0
    while remaining:
        best_i = max(
            range(len(remaining)),
            key=lambda i: (
                len(remaining[i][1] - covered) / max(remaining[i][0].cpu_seconds, 0.1),
                len(remaining[i][1]),
            ),
        )
        test, bins = remaining.pop(best_i)
        new = bins - covered
        if not new:
            break
        covered |= new
        cpu += test.cpu_seconds
        pct = round(100 * len(covered) / achieved, 1) if achieved else 100.0
        graded.append(GradedTest(test.test, test.seed, test.cpu_seconds, len(new), pct))
        if not tests_for_target and pct >= 100 * GRADING_TARGET:
            tests_for_target, cpu_for_target = len(graded), round(cpu, 1)
    return graded, tests_for_target or len(graded), cpu_for_target or round(cpu, 1)
