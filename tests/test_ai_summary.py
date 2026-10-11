"""Optional Claude summaries, tested with a fake client (no network, no API key, no cost)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from verimind.intelligence.summary import MODEL, LogExcerpt, ProblemFacts, SummaryError, build_prompt, summarize
from verimind.web.templating import simple_markdown

FACTS = ProblemFacts(
    title="[SCBD_MISMATCH] Data mismatch at addr <hex>",
    category="design",
    reasons=["Breaks 6 of 14 failing tests (43%)."],
    example_message="Data mismatch at addr 0x10: expected 0x1, actual 0x3",
    location="tb/dma_scoreboard.sv:214",
    excerpts=[LogExcerpt("dma_smoke_test", 80, [(79, "INFO ok"), (80, "ERROR mismatch"), (81, "INFO next")])],
)


def test_prompt_contains_the_evidence_and_marks_the_root_line() -> None:
    prompt = build_prompt(FACTS)
    assert "SCBD_MISMATCH" in prompt and "tb/dma_scoreboard.sv:214" in prompt and "Breaks 6 of 14" in prompt
    assert "    80> ERROR mismatch" in prompt and "    79  INFO ok" in prompt


def test_summarize_sends_the_expected_request(fake_claude: Callable[..., Any]) -> None:
    client = fake_claude("## What is happening\nThe DMA writes wrong data.")
    text, model = summarize(FACTS, client)
    assert text.startswith("## What is happening") and model == MODEL
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in call and "temperature" not in call  # not accepted on this model


def test_refusal_and_empty_answers_become_friendly_errors(fake_claude: Callable[..., Any]) -> None:
    with pytest.raises(SummaryError, match="declined"):
        summarize(FACTS, fake_claude("", stop_reason="refusal"))
    with pytest.raises(SummaryError, match="empty"):
        summarize(FACTS, fake_claude("   "))


def test_network_errors_become_friendly_errors(fake_claude: Callable[..., Any]) -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    with pytest.raises(SummaryError, match="internet connection"):
        summarize(FACTS, fake_claude(error=error))


def test_markdown_rendering_escapes_model_output() -> None:
    html = str(
        simple_markdown("## Cause\n- **FIFO** overflows\n- see `a_no_overflow`\n1. step one\n<script>x</script>")
    )
    assert "<h4>Cause</h4>" in html and "<li><strong>FIFO</strong> overflows</li>" in html
    assert "<code>a_no_overflow</code>" in html and "<ol>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
