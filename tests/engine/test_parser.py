from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from verimind.engine import LogParser, Severity, discover_logs

VCS_HEADER = [
    "Command: ./simv +UVM_TESTNAME=uart_smoke_test +ntb_random_seed=77 -l x.log",
    "Chronologic VCS simulator copyright 1991-2024",
]
VCS_FOOTER = ["           V C S   S i m u l a t i o n   R e p o r t", "Time: 100 ps"]


@pytest.fixture(scope="module")
def parser() -> LogParser:
    return LogParser()


def parse(parser: LogParser, lines: list[str], name: str = "t.log", fmt: str | None = None):  # noqa: ANN201
    return parser.parse_lines(iter(lines), path=Path(name), format_name=fmt)


def test_multiline_vcs_assertion_becomes_one_event(parser: LogParser) -> None:
    lines = [
        *VCS_HEADER,
        '"rtl/uart/uart_fifo.sv", 45: tb_top.dut.u_uart.u_rx_fifo.a_no_overflow: started at 1000ps failed at 1200ps',
        "\tOffending '!(push && full)'",
        'Error: "rtl/uart/uart_fifo.sv", 45: tb_top.dut.u_uart.u_rx_fifo.a_no_overflow: at time 1200 ps',
        "FIFO overflow: push while full (depth=16)",
        "UVM_INFO tb/x.sv(1) @ 1300: uvm_test_top [X] after",
        *VCS_FOOTER,
    ]
    result = parse(parser, lines)
    (event,) = result.errors
    assert (event.line, event.end_line) == (3, 6)
    assert event.kind == "assertion"
    assert event.message == "FIFO overflow: push while full (depth=16)"
    assert event.extra["expr"] == "!(push && full)"
    assert event.file == "rtl/uart/uart_fifo.sv" and event.file_line == 45
    assert event.time_ps == 1200
    assert result.severity_counts["INFO"] == 1  # the line after the assertion was still parsed


def test_time_is_normalized_to_picoseconds(parser: LogParser) -> None:
    lines = [
        "xrun(64): 24.03-s004: (c) Copyright 1995-2024 Cadence Design Systems, Inc.",
        "UVM_ERROR tb/s.sv(9) @ 8899 NS: uvm_test_top.env.scbd [SCBD] bad",
    ]
    (event,) = parse(parser, lines).errors
    assert event.time_ps == 8_899_000


def test_truncated_log_is_a_failure_even_without_error_lines(parser: LogParser) -> None:
    lines = [*VCS_HEADER, "UVM_INFO tb/x.sv(1) @ 10: uvm_test_top [X] working"]
    result = parse(parser, lines)
    assert result.status == "fail"
    assert result.completed is False
    assert "ends before the simulator finished" in result.status_reason


def test_clean_finished_log_passes(parser: LogParser) -> None:
    lines = [
        *VCS_HEADER,
        "UVM_INFO tb/t.sv(131) @ 900: uvm_test_top [TEST_DONE] ** TEST PASSED **",
        "UVM_ERROR :    0",
        *VCS_FOOTER,
    ]
    result = parse(parser, lines)
    assert (result.status, result.test_name, result.seed) == ("pass", "uart_smoke_test", 77)


def test_noise_that_mentions_errors_is_not_an_error(parser: LogParser) -> None:
    lines = [
        *VCS_HEADER,
        "UVM_INFO tb/s.sv(260) @ 50: uvm_test_top.env.scbd [STATS] Running totals: error_count=0 warning_count=2",
        "UVM_INFO tb/e.sv(288) @ 60: uvm_test_top.env.ecc_chk [ECC_CORR] Corrected 3 single-bit errors",
        "UVM_ERROR :    0",
        "UVM_FATAL :    0",
        *VCS_FOOTER,
    ]
    assert parse(parser, lines).errors == []


def test_unknown_format_falls_back_to_generic(parser: LogParser) -> None:
    lines = [
        "2026-03-07T10:00:00Z | INFO | module=aes | test=aes_test_01 | status=pass",
        "2026-03-07T10:00:01Z | ERROR | module=aes | test=aes_test_01 | status=fail",
        "ERROR: something else went wrong",
    ]
    result = parse(parser, lines, name="aes_test_01_s123.log")
    assert result.format == "generic"
    assert [e.line for e in result.errors] == [2, 3]
    assert result.status == "fail"
    assert result.test_name == "aes_test_01"  # taken from the filename


def test_event_cap_keeps_counting(tmp_path: Path) -> None:
    lines = [*VCS_HEADER] + [f"UVM_ERROR tb/s.sv(1) @ {i}: uvm_test_top [E] err {i}" for i in range(50)]
    result = LogParser(max_events=10).parse_lines(iter(lines), path=Path("x.log"))
    assert len(result.events) == 10
    assert result.dropped_events == 40
    assert result.severity_counts["ERROR"] == 50


def test_gzipped_logs_and_discovery(tmp_path: Path, parser: LogParser) -> None:
    text = "\n".join([*VCS_HEADER, "UVM_ERROR tb/s.sv(1) @ 5: uvm_test_top [E] boom", *VCS_FOOTER])
    (tmp_path / "sub").mkdir()
    with gzip.open(tmp_path / "sub" / "a.log.gz", "wt", encoding="utf-8") as f:
        f.write(text)
    (tmp_path / "b.log").write_text(text)
    (tmp_path / "answer_key.json").write_text("{}")

    found = discover_logs(tmp_path)
    assert [p.name for p in found] == ["b.log", "a.log.gz"]
    assert all(r.errors[0].severity == Severity.ERROR for r in parser.parse_many(found))


def test_forced_format_overrides_detection(parser: LogParser) -> None:
    lines = ["UVM_ERROR tb/s.sv(1) @ 5: uvm_test_top [E] boom"]
    assert parse(parser, lines).format == "generic"
    assert parse(parser, lines, fmt="vcs").format == "vcs"
