"""Turn a folder of logs into a ranked list of distinct issues.

1. Parse every log (format auto-detected).
2. For each failing test, take the first error as the root cause; later errors are follow-ons.
3. Group failing tests whose root causes share a signature: one group = one issue.
4. Classify each issue (design / testbench / infrastructure) and rank it.

Ranking is deliberately simple and explainable:
    priority = number of tests the issue breaks x category weight
Design bugs weigh most (they block tape-out). Infrastructure problems weigh least (they route to the
farm/tools team and usually disappear on rerun). Scores are scaled so the top issue is 100.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from verimind.engine.classify import Category, classify
from verimind.engine.models import LogEvent, ParsedLog, Severity
from verimind.engine.parser import LogParser, discover_logs
from verimind.engine.report import AnalysisReport, Issue, IssueTest, RootCause, RunSummary, TestResult
from verimind.engine.signature import Signature, SignatureIndex

ProgressCallback = Callable[[int, int], None]
"""Called as progress(files_done, files_total) while logs are parsed."""

CATEGORY_WEIGHT: dict[Category, float] = {"design": 1.0, "testbench": 0.8, "unknown": 0.6, "infrastructure": 0.5}

# Failing tests with no error message still need a group, so they aren't silently lost.
NO_ERROR_TRUNCATED = "NOERR-truncated"
NO_ERROR_VERDICT = "NOERR-verdict"
_NO_ERROR_TITLES = {
    NO_ERROR_TRUNCATED: "Log ends before the simulator finished, with no error message",
    NO_ERROR_VERDICT: "Test reported FAILED without printing an error",
}

_RTL_DIR = re.compile(r"(?:^|/)rtl/(\w+)/")
_DUT_SCOPE = re.compile(r"\.dut\.u_(\w+)")
_TB_ENV = re.compile(r"\.(\w+?)_env\.")  # uvm_test_top.env.<block>_env.scbd


@dataclass
class _Group:
    """Failing tests that share one root-cause signature (becomes an `Issue`)."""

    key: str
    signature: Signature | None
    root_events: list[LogEvent] = field(default_factory=list)
    tests: list[TestResult] = field(default_factory=list)
    follow_ons: Counter[str] = field(default_factory=Counter)


class Analyzer:
    def __init__(self, parser: LogParser | None = None) -> None:
        self.parser = parser or LogParser()

    def analyze_dir(
        self, folder: Path, name: str | None = None, progress: ProgressCallback | None = None
    ) -> AnalysisReport:
        paths = discover_logs(folder)
        root = folder if folder.is_dir() else folder.parent
        return self.analyze(paths, name=name or folder.name, root=root, progress=progress)

    def analyze(
        self, paths: Iterable[Path], name: str, root: Path | None = None, progress: ProgressCallback | None = None
    ) -> AnalysisReport:
        started = time.perf_counter()
        paths = list(paths)
        index = SignatureIndex()
        groups: dict[str, _Group] = {}
        occurrences: Counter[str] = Counter()
        tests: list[TestResult] = []
        formats: Counter[str] = Counter()
        vocabulary: set[str] = set()
        total_lines = 0

        for done, parsed in enumerate(self.parser.parse_many(paths), start=1):
            if progress is not None:
                progress(done, len(paths))
            formats[parsed.format] += 1
            total_lines += parsed.total_lines
            vocabulary.update(_block_names(parsed))
            errors = parsed.errors
            signatures = [index.assign(e) for e in errors]
            occurrences.update(s.signature_id for s in signatures)
            test = _test_result(parsed, root)
            tests.append(test)
            if parsed.status != "fail":
                continue

            if errors:
                key, sig, root_event = signatures[0].signature_id, signatures[0], errors[0]
                test.root_cause = _root_cause(root_event)
            else:
                key = NO_ERROR_TRUNCATED if parsed.completed is False else NO_ERROR_VERDICT
                sig, root_event = None, None
            test.issue_id = key
            test.follow_on_errors = max(0, len(errors) - 1)
            group = groups.setdefault(key, _Group(key, sig))
            group.tests.append(test)
            if root_event is not None:
                group.root_events.append(root_event)
            group.follow_ons.update({s.signature_id for s in signatures[1:]} - {key})

        _assign_modules(tests, vocabulary)
        issues = _rank(groups, occurrences, index, failed=sum(1 for t in tests if t.status == "fail"))
        summary = _summary(name, tests, formats, total_lines, issues, time.perf_counter() - started)
        return AnalysisReport(summary, issues, tests)


def _test_result(parsed: ParsedLog, root: Path | None) -> TestResult:
    try:
        rel = parsed.path.relative_to(root).as_posix() if root else parsed.path.as_posix()
    except ValueError:
        rel = parsed.path.as_posix()
    return TestResult(
        test_name=parsed.test_name or parsed.path.stem,
        seed=parsed.seed,
        path=rel,
        format=parsed.format,
        status=parsed.status,
        status_reason=parsed.status_reason,
        module="",
        total_lines=parsed.total_lines,
        error_count=parsed.severity_counts["ERROR"] + parsed.severity_counts["FATAL"],
        follow_on_errors=0,
    )


def _root_cause(e: LogEvent) -> RootCause:
    location = f"{e.file}:{e.file_line}" if e.file and e.file_line is not None else None
    return RootCause(e.line, e.end_line, e.message, e.kind, e.severity.name, e.msg_id, e.source, location, e.time_ps)


def _block_names(parsed: ParsedLog) -> set[str]:
    """Design block names mentioned in a log: rtl/<block>/ paths, tb_top.dut.u_<block> scopes, and
    per-block testbench environments (uvm_test_top.env.<block>_env...)."""
    names = set()
    for e in parsed.events:
        for regex, text in ((_RTL_DIR, e.file or ""), (_DUT_SCOPE, e.source or ""), (_TB_ENV, e.source or "")):
            if m := regex.search(text):
                names.add(m.group(1))
    return names


def _assign_modules(tests: list[TestResult], vocabulary: set[str]) -> None:
    """Best-effort "which design block is this test about?", used for the module heatmap.

    Each test gets the longest known block name its test name starts with, or else the first word
    of its name.
    """
    by_length = sorted(vocabulary, key=len, reverse=True)
    for t in tests:
        t.module = next(
            (v for v in by_length if t.test_name == v or t.test_name.startswith(v + "_")),
            t.test_name.split("_", 1)[0],
        )


def _rank(groups: dict[str, _Group], occurrences: Counter[str], index: SignatureIndex, failed: int) -> list[Issue]:
    drafts: list[tuple[float, int, Issue]] = []
    for order, group in enumerate(groups.values()):
        first = group.root_events[0] if group.root_events else None
        category, category_reason = classify(first)
        weight = CATEGORY_WEIGHT[category]
        n = len(group.tests)
        severity = max((e.severity for e in group.root_events), default=Severity.ERROR)
        modules = Counter(t.module for t in group.tests)
        sig = group.signature
        follow_on_in = sum(1 for g in groups.values() if g is not group and group.key in g.follow_ons)

        reasons = [f"Breaks {n} of {failed} failing tests ({100 * n / max(failed, 1):.0f}%).", category_reason]
        if len(modules) > 1:
            reasons.append(f"Seen in {len(modules)} modules: {', '.join(m for m, _ in modules.most_common(5))}.")
        if severity == Severity.FATAL:
            reasons.append("Fatal: it stops the simulation, so the rest of each affected test never runs.")
        if follow_on_in:
            reasons.append(f"Also appears as a follow-on error in {follow_on_in} other issue(s).")

        drafts.append(
            (
                n * weight,
                order,
                Issue(
                    rank=0,
                    issue_id=group.key,
                    title=sig.title if sig else _NO_ERROR_TITLES[group.key],
                    kind=sig.kind if sig else "crash",
                    msg_id=sig.msg_id if sig else None,
                    location=sig.location if sig else None,
                    category=category,
                    category_reason=category_reason,
                    severity=severity.name,
                    score=0.0,
                    score_parts={"tests": n, "category_weight": weight},
                    reasons=reasons,
                    test_count=n,
                    occurrences=occurrences.get(group.key, n),
                    modules=dict(modules.most_common()),
                    example_message=first.message if first else _NO_ERROR_TITLES[group.key],
                    tests=[
                        IssueTest(t.test_name, t.seed, t.path, t.root_cause.line if t.root_cause else None)
                        for t in group.tests
                    ],
                    followed_by=[index.signatures[s].title for s, _ in group.follow_ons.most_common(3)],
                ),
            )
        )

    drafts.sort(key=lambda d: (-d[0], d[1]))  # highest priority first; ties keep first-seen order
    top = drafts[0][0] if drafts else 1.0
    issues = []
    for rank, (priority, _, issue) in enumerate(drafts, start=1):
        issue.rank = rank
        issue.score = round(100 * priority / top, 1)
        issues.append(issue)
    return issues


def _summary(
    name: str, tests: list[TestResult], formats: Counter[str], total_lines: int, issues: list[Issue], seconds: float
) -> RunSummary:
    statuses = Counter(t.status for t in tests)
    by_category: Counter[str] = Counter()
    for i in issues:
        by_category[i.category] += 1
    return RunSummary(
        name=name,
        total_tests=len(tests),
        passed=statuses["pass"],
        failed=statuses["fail"],
        unknown=statuses["unknown"],
        formats=dict(formats.most_common()),
        total_lines=total_lines,
        error_lines=sum(t.error_count for t in tests if t.status == "fail"),
        issue_count=len(issues),
        by_category=dict(by_category.most_common()),
        seconds=round(seconds, 2),
    )
