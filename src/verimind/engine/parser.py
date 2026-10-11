"""Streaming log parser: reads a log line by line and produces a `ParsedLog`.

Memory use doesn't depend on file size. Only warnings and errors are kept as events; INFO
lines are just counted. Gzipped logs (`*.log.gz`) are read transparently.
"""

from __future__ import annotations

import gzip
import io
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from itertools import chain, islice
from pathlib import Path
from typing import TextIO

from verimind.engine.models import LogEvent, ParsedLog, Severity, Status
from verimind.engine.registry import TIME_UNITS_PS, CompiledFormat, CompiledRule, FormatRegistry

LOG_PATTERNS = ("*.log", "*.log.gz", "*.out", "*.out.gz", "*.txt", "*.txt.gz")
HEADER_SCAN_LINES = 200  # test name / seed are looked for only near the top
MAX_MESSAGE_CHARS = 2000
_KNOWN_GROUPS = {"severity", "message", "source", "msg_id", "file", "file_line", "time", "unit", "detail"}


def open_log(path: Path) -> TextIO:
    """Open a log as text, tolerating bad bytes (simulators sometimes print binary junk)."""
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def discover_logs(root: Path, patterns: Iterable[str] = LOG_PATTERNS) -> list[Path]:
    """All log files under `root` (or `root` itself if it is a file), sorted for stable output."""
    if root.is_file():
        return [root]
    found = {p for pattern in patterns for p in root.rglob(pattern) if p.is_file()}
    return sorted(found)


class _LogState:
    """Mutable state while one file is being parsed."""

    def __init__(self, fmt: CompiledFormat, max_events: int) -> None:
        self.fmt = fmt
        self.max_events = max_events
        self.events: list[LogEvent] = []
        self.dropped = 0
        self.counts: Counter[str] = Counter()
        self.test_name: str | None = None
        self.seed: int | None = None
        self.verdict: Status | None = None
        self.completed = False
        # A multi-line report in progress: the event, the rule that started it, and the next step.
        self.pending: LogEvent | None = None
        self.pending_rule: CompiledRule | None = None
        self.step_index = 0
        self.fallback_message = ""

    # -- per line ------------------------------------------------------------------------------

    def feed(self, line_no: int, line: str) -> None:
        if self.pending is not None and self._continue(line_no, line):
            return
        if line_no <= HEADER_SCAN_LINES:
            self._scan_header(line)
        if ("PASS" in line or "FAIL" in line) and self.fmt.verdict_pass is not None:
            if self.fmt.verdict_fail is not None and self.fmt.verdict_fail.search(line):
                self.verdict = "fail"
            elif self.fmt.verdict_pass.search(line):
                self.verdict = "pass"
        if self.fmt.end_marker is not None and self.fmt.end_marker.search(line):
            self.completed = True
        for rule in self.fmt.rules:
            if rule.contains is not None and rule.contains not in line:
                continue
            match = rule.regex.match(line)
            if match:
                self._start_event(line_no, line, rule, match)
                return

    def _scan_header(self, line: str) -> None:
        if self.test_name is None:
            for regex in self.fmt.test_name:
                if m := regex.search(line):
                    self.test_name = m.group("test")
                    break
        if self.seed is None:
            for regex in self.fmt.seed:
                if m := regex.search(line):
                    self.seed = int(m.group("seed"))
                    break

    def _start_event(self, line_no: int, line: str, rule: CompiledRule, match: re.Match[str]) -> None:
        if rule.severity is not None:
            severity = rule.severity
        else:
            severity = Severity.parse(match.group("severity") or "") or Severity.INFO
        if severity < Severity.WARNING and not rule.steps:
            self.counts[severity.name] += 1  # fast path: INFO lines are ~90% of a log and only counted
            return
        groups = match.groupdict()
        event = LogEvent(line=line_no, end_line=line_no, severity=severity, kind=rule.kind, rule=rule.name, message="")
        self._merge(event, groups, rule.time_unit_ps)
        self.fallback_message = line.strip()[:MAX_MESSAGE_CHARS]  # used only if no message is captured
        if rule.steps:
            self.pending, self.pending_rule, self.step_index = event, rule, 0
        else:
            self._finish(event)

    def _continue(self, line_no: int, line: str) -> bool:
        """Try to absorb `line` into the pending multi-line report. Returns True if absorbed."""
        assert self.pending is not None and self.pending_rule is not None
        steps = self.pending_rule.steps
        while self.step_index < len(steps):
            step = steps[self.step_index]
            match = step.regex.match(line)
            if match:
                self._merge(self.pending, match.groupdict(), self.pending_rule.time_unit_ps, from_step=True)
                self.pending.end_line = line_no
                if step.kind:
                    self.pending.kind = step.kind
                if not step.repeat:
                    self.step_index += 1
                if self.step_index == len(steps):
                    self._finish_pending()
                return True
            if step.optional or step.repeat:
                self.step_index += 1  # skip it and try the next step on the same line
                continue
            break
        self._finish_pending()
        return False

    # -- events --------------------------------------------------------------------------------

    @staticmethod
    def _merge(event: LogEvent, groups: dict[str, str | None], unit_ps: float, from_step: bool = False) -> None:
        def take(name: str) -> str | None:
            value = groups.get(name)
            return value.strip() if value else None

        if (message := take("message")) and not (from_step and event.message):
            event.message = message[:MAX_MESSAGE_CHARS]
        if detail := take("detail"):
            event.details.append(detail[:MAX_MESSAGE_CHARS])
        event.source = event.source or take("source")
        event.msg_id = event.msg_id or take("msg_id")
        event.file = event.file or take("file")
        if event.file_line is None and (file_line := take("file_line")):
            event.file_line = int(file_line)
        if event.time_ps is None and (time := take("time")):
            unit = take("unit")
            factor = TIME_UNITS_PS.get(unit.lower(), unit_ps) if unit else unit_ps
            event.time_ps = int(float(time) * factor)
        for name, value in groups.items():
            if name not in _KNOWN_GROUPS and value:
                event.extra[name] = value.strip()

    def _finish_pending(self) -> None:
        if self.pending is not None:
            self._finish(self.pending)
        self.pending, self.pending_rule, self.step_index = None, None, 0

    def _finish(self, event: LogEvent) -> None:
        if not event.message:
            event.message = self.fallback_message
        self.counts[event.severity.name] += 1
        if event.severity < Severity.WARNING:
            return
        if len(self.events) < self.max_events:
            self.events.append(event)
        else:
            self.dropped += 1

    # -- result --------------------------------------------------------------------------------

    def result(self, path: Path, total_lines: int) -> ParsedLog:
        self._finish_pending()
        completed: bool | None = self.completed if self.fmt.end_marker is not None else None
        status, reason = self._status(completed)
        return ParsedLog(
            path=path,
            format=self.fmt.name,
            total_lines=total_lines,
            status=status,
            status_reason=reason,
            completed=completed,
            test_name=self.test_name,
            seed=self.seed,
            severity_counts={s.name: self.counts[s.name] for s in Severity},
            events=self.events,
            dropped_events=self.dropped,
        )

    def _status(self, completed: bool | None) -> tuple[Status, str]:
        n_errors = self.counts["ERROR"] + self.counts["FATAL"]
        first = next((e for e in self.events if e.is_error), None)
        if n_errors:
            where = f"; the first is on line {first.line}" if first else ""
            noun = "error" if n_errors == 1 else "errors"
            return "fail", f"{n_errors} {noun} reported{where}."
        if self.verdict == "fail":
            return "fail", "The test reported FAILED, although no error lines were recognized."
        if completed is False:
            return "fail", "The log ends before the simulator finished (crash, kill, or timeout)."
        if self.verdict == "pass":
            return "pass", "The test reported PASSED and no errors were found."
        if completed:
            return "pass", "The simulation finished with no errors."
        return "unknown", "No errors were found, but the log has no pass/fail marker."


class LogParser:
    def __init__(self, registry: FormatRegistry | None = None, max_events: int = 20_000, detect_lines: int = 40):
        self.registry = registry or FormatRegistry.load()
        self.max_events = max_events
        self.detect_lines = detect_lines

    def parse_file(self, path: Path, format_name: str | None = None) -> ParsedLog:
        with open_log(path) as stream:
            return self.parse_lines(stream, path=path, format_name=format_name)

    def parse_lines(self, lines: Iterable[str], path: Path, format_name: str | None = None) -> ParsedLog:
        it = iter(lines)
        head = [line.rstrip("\r\n") for line in islice(it, self.detect_lines)]
        fmt = self.registry.get(format_name) if format_name else self.registry.detect(head)

        state = _LogState(fmt, self.max_events)
        line_no = 0
        for line_no, raw in enumerate(chain(head, (line.rstrip("\r\n") for line in it)), start=1):
            state.feed(line_no, raw)
        parsed = state.result(path, line_no)
        if parsed.test_name is None:
            parsed.test_name = _test_name_from_filename(path)
        return parsed

    def parse_many(self, paths: Iterable[Path], format_name: str | None = None) -> Iterator[ParsedLog]:
        for path in paths:
            yield self.parse_file(path, format_name)


_SEED_SUFFIX = re.compile(r"[_.-](?:s|seed)?\d{3,}$")


def _test_name_from_filename(path: Path) -> str:
    """Fallback when the log header doesn't name the test: 'uart_smoke_s1234.log' -> 'uart_smoke'."""
    name = path.name
    for suffix in (".gz", ".log", ".out", ".txt"):
        name = name.removesuffix(suffix)
    return _SEED_SUFFIX.sub("", name) or name
