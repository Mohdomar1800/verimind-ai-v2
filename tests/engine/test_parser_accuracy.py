"""The parser, scored against the generator's answer key, must be exact for the built-in formats."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from verimind.engine import LogParser, Severity
from verimind.synth import PRESETS, AnswerKey, generate_dataset
from verimind.synth.formats import FORMATS


@pytest.fixture(scope="module")
def parser() -> LogParser:
    return LogParser()


@pytest.mark.parametrize("simulator", list(FORMATS))
def test_parser_matches_answer_key(tmp_path: Path, parser: LogParser, simulator: str) -> None:
    spec = replace(PRESETS["quick"], simulator=simulator, tests_per_run=60, lines_per_test=60, infra_rate=0.15)
    key: AnswerKey = generate_dataset(spec, tmp_path).answer_key

    for truth in key.runs[0].tests:
        parsed = parser.parse_file(tmp_path / truth.log_file)
        where = truth.log_file

        assert parsed.format == simulator, where
        assert parsed.status == truth.status, f"{where}: {parsed.status_reason}"
        assert parsed.test_name == truth.test_name, where
        assert parsed.seed == truth.seed, where
        assert parsed.total_lines == truth.total_lines, where
        assert [e.line for e in parsed.errors] == truth.error_lines, where
        first = parsed.first_error
        assert (first.line if first else None) == truth.first_failure_line, where


def test_infrastructure_failures_are_classified(tmp_path: Path, parser: LogParser) -> None:
    spec = replace(PRESETS["demo"], tests_per_run=60, lines_per_test=40, infra_rate=0.3)
    key = generate_dataset(spec, tmp_path).answer_key
    infra_tests = [t for r in key.runs for t in r.failing if (t.root_cause or "").startswith("INFRA-")]
    assert infra_tests

    for truth in infra_tests:
        first = parser.parse_file(tmp_path / truth.log_file).first_error
        assert first is not None
        expected = "crash" if truth.root_cause in ("INFRA-SEGFAULT", "INFRA-OOM") else "tool"
        assert first.kind == expected, truth.log_file
        assert first.severity == Severity.FATAL or expected == "tool"


def test_assertion_failures_are_one_event_with_details(tmp_path: Path, parser: LogParser) -> None:
    spec = replace(PRESETS["demo"], tests_per_run=120, lines_per_test=40)
    key = generate_dataset(spec, tmp_path).answer_key
    bug_keys = {b.bug_id: b.key for b in key.bugs}
    assertion_bugs = {"axi_handshake", "fifo_overflow", "fsm_illegal"}

    checked = 0
    for run in key.runs:
        for truth in run.failing:
            if bug_keys.get(truth.root_cause or "") not in assertion_bugs:
                continue
            first = parser.parse_file(tmp_path / truth.log_file).first_error
            assert first is not None and first.kind == "assertion", truth.log_file
            assert first.source and first.source.startswith("tb_top.dut."), truth.log_file
            assert first.file and first.file.startswith("rtl/"), truth.log_file
            assert first.file_line is not None
            # The human-readable assertion message, not the raw first line of the report.
            assert not first.message.startswith(('"', "xmsim:", "#", "Offending")), first.message
            assert len(first.message) > 10
            checked += 1
    assert checked, "dataset should contain assertion failures"
