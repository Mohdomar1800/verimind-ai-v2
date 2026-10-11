"""How each simulator prints the same events.

Verification teams use one of three commercial simulators (Synopsys VCS, Cadence Xcelium, Siemens
Questa), and many teams also have an in-house regression runner with its own log style. The same
UVM error looks different in each, and assertion failures differ even more. The analysis engine
has to understand all of them, so the generator writes all of them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar

from verimind.synth.catalog import InfraKind, Severity

UVM_ROOT = "/tools/uvm-1.2/src/base/uvm_root.svh"
OOM_LINE = "*** Job terminated by regression runner: process killed by signal 9 (out of memory) ***"
SEVERITIES: tuple[Severity, ...] = ("UVM_INFO", "UVM_WARNING", "UVM_ERROR", "UVM_FATAL")


@dataclass(frozen=True)
class SimContext:
    test_name: str
    seed: int
    block: str
    log_name: str
    started_at: datetime


@dataclass(frozen=True)
class UvmEvent:
    severity: Severity
    time_ps: int
    file: str | None
    line: int
    component: str
    msg_id: str
    message: str


@dataclass(frozen=True)
class AssertionEvent:
    time_ps: int
    start_ps: int
    scope: str
    file: str
    line: int
    expr: str
    message: str


class LogFormat(ABC):
    name: ClassVar[str]

    def __init__(self, ctx: SimContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def header(self) -> list[str]: ...

    @abstractmethod
    def time(self, time_ps: int) -> str: ...

    @abstractmethod
    def assertion(self, ev: AssertionEvent) -> list[str]: ...

    @abstractmethod
    def infra(self, kind: InfraKind, time_ps: int) -> list[str]: ...

    @abstractmethod
    def footer(self, time_ps: int, passed: bool, sev_counts: Counter[str], assertion_failures: int) -> list[str]: ...

    def uvm(self, ev: UvmEvent) -> list[str]:
        location = f"{ev.file}({ev.line}) " if ev.file else ""
        return [f"{ev.severity} {location}@ {self.time(ev.time_ps)}: {ev.component} [{ev.msg_id}] {ev.message}"]

    def summary(self, sev_counts: Counter[str], id_counts: Counter[str]) -> list[str]:
        lines = ["", "--- UVM Report Summary ---", "", "** Report counts by severity"]
        lines += [f"{sev} : {sev_counts[sev]:5d}" for sev in SEVERITIES]
        lines.append("** Report counts by id")
        lines += [f"[{msg_id}] {count:5d}" for msg_id, count in sorted(id_counts.items())]
        return lines

    def _wallclock(self, time_ps: int) -> datetime:
        # Simulated time runs far slower than wall time: 1 ns of simulation ~ 1 ms of CPU.
        return self.ctx.started_at + timedelta(milliseconds=time_ps / 1000)


class VcsFormat(LogFormat):
    name = "vcs"

    def header(self) -> list[str]:
        c = self.ctx
        return [
            f"Command: ./simv +UVM_TESTNAME={c.test_name} +ntb_random_seed={c.seed} -l {c.log_name}",
            "Chronologic VCS simulator copyright 1991-2024",
            "Contains Synopsys proprietary information.",
            f"Compiler version W-2024.09-SP1; Runtime version W-2024.09-SP1;  {c.started_at:%b %d %H:%M %Y}",
        ]

    def time(self, time_ps: int) -> str:
        return str(time_ps)

    def assertion(self, ev: AssertionEvent) -> list[str]:
        return [
            f'"{ev.file}", {ev.line}: {ev.scope}: started at {ev.start_ps}ps failed at {ev.time_ps}ps',
            f"\tOffending '{ev.expr}'",
            f'Error: "{ev.file}", {ev.line}: {ev.scope}: at time {ev.time_ps} ps',
            ev.message,
        ]

    def infra(self, kind: InfraKind, time_ps: int) -> list[str]:
        if kind == "license":
            return [
                "Error-[LICENSE_FAIL] License checkout failed",
                "  Unable to checkout license feature 'VCSRuntime_Net' from server 27000@lic-srv01.",
                "  Please contact your system administrator.",
            ]
        if kind == "segfault":
            return [
                "Error-[SIGSEGV] Simulation process crashed",
                f"  Segmentation fault (core dumped) at simulation time {time_ps} ps",
            ]
        return [OOM_LINE]

    def footer(self, time_ps: int, passed: bool, sev_counts: Counter[str], assertion_failures: int) -> list[str]:
        cpu = 0.8 + time_ps / 2e8
        return [
            f'$finish called from file "{UVM_ROOT}", line 527.',
            f"$finish at simulation time {time_ps:>20d}",
            "           V C S   S i m u l a t i o n   R e p o r t",
            f"Time: {time_ps} ps",
            f"CPU Time: {cpu:12.3f} seconds;       Data structure size:   52.3Mb",
            f"{self.ctx.started_at + timedelta(seconds=cpu):%a %b %d %H:%M:%S %Y}",
        ]


class XceliumFormat(LogFormat):
    name = "xcelium"

    def header(self) -> list[str]:
        c = self.ctx
        return [
            "xrun(64): 24.03-s004: (c) Copyright 1995-2024 Cadence Design Systems, Inc.",
            f"TOOL:\txrun(64)\t24.03-s004: Started on {c.started_at:%b %d, %Y at %H:%M:%S} UTC",
            f"xrun -R +UVM_TESTNAME={c.test_name} -svseed {c.seed} -l {c.log_name}",
            "Loading snapshot worklib.tb_top:sv .................... Done",
            "xcelium> run",
        ]

    def time(self, time_ps: int) -> str:
        return f"{time_ps // 1000} NS"

    def assertion(self, ev: AssertionEvent) -> list[str]:
        cycles = max(1, (ev.time_ps - ev.start_ps) // 5000)
        return [
            f"xmsim: *E,ASRTST ({ev.file},{ev.line}): (time {ev.time_ps // 1000} NS) Assertion {ev.scope} "
            f"has failed ({cycles} cycles, starting {ev.start_ps // 1000} NS)",
            f"    {ev.message}",
        ]

    def infra(self, kind: InfraKind, time_ps: int) -> list[str]:
        if kind == "license":
            return ["xrun: *F,NOLICN: Unable to checkout license for feature 'Xcelium_Single_Core'."]
        if kind == "segfault":
            return [
                "xmsim: *F,INTERR: INTERNAL EXCEPTION",
                "-----------------------------------------------------------------",
                "The tool has encountered an unexpected condition and must exit.",
                f"Simulation time: {time_ps // 1000} NS",
            ]
        return [OOM_LINE]

    def footer(self, time_ps: int, passed: bool, sev_counts: Counter[str], assertion_failures: int) -> list[str]:
        seconds = int(1 + time_ps / 2e8)
        return [
            f"Simulation complete via $finish(1) at time {time_ps // 1000} NS + 0",
            f"{UVM_ROOT}:527     $finish;",
            "xcelium> exit",
            f"TOOL:\txrun(64)\t24.03-s004: Exiting on "
            f"{self.ctx.started_at + timedelta(seconds=seconds):%b %d, %Y at %H:%M:%S} UTC "
            f"(total: 00:{seconds // 60:02d}:{seconds % 60:02d})",
        ]


class QuestaFormat(LogFormat):
    """Questa writes a transcript in which every simulator line starts with '# '."""

    name = "questa"

    def header(self) -> list[str]:
        c = self.ctx
        return [
            f'# vsim -c -sv_seed {c.seed} +UVM_TESTNAME={c.test_name} work.tb_top -do "run -all; quit"',
            f"# Start time: {c.started_at:%H:%M:%S} on {c.started_at:%b %d,%Y}",
            "# Loading sv_std.std",
            "# Loading work.tb_top(fast)",
        ]

    def time(self, time_ps: int) -> str:
        return f"{time_ps // 1000} ns"

    def uvm(self, ev: UvmEvent) -> list[str]:
        return [f"# {line}" for line in super().uvm(ev)]

    def summary(self, sev_counts: Counter[str], id_counts: Counter[str]) -> list[str]:
        return [f"# {line}".rstrip() for line in super().summary(sev_counts, id_counts)]

    def assertion(self, ev: AssertionEvent) -> list[str]:
        return [
            f"# ** Error: {ev.message}",
            f"#    Time: {ev.time_ps // 1000} ns Started: {ev.start_ps // 1000} ns  Scope: {ev.scope} "
            f"File: {ev.file} Line: {ev.line}",
        ]

    def infra(self, kind: InfraKind, time_ps: int) -> list[str]:
        if kind == "license":
            return ["# ** Fatal: (vsim-3826) Unable to checkout a license for feature 'qhsimvl'."]
        if kind == "segfault":
            return [
                "# ** Fatal: (SIGSEGV) Bad handle or reference.",
                f"#    Time: {time_ps // 1000} ns  Iteration: 1  Process: /tb_top/#INITIAL#12",
            ]
        return [OOM_LINE]

    def footer(self, time_ps: int, passed: bool, sev_counts: Counter[str], assertion_failures: int) -> list[str]:
        errors = sev_counts["UVM_ERROR"] + sev_counts["UVM_FATAL"] + assertion_failures
        end = self.ctx.started_at + timedelta(seconds=1 + time_ps / 2e8)
        return [
            f"# ** Note: $finish    : {UVM_ROOT}(527)",
            f"#    Time: {time_ps // 1000} ns  Iteration: 61  Instance: /tb_top",
            f"# End time: {end:%H:%M:%S} on {end:%b %d,%Y}",
            f"# Errors: {errors}, Warnings: {sev_counts['UVM_WARNING']}",
        ]


class InHouseFormat(LogFormat):
    """A company-specific runner that prints its own log style instead of raw UVM messages."""

    name = "inhouse"
    LEVELS: ClassVar[dict[str, str]] = {
        "UVM_INFO": "INFO",
        "UVM_WARNING": "WARN",
        "UVM_ERROR": "ERROR",
        "UVM_FATAL": "FATAL",
    }

    def _line(self, time_ps: int, level: str, source: str, text: str) -> str:
        stamp = self._wallclock(time_ps).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"{stamp} {level:<5} {source}: {text}"

    def header(self) -> list[str]:
        c = self.ctx
        return [
            "=== regrun v3.2 (in-house regression runner) ===",
            f"test={c.test_name} seed={c.seed} block={c.block} log={c.log_name}",
        ]

    def time(self, time_ps: int) -> str:
        return f"{time_ps // 1000}ns"

    def uvm(self, ev: UvmEvent) -> list[str]:
        return [self._line(ev.time_ps, self.LEVELS[ev.severity], ev.component, f"[{ev.msg_id}] {ev.message}")]

    def assertion(self, ev: AssertionEvent) -> list[str]:
        return [self._line(ev.time_ps, "ERROR", ev.scope, f"[ASSERT] {ev.message} ({ev.file}:{ev.line})")]

    def infra(self, kind: InfraKind, time_ps: int) -> list[str]:
        if kind == "license":
            text = "[LICENSE] License server lic-srv01 unreachable, simulator could not start"
        elif kind == "segfault":
            text = "[CRASH] Simulator exited with signal 11 (segmentation fault)"
        else:
            return [OOM_LINE]
        return [self._line(time_ps, "FATAL", "regrun", text)]

    def summary(self, sev_counts: Counter[str], id_counts: Counter[str]) -> list[str]:
        counts = " ".join(f"{self.LEVELS[sev].lower()}={sev_counts[sev]}" for sev in SEVERITIES)
        return [f"Summary: {counts}"]

    def footer(self, time_ps: int, passed: bool, sev_counts: Counter[str], assertion_failures: int) -> list[str]:
        return [f"RESULT: {'PASSED' if passed else 'FAILED'} (sim time {time_ps // 1000} ns)"]


FORMATS: dict[str, type[LogFormat]] = {f.name: f for f in (VcsFormat, XceliumFormat, QuestaFormat, InHouseFormat)}
