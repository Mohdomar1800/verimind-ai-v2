"""Simulate one test: produce its log lines and record the truth about them.

A test goes through the usual UVM phases: build, reset, run (traffic), report. If a bug is planned,
the root-cause error appears partway through the run. Cascade errors follow, which is the noise
an engineer normally has to read through. The simulator records the exact line numbers as it
writes them, so the answer key is correct by construction.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from verimind.synth import catalog as cat
from verimind.synth.catalog import Assertion, Block, BugTemplate, InfraTemplate, Msg, Severity, TemplateValues
from verimind.synth.formats import AssertionEvent, LogFormat, UvmEvent

PS_PER_NS = 1000


@dataclass(frozen=True)
class BugInstance:
    """A catalog bug placed into a concrete block for one dataset."""

    bug_id: str
    template: BugTemplate
    block: Block
    hit_rate: float
    introduced_in: int
    fixed_in: int | None

    @property
    def title(self) -> str:
        return self.template.title.format(block=self.block.name, agent=self.block.agent)

    def active_in(self, run_index: int) -> bool:
        return self.introduced_in <= run_index and (self.fixed_in is None or run_index < self.fixed_in)

    def affects(self, block: Block, scenario: str) -> bool:
        scenarios = self.template.scenarios
        return block == self.block and (scenarios is None or scenario in scenarios)


@dataclass(frozen=True)
class TestPlan:
    __test__ = False  # stop pytest from treating this as a test class

    test_name: str
    seed: int
    block: Block
    scenario: str
    bug: BugInstance | None = None
    infra: InfraTemplate | None = None

    @property
    def root_cause(self) -> str | None:
        if self.infra:
            return self.infra.bug_id
        return self.bug.bug_id if self.bug else None


@dataclass
class SimResult:
    lines: list[str]
    error_lines: list[int]

    @property
    def passed(self) -> bool:
        return not self.error_lines


@dataclass
class TestbenchSimulator:
    plan: TestPlan
    fmt: LogFormat
    rng: random.Random
    lines_per_test: int
    lines: list[str] = field(default_factory=list)
    error_lines: list[int] = field(default_factory=list)
    sev_counts: Counter[str] = field(default_factory=Counter)
    id_counts: Counter[str] = field(default_factory=Counter)
    assertion_failures: int = 0
    time_ps: int = 0

    # -- output helpers ------------------------------------------------------------------------

    def _emit(self, lines: list[str], is_error: bool = False) -> None:
        if is_error:
            self.error_lines.append(len(self.lines) + 1)
        self.lines.extend(lines)

    def _values(self, **fixed: object) -> TemplateValues:
        return TemplateValues(self.rng, self.plan.block, **fixed)

    def uvm(self, severity: Severity, msg: Msg, **fixed: object) -> None:
        v = self._values(timeout=self.time_ps, **fixed)
        ev = UvmEvent(
            severity=severity,
            time_ps=self.time_ps,
            file=cat.render(msg.file, v) if msg.file else None,
            line=msg.line,
            component=cat.render(msg.component, v),
            msg_id=cat.render(msg.msg_id, v),
            message=cat.render(msg.text, v),
        )
        self.sev_counts[severity] += 1
        self.id_counts[ev.msg_id] += 1
        self._emit(self.fmt.uvm(ev), is_error=severity in ("UVM_ERROR", "UVM_FATAL"))

    def assertion(self, a: Assertion) -> None:
        v = self._values()
        block = self.plan.block.name
        ev = AssertionEvent(
            time_ps=self.time_ps,
            start_ps=max(0, self.time_ps - self.rng.randint(1, 20) * 5 * PS_PER_NS),
            scope=f"tb_top.dut.u_{block}.{a.instance}.{a.name}",
            file=cat.render(a.file, v),
            line=a.line,
            expr=a.expr,
            message=cat.render(a.text, v),
        )
        self.assertion_failures += 1
        self._emit(self.fmt.assertion(ev), is_error=True)

    def advance(self, min_ns: int = 10, max_ns: int = 500) -> None:
        self.time_ps += self.rng.randint(min_ns, max_ns) * PS_PER_NS

    # -- phases ----------------------------------------------------------------------------------

    def run(self) -> SimResult:
        plan, bug, infra = self.plan, self.plan.bug, self.plan.infra
        self._emit(self.fmt.header())

        if infra and infra.at_start:
            return self._crash(infra)

        # Build phase.
        self.uvm("UVM_INFO", cat.MSG_RUN_TEST, test=plan.test_name)
        self.uvm("UVM_INFO", cat.NOISE_CFG)
        self.uvm("UVM_INFO", cat.MSG_ENV_BUILD)
        if bug and bug.template.at_time_zero:
            self._root_cause(bug.template)
            return self._finish()

        # Reset.
        self.uvm("UVM_INFO", cat.MSG_RESET_ON)
        self.time_ps = 100 * PS_PER_NS
        self.uvm("UVM_INFO", cat.MSG_RESET_OFF)

        # Run phase: traffic, with the failure injected partway through.
        length = cat.SCENARIO_LENGTH[plan.scenario] * self.rng.uniform(0.6, 1.4)
        n_events = max(10, int(self.lines_per_test * length))
        fail_at = int(n_events * self.rng.uniform(0.2, 0.85)) if (bug or infra) else -1
        pending: list[Msg] = []
        i = 0
        while i < n_events:
            self.advance()
            if i == fail_at:
                if infra:
                    return self._crash(infra)
                assert bug is not None
                self._root_cause(bug.template)
                if bug.template.primary_severity == "UVM_FATAL":
                    return self._finish()
                pending = self._expand_cascade(bug.template, at_end=False)
                if bug.template.fatal:  # the test hangs: only a little more activity before the timeout
                    n_events = min(n_events, i + 1 + len(pending) * self.rng.randint(2, 5) + self.rng.randint(3, 10))
            elif pending and self.rng.random() < 0.35:
                self.uvm("UVM_ERROR", pending.pop(0))
            else:
                self._everyday_message(i)
            i += 1
        for msg in pending:
            self.advance()
            self.uvm("UVM_ERROR", msg)

        if bug and bug.template.fatal:
            self.time_ps += self.rng.randint(500_000, 2_000_000) * PS_PER_NS
            self.uvm("UVM_FATAL", bug.template.fatal)
            return self._finish()

        # Report phase.
        self.advance()
        if bug:
            for msg in self._expand_cascade(bug.template, at_end=True):
                self.uvm("UVM_ERROR", msg)
        mismatches = self.id_counts["SCBD_MISMATCH"]
        self.uvm("UVM_INFO", cat.MSG_SCBD_REPORT, matches=self.id_counts["SCBD"], mismatches=mismatches)
        self.uvm("UVM_INFO", cat.MSG_TEST_DONE, result="FAILED" if self.error_lines else "PASSED")
        return self._finish()

    def _everyday_message(self, i: int) -> None:
        rng, block, scenario = self.rng, self.plan.block.name, self.plan.scenario
        if i and i % 150 == 0:
            self.uvm(*cat.NOISE[2])  # periodic "error_count=0" statistics line
        elif rng.random() < 0.02:
            self.uvm(*cat.NOISE[rng.randrange(2)])
        elif scenario == "error_inject" and rng.random() < 0.04:
            self.uvm("UVM_INFO", cat.NOISE_NEGATIVE_TEST)
        elif block in cat.ECC_BLOCKS and rng.random() < 0.03:
            self.uvm("UVM_INFO", cat.NOISE_ECC_CORRECTED)
        else:
            msgs, weights = zip(*cat.TRAFFIC, strict=True)
            # `n` numbers scoreboard comparisons in order, as a real scoreboard does.
            self.uvm("UVM_INFO", rng.choices(msgs, weights)[0], n=self.id_counts["SCBD"] + 1)

    def _root_cause(self, template: BugTemplate) -> None:
        if isinstance(template.primary, Assertion):
            self.assertion(template.primary)
        else:
            self.uvm(template.primary_severity, template.primary)

    def _expand_cascade(self, template: BugTemplate, at_end: bool) -> list[Msg]:
        out: list[Msg] = []
        for step in template.cascade:
            if step.at_end == at_end:
                out.extend([step.msg] * self.rng.randint(*step.repeat))
        return out

    def _crash(self, infra: InfraTemplate) -> SimResult:
        """Infrastructure failures cut the log off: no report summary, no footer."""
        self._emit(self.fmt.infra(infra.kind, self.time_ps), is_error=True)
        return SimResult(self.lines, self.error_lines)

    def _finish(self) -> SimResult:
        """Report summary and simulator footer. UVM prints these even when a UVM_FATAL aborts the test."""
        self._emit(self.fmt.summary(self.sev_counts, self.id_counts))
        self._emit(self.fmt.footer(self.time_ps, not self.error_lines, self.sev_counts, self.assertion_failures))
        return SimResult(self.lines, self.error_lines)
