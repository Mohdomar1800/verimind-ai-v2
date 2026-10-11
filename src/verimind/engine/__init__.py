"""The analysis engine: pure Python, no web or database code.

The worker, the CLI, and the tests all use it the same way:

    report = Analyzer(LogParser(FormatRegistry.load())).analyze_dir(Path("regression_01"))
    for issue in report.issues:
        ...
"""

from verimind.engine.analyzer import Analyzer
from verimind.engine.models import EventKind, LogEvent, ParsedLog, Severity, Status
from verimind.engine.parser import LogParser, discover_logs, open_log
from verimind.engine.registry import FormatError, FormatRegistry
from verimind.engine.report import AnalysisReport, Issue, TestResult

__all__ = [
    "AnalysisReport",
    "Analyzer",
    "Issue",
    "TestResult",
    "EventKind",
    "FormatError",
    "FormatRegistry",
    "LogEvent",
    "LogParser",
    "ParsedLog",
    "Severity",
    "Status",
    "discover_logs",
    "open_log",
]
