"""The normalized log model: every log format is converted into these same few types.

Downstream analysis (signatures, clustering, ranking) only ever sees `ParsedLog` and `LogEvent`,
so it works the same for VCS, Xcelium, Questa, or a team's custom format.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal

EventKind = Literal["message", "assertion", "tool", "crash"]
"""message: testbench (UVM) report. assertion: SVA failure in the design. tool: simulator/launcher
error (license, elaboration). crash: the simulator or the job died."""

Status = Literal["pass", "fail", "unknown"]

_SEVERITY_WORDS = {
    "uvm_info": "INFO",
    "info": "INFO",
    "note": "INFO",
    "debug": "INFO",
    "uvm_warning": "WARNING",
    "warning": "WARNING",
    "warn": "WARNING",
    "uvm_error": "ERROR",
    "error": "ERROR",
    "err": "ERROR",
    "uvm_fatal": "FATAL",
    "fatal": "FATAL",
    "critical": "FATAL",
}


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2
    FATAL = 3

    @classmethod
    def parse(cls, text: str) -> Severity | None:
        """Map the many spellings tools use ('UVM_ERROR', 'Error', 'ERR', 'Fatal'...) to a level."""
        name = _SEVERITY_WORDS.get(text.strip().lower())
        return cls[name] if name else None


@dataclass(slots=True)
class LogEvent:
    """One reported message. Multi-line reports (e.g. assertion failures) become one event."""

    line: int  # 1-based line where the report starts
    severity: Severity
    kind: EventKind
    rule: str  # name of the format rule that recognized it (useful when debugging a format)
    message: str
    end_line: int = 0  # last line of a multi-line report (equals `line` for single-line reports)
    source: str | None = None  # reporting component or assertion scope, e.g. uvm_test_top.env.scbd
    msg_id: str | None = None  # UVM message ID or tool error code, e.g. SCBD_MISMATCH
    file: str | None = None  # source file that issued the report
    file_line: int | None = None
    time_ps: int | None = None  # simulation time, normalized to picoseconds
    details: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.severity >= Severity.ERROR


@dataclass(slots=True)
class ParsedLog:
    """Everything the engine learned from one log file (one simulation run of one test)."""

    path: Path
    format: str
    total_lines: int
    status: Status
    status_reason: str  # plain-language explanation of the status, shown to users
    completed: bool | None  # did the simulator finish normally? None if the format can't tell
    test_name: str | None
    seed: int | None
    severity_counts: dict[str, int]
    events: list[LogEvent]  # WARNING and above; INFO lines are only counted
    dropped_events: int = 0  # events beyond the per-log cap (still counted in severity_counts)

    @property
    def errors(self) -> list[LogEvent]:
        return [e for e in self.events if e.is_error]

    @property
    def first_error(self) -> LogEvent | None:
        return next((e for e in self.events if e.is_error), None)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = self.path.as_posix()
        for event in data["events"]:
            event["severity"] = Severity(event["severity"]).name
        return data
