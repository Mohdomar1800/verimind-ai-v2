from __future__ import annotations

import pytest

from verimind.engine.classify import classify
from verimind.engine.models import LogEvent, Severity


@pytest.mark.parametrize(
    ("kind", "severity", "message", "time_ps", "expected"),
    [
        ("crash", Severity.FATAL, "process killed by signal 9 (out of memory)", None, "infrastructure"),
        ("tool", Severity.ERROR, "License checkout failed", None, "infrastructure"),
        ("assertion", Severity.ERROR, "FIFO overflow: push while full", 1200, "design"),
        ("message", Severity.FATAL, "Virtual interface must be set for: env.drv.vif", 0, "testbench"),
        ("message", Severity.FATAL, "Null object access: response item handle is null", 5000, "testbench"),
        ("message", Severity.ERROR, "Reference model predicted 4 beats but DUT returned 8", 9000, "testbench"),
        ("message", Severity.FATAL, "Unexpected configuration at time zero", 0, "testbench"),
        ("message", Severity.ERROR, "Data mismatch at addr 0x10: expected 0x1, actual 0x3", 9000, "design"),
        ("message", Severity.FATAL, "Expected interrupt never fired", None, "design"),  # no sim time known
    ],
)
def test_classification(kind: str, severity: Severity, message: str, time_ps: int | None, expected: str) -> None:
    event = LogEvent(line=1, severity=severity, kind=kind, rule="t", message=message, time_ps=time_ps)  # type: ignore[arg-type]
    category, reason = classify(event)
    assert category == expected
    assert reason


def test_missing_event_is_unknown() -> None:
    assert classify(None)[0] == "unknown"
