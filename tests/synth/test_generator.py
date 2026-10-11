"""The answer key must be exactly right, otherwise every metric computed against it is meaningless."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path

import pytest

from verimind.synth import PRESETS, AnswerKey, DatasetSpec, generate_dataset
from verimind.synth.formats import FORMATS

# A line that starts an error-level report, in any of the supported formats. Summary lines such as
# "UVM_ERROR :    3" and benign text such as "error_count=0" must NOT match.
ERROR_START = re.compile(
    r"""^(
        (\#\ )?UVM_(ERROR|FATAL)\ (\S+\(\d+\)\ )?@        # UVM error/fatal (Questa adds '# ')
      | "[^"]+",\ \d+:\ \S+:\ started\ at                 # VCS assertion
      | Error-\[                                          # VCS tool error
      | xmsim:\ \*[EF],                                   # Xcelium error/fatal
      | xrun:\ \*F,                                       # Xcelium launcher fatal
      | \#\ \*\*\ (Error|Fatal):                          # Questa error/fatal
      | \S+\ \S+\ (ERROR|FATAL)\                          # in-house runner
      | \*\*\*\ Job\ terminated                           # farm killed the job
    )""",
    re.VERBOSE,
)

SMALL = replace(PRESETS["quick"], tests_per_run=30, lines_per_test=60)


def _check_answer_key(dataset_dir: Path, key: AnswerKey) -> None:
    bug_ids = {b.bug_id for b in key.bugs}
    for run in key.runs:
        for t in run.tests:
            lines = (dataset_dir / t.log_file).read_text(encoding="utf-8").splitlines()
            assert len(lines) == t.total_lines

            actual_error_lines = [n for n, line in enumerate(lines, start=1) if ERROR_START.match(line)]
            assert actual_error_lines == t.error_lines, t.log_file

            if t.status == "pass":
                assert t.root_cause is None and t.first_failure_line is None
                assert any("PASSED" in line for line in lines)
            else:
                assert t.root_cause in bug_ids
                assert t.first_failure_line == t.error_lines[0]


@pytest.fixture(scope="module")
def quick(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, AnswerKey]:
    out = tmp_path_factory.mktemp("quick")
    report = generate_dataset(PRESETS["quick"], out)
    return out, report.answer_key


def test_answer_key_matches_logs(quick: tuple[Path, AnswerKey]) -> None:
    out, key = quick
    assert AnswerKey.load(out) == key
    _check_answer_key(out, key)


@pytest.mark.parametrize("simulator", list(FORMATS))
def test_every_format_is_consistent(tmp_path: Path, simulator: str) -> None:
    spec = replace(SMALL, simulator=simulator, infra_rate=0.2)  # high infra rate to exercise crash output
    key = generate_dataset(spec, tmp_path).answer_key
    assert any(t.status == "fail" for t in key.runs[0].tests)
    _check_answer_key(tmp_path, key)


def test_same_seed_gives_identical_output(tmp_path: Path) -> None:
    def digest(root: Path) -> str:
        h = hashlib.sha256()
        for p in sorted(root.rglob("*")):
            if p.is_file():
                h.update(p.relative_to(root).as_posix().encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    generate_dataset(SMALL, tmp_path / "a")
    generate_dataset(SMALL, tmp_path / "b")
    generate_dataset(replace(SMALL, seed=7), tmp_path / "c")
    assert digest(tmp_path / "a") == digest(tmp_path / "b")
    assert digest(tmp_path / "a") != digest(tmp_path / "c")


def test_bug_lifecycle_across_runs(tmp_path: Path) -> None:
    spec = replace(PRESETS["demo"], tests_per_run=60, lines_per_test=40)
    key = generate_dataset(spec, tmp_path).answer_key
    bugs = {b.bug_id: b for b in key.bugs}

    for run in key.runs:
        for t in run.failing:
            bug = bugs[t.root_cause or ""]
            assert bug.introduced_in_run <= run.index
            assert bug.fixed_in_run is None or run.index < bug.fixed_in_run
        observed: dict[str, int] = {}
        for t in run.failing:
            observed[t.root_cause or ""] = observed.get(t.root_cause or "", 0) + 1
        assert observed == {b.bug_id: b.hits_per_run[run.run_id] for b in key.bugs if run.run_id in b.hits_per_run}

    # The demo guarantees one bug that is new in the latest run and one that was fixed before it.
    last = len(key.runs)
    assert any(b.introduced_in_run == last for b in key.bugs)
    assert any(b.fixed_in_run == last for b in key.bugs)


def test_passing_logs_contain_misleading_error_words(quick: tuple[Path, AnswerKey]) -> None:
    """Keyword search for 'error' must not be enough to find failures."""
    out, key = quick
    passing = [t for t in key.runs[0].tests if t.status == "pass"]
    assert any("error" in (out / t.log_file).read_text().lower() for t in passing)


@pytest.mark.parametrize(
    "overrides",
    [{"simulator": "modelsim"}, {"bugs": 0}, {"bugs": 99}, {"runs": 0}, {"infra_rate": 1.5}],
)
def test_invalid_spec_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DatasetSpec(**overrides)  # type: ignore[arg-type]
