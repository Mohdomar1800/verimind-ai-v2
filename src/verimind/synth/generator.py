"""Generate a complete synthetic dataset: one or more regression runs plus the answer key.

Layout on disk:

    <out_dir>/
      answer_key.json                  # the truth (never read by the analysis engine)
      runs/
        regression_01/
          dma_stress_test_s1834512.log
          ...
        regression_02/
          ...

Bugs have a lifecycle across runs: some exist from the start, some are introduced later ("new"),
and some get fixed ("resolved"). That's what run-to-run comparison and trend features need.
Everything is derived from one seed, so the same spec always produces byte-identical output.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from verimind import __version__
from verimind.synth import catalog as cat
from verimind.synth.answer_key import AnswerKey, BugTruth, RunTruth, TestTruth
from verimind.synth.coverage import CoverageTest, write_coverage
from verimind.synth.formats import FORMATS, SimContext
from verimind.synth.testbench import BugInstance, TestbenchSimulator, TestPlan

SIMULATOR_CHOICES = (*FORMATS, "mixed")
BASE_DATE = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)  # nightly regressions start at 22:00


@dataclass(frozen=True)
class DatasetSpec:
    name: str = "demo"
    seed: int = 42
    runs: int = 4
    tests_per_run: int = 120
    lines_per_test: int = 300
    simulator: str = "mixed"  # one of FORMATS, or "mixed" to rotate formats run by run
    bugs: int = 6
    infra_rate: float = 0.02
    coverage: bool = True  # also write a coverage.json for each run

    def __post_init__(self) -> None:
        if self.simulator not in SIMULATOR_CHOICES:
            raise ValueError(f"simulator must be one of {', '.join(SIMULATOR_CHOICES)}")
        if not 1 <= self.bugs <= len(cat.BUG_CATALOG):
            raise ValueError(f"bugs must be between 1 and {len(cat.BUG_CATALOG)}")
        if self.runs < 1 or self.tests_per_run < 1 or self.lines_per_test < 10:
            raise ValueError("runs and tests_per_run must be >= 1, and lines_per_test >= 10")
        if not 0 <= self.infra_rate < 1:
            raise ValueError("infra_rate must be in [0, 1)")

    def simulator_for_run(self, run_index: int) -> str:
        if self.simulator != "mixed":
            return self.simulator
        names = list(FORMATS)
        return names[(run_index - 1) % len(names)]


PRESETS: dict[str, DatasetSpec] = {
    # Tiny and fast: unit tests and quick experiments.
    "quick": DatasetSpec(name="quick", runs=1, tests_per_run=40, lines_per_test=80, simulator="vcs", bugs=4),
    # What the "Load demo data" button uses: every log format, with new and fixed bugs across runs.
    "demo": DatasetSpec(name="demo"),
    # Many nightly runs of one project, for trend and history features.
    "history": DatasetSpec(name="history", runs=10, tests_per_run=100, lines_per_test=200, simulator="vcs"),
    # Performance testing: one big regression (~3 million lines).
    "large": DatasetSpec(name="large", runs=1, tests_per_run=2000, lines_per_test=1200, simulator="vcs", bugs=8),
}


@dataclass(frozen=True)
class GenerationReport:
    out_dir: Path
    answer_key: AnswerKey
    total_lines: int
    total_bytes: int

    @property
    def total_tests(self) -> int:
        return sum(len(r.tests) for r in self.answer_key.runs)

    @property
    def failing_tests(self) -> int:
        return sum(len(r.failing) for r in self.answer_key.runs)


def _test_slots(spec: DatasetSpec) -> list[tuple[cat.Block, str]]:
    """The regression's test list (block x scenario), the same in every run."""
    combos = [(b, s) for b in cat.BLOCKS for s in cat.SCENARIOS]
    random.Random(f"{spec.seed}:testlist").shuffle(combos)
    return [combos[i % len(combos)] for i in range(spec.tests_per_run)]


def _plan_bugs(spec: DatasetSpec, slots: list[tuple[cat.Block, str]]) -> list[BugInstance]:
    rng = random.Random(f"{spec.seed}:bugs")
    present = {(b.name, s) for b, s in slots}
    templates = list(cat.BUG_CATALOG)
    rng.shuffle(templates)

    bugs: list[BugInstance] = []
    used_blocks: set[str] = set()
    for template in templates:
        if len(bugs) == spec.bugs:
            break
        candidates = [
            cat.BLOCKS_BY_NAME[name]
            for name in (template.blocks or tuple(cat.BLOCKS_BY_NAME))
            if any((name, s) in present for s in (template.scenarios or cat.SCENARIOS))
        ]
        # Prefer blocks that don't already have a bug, so issues stay distinguishable.
        fresh = [b for b in candidates if b.name not in used_blocks]
        if not (fresh or candidates):
            continue  # no test in this regression could ever hit this bug
        block = rng.choice(fresh or candidates)
        used_blocks.add(block.name)
        bugs.append(
            BugInstance(
                bug_id=f"BUG-{len(bugs) + 1:02d}",
                template=template,
                block=block,
                hit_rate=rng.uniform(*template.hit_rate),
                introduced_in=1,
                fixed_in=None,
            )
        )
    return _schedule_lifecycles(bugs, spec.runs, rng)


def _schedule_lifecycles(bugs: list[BugInstance], runs: int, rng: random.Random) -> list[BugInstance]:
    if runs < 2:
        return bugs
    out: list[BugInstance] = []
    for i, bug in enumerate(bugs):
        if i == 0:  # guarantee one bug that is new in the latest run
            introduced, fixed = runs, None
        elif i == 1 and runs >= 3:  # and one that was fixed before the latest run
            introduced, fixed = 1, runs
        else:
            introduced = 1 if rng.random() < 0.7 else rng.randint(2, runs)
            fixed = None if rng.random() < 0.6 or introduced == runs else rng.randint(introduced + 1, runs)
        out.append(replace(bug, introduced_in=introduced, fixed_in=fixed))
    return out


def _plan_test(
    spec: DatasetSpec, run_index: int, slot_index: int, block: cat.Block, scenario: str, bugs: list[BugInstance]
) -> tuple[TestPlan, random.Random]:
    rng = random.Random(f"{spec.seed}:{run_index}:{slot_index}")
    seed = rng.randrange(1, 2**31)
    test_name = f"{block.name}_{scenario}_test"
    if rng.random() < spec.infra_rate:
        return TestPlan(test_name, seed, block, scenario, infra=rng.choice(cat.INFRA_CATALOG)), rng
    triggered = [b for b in bugs if b.active_in(run_index) and b.affects(block, scenario) and rng.random() < b.hit_rate]
    bug = rng.choice(triggered) if triggered else None
    return TestPlan(test_name, seed, block, scenario, bug=bug), rng


def generate_dataset(spec: DatasetSpec, out_dir: Path) -> GenerationReport:
    out_dir.mkdir(parents=True, exist_ok=True)
    slots = _test_slots(spec)
    bugs = _plan_bugs(spec, slots)
    hits: dict[str, Counter[str]] = {}
    runs: list[RunTruth] = []
    total_lines = total_bytes = 0

    for run_index in range(1, spec.runs + 1):
        run_id = f"regression_{run_index:02d}"
        run_dir = out_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = BASE_DATE + timedelta(days=run_index - 1)
        simulator = spec.simulator_for_run(run_index)
        tests: list[TestTruth] = []
        coverage_tests: list[CoverageTest] = []

        for slot_index, (block, scenario) in enumerate(slots):
            plan, rng = _plan_test(spec, run_index, slot_index, block, scenario, bugs)
            log_name = f"{plan.test_name}_s{plan.seed}.log"
            ctx = SimContext(
                plan.test_name, plan.seed, block.name, log_name, started_at + timedelta(seconds=37 * slot_index)
            )
            result = TestbenchSimulator(plan, FORMATS[simulator](ctx), rng, spec.lines_per_test).run()

            text = "\n".join(result.lines) + "\n"
            data = text.encode("utf-8")
            (run_dir / log_name).write_bytes(data)
            total_lines += len(result.lines)
            total_bytes += len(data)

            coverage_tests.append(CoverageTest(plan.test_name, plan.seed, block.name, scenario, len(result.lines)))
            root_cause = plan.root_cause if not result.passed else None
            if root_cause:
                hits.setdefault(root_cause, Counter())[run_id] += 1
            tests.append(
                TestTruth(
                    test_name=plan.test_name,
                    seed=plan.seed,
                    block=block.name,
                    scenario=scenario,
                    log_file=f"runs/{run_id}/{log_name}",
                    status="pass" if result.passed else "fail",
                    root_cause=root_cause,
                    first_failure_line=result.error_lines[0] if result.error_lines else None,
                    error_lines=result.error_lines,
                    total_lines=len(result.lines),
                )
            )
        if spec.coverage:
            write_coverage(run_dir, coverage_tests, (b.name for b, _ in slots), seed=str(spec.seed))
        runs.append(RunTruth(run_id=run_id, index=run_index, simulator=simulator, started_at=started_at, tests=tests))

    key = AnswerKey(
        dataset=spec.name,
        seed=spec.seed,
        generator_version=__version__,
        spec=asdict(spec),
        bugs=_bug_truths(bugs, hits),
        runs=runs,
    )
    key.save(out_dir)
    return GenerationReport(out_dir, key, total_lines, total_bytes)


def _bug_truths(bugs: list[BugInstance], hits: dict[str, Counter[str]]) -> list[BugTruth]:
    truths = [
        BugTruth(
            bug_id=b.bug_id,
            key=b.template.key,
            title=b.title,
            category=b.template.category,
            block=b.block.name,
            signature_hint=b.template.signature_hint,
            introduced_in_run=b.introduced_in,
            fixed_in_run=b.fixed_in,
            hits_per_run=dict(hits.get(b.bug_id, {})),
        )
        for b in bugs
    ]
    for infra in cat.INFRA_CATALOG:
        truths.append(
            BugTruth(
                bug_id=infra.bug_id,
                key=infra.key,
                title=infra.title,
                category="infrastructure",
                block=None,
                signature_hint=infra.kind,
                introduced_in_run=1,
                fixed_in_run=None,
                hits_per_run=dict(hits.get(infra.bug_id, {})),
            )
        )
    return truths
