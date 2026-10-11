"""Optional AI summaries of a problem, written by Claude.

Off unless ANTHROPIC_API_KEY is set, and only ever run when a user clicks "Explain with AI". What is
sent: the problem's title, category, ranking reasons, an example message, and about 30 log lines
around the root cause of up to three affected tests. Nothing is sent automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic

MODEL = "claude-opus-5-5"
# The server-side fallback re-runs a request on another model if a safety classifier declines it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TIMEOUT_SECONDS = 90.0

SYSTEM_PROMPT = """You are a senior design-verification engineer helping a colleague triage a failing \
RTL regression. You are given one problem that VeriMind found: failing tests that share the same first \
error, plus log excerpts around that error.

Write for an engineer who has not looked at the logs yet. Use exactly these three sections, in Markdown:

## What is happening
Two or three sentences in plain language.

## Most likely cause
Your best hypothesis, and how confident you are. Say whether it looks like a design (RTL) bug, a \
testbench bug, or a tool/infrastructure problem, and point to the specific log evidence.

## What to check next
Three to five concrete debugging steps, most useful first.

Base everything on the evidence given. If the excerpts are not enough to tell, say what extra \
information would settle it. Keep the whole answer under 300 words."""


class SummaryError(Exception):
    """The summary could not be produced. The message is safe to show to the user."""


@dataclass
class LogExcerpt:
    test_name: str
    root_line: int | None
    lines: list[tuple[int, str]]


@dataclass
class ProblemFacts:
    title: str
    category: str
    reasons: list[str]
    example_message: str
    location: str | None
    followed_by: list[str] = field(default_factory=list)
    excerpts: list[LogExcerpt] = field(default_factory=list)


def build_prompt(facts: ProblemFacts) -> str:
    parts = [
        f"Problem: {facts.title}",
        f"VeriMind category: {facts.category}",
        f"Example message: {facts.example_message}",
    ]
    if facts.location:
        parts.append(f"Reported from: {facts.location}")
    parts.append("Why VeriMind ranked it:\n" + "\n".join(f"- {r}" for r in facts.reasons))
    if facts.followed_by:
        parts.append("Usually followed by:\n" + "\n".join(f"- {f}" for f in facts.followed_by))
    for ex in facts.excerpts:
        body = "\n".join(f"{n:>6}{'>' if n == ex.root_line else ' '} {text}" for n, text in ex.lines)
        parts.append(f"Log excerpt from test {ex.test_name} (root cause marked with >):\n{body}")
    return "\n\n".join(parts)


def summarize(facts: ProblemFacts, client: Any) -> tuple[str, str]:
    """Return (summary markdown, model that wrote it). `client` is an anthropic.Anthropic instance."""
    try:
        response = client.with_options(timeout=TIMEOUT_SECONDS).beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},  # a short summary doesn't need deep thinking
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": build_prompt(facts)}],
        )
    except anthropic.AuthenticationError as exc:
        raise SummaryError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise SummaryError("The Anthropic API key is not allowed to use this model.") from exc
    except anthropic.RateLimitError as exc:
        raise SummaryError("The Anthropic API is rate-limiting requests. Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise SummaryError(f"The Anthropic API returned an error ({exc.status_code}). Try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError("Could not reach the Anthropic API. Check this machine's internet connection.") from exc

    if response.stop_reason == "refusal":
        raise SummaryError("Claude declined to summarize this problem.")
    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise SummaryError("Claude returned an empty answer. Try again.")
    return text, response.model
