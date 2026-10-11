from __future__ import annotations

import json
from pathlib import Path

from verimind.engine import Analyzer

HEADER = "Chronologic VCS simulator copyright 1991-2024\nCommand: ./simv +UVM_TESTNAME={test} +ntb_random_seed={seed}\n"
FOOTER = "           V C S   S i m u l a t i o n   R e p o r t\n"


def write_log(folder: Path, test: str, seed: int, body: list[str], finished: bool = True) -> None:
    text = HEADER.format(test=test, seed=seed) + "\n".join(body) + "\n" + (FOOTER if finished else "")
    (folder / f"{test}_s{seed}.log").write_text(text)


def mismatch(addr: str) -> str:
    return f"UVM_ERROR tb/dma_scoreboard.sv(214) @ 500: uvm_test_top.env.scbd [SCBD_MISMATCH] Data mismatch at {addr}"


def test_failures_group_into_ranked_issues(tmp_path: Path) -> None:
    timeout = "UVM_ERROR tb/drv.sv(156) @ 900: uvm_test_top.env.drv [RSP_TIMEOUT] Timeout after 5000 cycles"
    write_log(tmp_path, "dma_smoke_test", 1, [mismatch("0x10"), timeout, timeout])
    write_log(tmp_path, "dma_stress_test", 2, [mismatch("0x20")])
    write_log(tmp_path, "dma_random_test", 3, [mismatch("0x30"), mismatch("0x31")])
    write_log(tmp_path, "uart_smoke_test", 4, ["*** Job terminated by regression runner: out of memory ***"], False)
    write_log(tmp_path, "uart_reset_test", 5, ["UVM_INFO tb/t.sv(1) @ 1: uvm_test_top [TEST_DONE] ** TEST PASSED **"])

    report = Analyzer().analyze_dir(tmp_path)

    assert (report.summary.total_tests, report.summary.passed, report.summary.failed) == (5, 1, 4)
    top, second = report.issues
    assert top.title.startswith("[SCBD_MISMATCH] Data mismatch at <hex>")
    assert (top.test_count, top.category, top.score, top.rank) == (3, "design", 100.0, 1)
    assert top.location == "tb/dma_scoreboard.sv:214"
    assert top.occurrences == 4  # one test reports it twice
    assert any("RSP_TIMEOUT" in f for f in top.followed_by)
    assert second.category == "infrastructure" and second.score < top.score

    by_name = {t.test_name: t for t in report.tests}
    assert by_name["dma_smoke_test"].follow_on_errors == 2
    assert by_name["dma_smoke_test"].root_cause is not None and by_name["dma_smoke_test"].root_cause.line == 3
    assert by_name["uart_reset_test"].issue_id is None
    json.dumps(report.to_dict())  # the report must be JSON-serializable for the API
    assert "## 1. [SCBD_MISMATCH]" in report.to_markdown()


def test_failing_test_without_error_message_is_not_lost(tmp_path: Path) -> None:
    write_log(tmp_path, "cpu_core_perf_test", 9, ["UVM_INFO tb/x.sv(1) @ 1: uvm_test_top [X] working"], False)
    report = Analyzer().analyze_dir(tmp_path)
    (issue,) = report.issues
    assert issue.issue_id == "NOERR-truncated"
    assert issue.category == "unknown"
    assert report.tests[0].issue_id == issue.issue_id


def test_modules_use_block_names_seen_in_the_design(tmp_path: Path) -> None:
    assertion = [
        '"rtl/nand_ctrl/fsm.sv", 163: tb_top.dut.u_nand_ctrl.u_fsm.a_ok: started at 1ps failed at 2ps',
        "illegal state",
    ]
    write_log(tmp_path, "nand_ctrl_stress_test", 1, assertion)
    write_log(tmp_path, "nand_ctrl_smoke_test", 2, [])
    write_log(tmp_path, "spi_host_smoke_test", 3, [])
    modules = {t.test_name: t.module for t in Analyzer().analyze_dir(tmp_path).tests}
    assert modules == {
        "nand_ctrl_stress_test": "nand_ctrl",
        "nand_ctrl_smoke_test": "nand_ctrl",
        "spi_host_smoke_test": "spi",
    }


def test_clean_run_has_no_issues(tmp_path: Path) -> None:
    write_log(tmp_path, "aes_smoke_test", 1, [])
    report = Analyzer().analyze_dir(tmp_path)
    assert report.issues == [] and report.summary.passed == 1


def test_block_names_come_from_testbench_environments_too(tmp_path: Path) -> None:
    warning = "UVM_WARNING tb/m.sv(1) @ 5: uvm_test_top.env.axi_xbar_env.axi_agent.mon [SLOW] slow response"
    write_log(tmp_path, "axi_xbar_perf_test", 1, [warning])
    write_log(tmp_path, "axi_xbar_smoke_test", 2, [])
    modules = {t.module for t in Analyzer().analyze_dir(tmp_path).tests}
    assert modules == {"axi_xbar"}
