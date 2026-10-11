from __future__ import annotations

from datetime import UTC, datetime, timedelta

from verimind.web.templating import duration, filesize, plural, signature, timeago


def test_signature_placeholders_become_pills_and_text_is_escaped() -> None:
    html = str(signature("Data <b> at <hex> after <n> cycles <*>"))
    assert "&lt;b&gt;" in html  # real text is escaped
    assert html.count('class="ph"') == 3 and ">value<" in html and ">number<" in html


def test_human_friendly_formats() -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert timeago(now - timedelta(seconds=10), now) == "just now"
    assert timeago(now - timedelta(minutes=1), now) == "1 minute ago"
    assert timeago(now - timedelta(hours=5), now) == "5 hours ago"
    assert filesize(512) == "512 bytes" and filesize(5 * 1024**2) == "5.0 MB"
    assert duration(0.2) == "under a second" and duration(1) == "1 second" and duration(90) == "1.5 minutes"
    assert plural(1, "problem") == "1 problem" and plural(3, "problem") == "3 problems"
