"""Fixtures shared by all test folders."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from verimind.intelligence.summary import MODEL


class FakeAnthropic:
    """Mimics the parts of anthropic.Anthropic that VeriMind uses (no network, no key, no cost)."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response, self._error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **_: Any) -> FakeAnthropic:
        return self

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def claude_reply(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, model=MODEL)


@pytest.fixture
def fake_claude() -> Callable[..., FakeAnthropic]:
    """fake_claude("answer text") or fake_claude(error=...) -> a fake Anthropic client."""

    def make(text: str = "", stop_reason: str = "end_turn", error: Exception | None = None) -> FakeAnthropic:
        return FakeAnthropic(None if error else claude_reply(text, stop_reason), error)

    return make
