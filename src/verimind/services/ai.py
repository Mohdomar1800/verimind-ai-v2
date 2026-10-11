"""Gather a problem's evidence from the database and log files, ask Claude, and store the answer."""

from __future__ import annotations

from itertools import islice
from typing import Any

from sqlalchemy.orm import Session

from verimind.config import get_settings
from verimind.db.models import AiSummary, Issue, TestResult, utcnow
from verimind.engine.parser import open_log
from verimind.intelligence.summary import LogExcerpt, ProblemFacts, summarize
from verimind.storage import Storage

MAX_TESTS = 3
LINES_BEFORE, LINES_AFTER = 10, 20
MAX_LINE_CHARS = 300


def make_client() -> Any | None:
    """An Anthropic client when ANTHROPIC_API_KEY is configured, otherwise None (feature off)."""
    settings = get_settings()
    if not settings.ai_summaries_enabled or settings.anthropic_api_key is None:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())


def _excerpt(storage: Storage, test: TestResult) -> LogExcerpt | None:
    if test.root_line is None:
        return None
    try:
        path = storage.log_path(test.run_id, test.path)
    except (FileNotFoundError, ValueError):
        return None
    start = max(1, test.root_line - LINES_BEFORE)
    with open_log(path) as stream:
        lines = [
            (n, line.rstrip("\r\n")[:MAX_LINE_CHARS])
            for n, line in enumerate(islice(stream, start - 1, test.root_line + LINES_AFTER), start=start)
        ]
    return LogExcerpt(test.test_name, test.root_line, lines)


def gather_facts(storage: Storage, issue: Issue) -> ProblemFacts:
    tests = sorted(issue.tests, key=lambda t: t.test_name)[:MAX_TESTS]
    excerpts = [e for t in tests if (e := _excerpt(storage, t)) is not None]
    return ProblemFacts(
        title=issue.title,
        category=issue.category,
        reasons=list(issue.reasons),
        example_message=issue.example_message,
        location=issue.location,
        followed_by=list(issue.followed_by),
        excerpts=excerpts,
    )


def explain_issue(session: Session, storage: Storage, client: Any, issue: Issue) -> AiSummary:
    """Ask Claude about this problem and store the answer (replacing any earlier one).
    Raises SummaryError with a user-facing message on failure."""
    text, model = summarize(gather_facts(storage, issue), client)
    summary = session.get(AiSummary, issue.signature_id) or AiSummary(signature_id=issue.signature_id)
    summary.run_id, summary.model, summary.text, summary.created_at = issue.run_id, model, text, utcnow()
    session.add(summary)
    session.flush()
    return summary
