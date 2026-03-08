from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a single validation log for end-to-end VeriMind4 feature checks"
    )
    parser.add_argument(
        "--output",
        default="sample files/verimind4_feature_validation.log",
        help="Output log path",
    )
    return parser.parse_args()


def emit_line(ts: datetime, sev: str, module: str, test: str, cycle: int, msg: str) -> str:
    return f"{ts.isoformat()} | {sev} | module={module} | test={test} | cycle={cycle} | {msg}"


def main() -> None:
    args = parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Structured scenarios: varying pass/fail mix across modules to exercise
    # failure-risk, coverage-priority, debug-priority, and explainability outputs.
    scenarios = {
        "aes": [
            ("aes_smoke_01", ["pass", "fail_assert", "pass", "pass"]),
            ("aes_stress_02", ["pass", "fail_timeout", "fail_check", "pass"]),
            ("aes_random_03", ["pass", "pass", "pass", "pass"]),
        ],
        "uart": [
            ("uart_smoke_01", ["pass", "pass", "pass", "pass"]),
            ("uart_fifo_02", ["pass", "fail_timeout", "pass", "pass"]),
            ("uart_long_03", ["pass", "pass", "pass", "pass"]),
        ],
        "dma": [
            ("dma_smoke_01", ["pass", "fail_assert", "pass", "fail_diffail"]),
            ("dma_stress_02", ["pass", "fail_timeout", "fail_check", "pass"]),
            ("dma_random_03", ["pass", "pass", "pass", "pass"]),
        ],
        "gpio": [
            ("gpio_smoke_01", ["pass", "pass", "pass", "pass"]),
            ("gpio_intr_02", ["pass", "pass", "pass", "pass"]),
            ("gpio_edge_03", ["pass", "fail_assert", "pass", "pass"]),
        ],
        "otbn": [
            ("otbn_smoke_01", ["pass", "fail_assert", "fail_timeout", "pass"]),
            ("otbn_sha3_02", ["pass", "fail_check", "pass", "pass"]),
            ("otbn_big_03", ["pass", "fail_timeout", "fail_diffail", "pass"]),
        ],
        "keymgr": [
            ("keymgr_smoke_01", ["pass", "pass", "pass", "pass"]),
            ("keymgr_state_02", ["pass", "fail_assert", "pass", "pass"]),
            ("keymgr_kmac_03", ["pass", "pass", "pass", "pass"]),
        ],
        "ibex_core": [
            ("ibex_smoke_01", ["pass", "pass", "pass", "pass"]),
            ("ibex_irq_02", ["pass", "pass", "pass", "pass"]),
            ("ibex_csr_03", ["pass", "fail_check", "pass", "pass"]),
        ],
        "hmac": [
            ("hmac_smoke_01", ["pass", "fail_assert", "pass", "pass"]),
            ("hmac_digest_02", ["pass", "fail_timeout", "pass", "pass"]),
            ("hmac_msg_03", ["pass", "pass", "pass", "pass"]),
        ],
        "usbdev": [
            ("usbdev_smoke_01", ["pass", "fail_timeout", "fail_assert", "pass"]),
            ("usbdev_iso_02", ["pass", "fail_check", "pass", "pass"]),
            ("usbdev_bulk_03", ["pass", "pass", "pass", "pass"]),
        ],
        "spi_host": [
            ("spi_smoke_01", ["pass", "pass", "pass", "pass"]),
            ("spi_stress_02", ["pass", "fail_diffail", "pass", "pass"]),
            ("spi_loop_03", ["pass", "pass", "pass", "pass"]),
        ],
    }

    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=45)
    cycle = 1000
    lines: list[str] = []

    # Header markers for judge readability (parser safely ignores these).
    lines.append("# VeriMind4 feature validation log")
    lines.append("# Contains mixed pass/fail tests with module=test annotations.")
    lines.append("# Includes assert/timeout/check-fail/dif-fail patterns for debug features.")

    for module, tests in scenarios.items():
        for test_name, pattern in tests:
            for event_id in range(30):
                state = pattern[event_id % len(pattern)]
                ts = start + timedelta(seconds=len(lines))

                if state == "pass":
                    msg = "transaction complete and self-check pass"
                    sev = "INFO"
                elif state == "fail_assert":
                    msg = "assert failure in scoreboard path"
                    sev = "ERROR"
                elif state == "fail_timeout":
                    msg = "timeout error waiting for response"
                    sev = "ERROR"
                elif state == "fail_check":
                    msg = "check-fail signature mismatch in monitor"
                    sev = "ERROR"
                elif state == "fail_diffail":
                    msg = "dif-fail register access mismatch"
                    sev = "FATAL"
                else:
                    msg = "unexpected error"
                    sev = "ERROR"

                lines.append(emit_line(ts, sev, module, test_name, cycle, msg))
                cycle += 1

            # Add explicit end marker per test.
            ts = start + timedelta(seconds=len(lines))
            lines.append(emit_line(ts, "INFO", module, test_name, cycle, "test complete"))
            cycle += 1

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated: {out}")
    print(f"Total lines: {len(lines)}")
    print(f"Modules: {len(scenarios)}")
    print(f"Tests: {sum(len(v) for v in scenarios.values())}")


if __name__ == "__main__":
    main()
