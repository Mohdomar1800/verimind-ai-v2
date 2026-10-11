"""Shared Jinja2 environment for server-rendered pages, plus filters that turn raw values into
plain language ("3.2 MB", "5 minutes ago", "Design bug")."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from verimind import __version__

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def render_page(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    """Render a template as a full page or an HTMX fragment."""
    return templates.TemplateResponse(request, name, context, status_code=status_code)


STATUS_LABELS = {
    "queued": ("Waiting to start", "neutral"),
    "processing": ("Analyzing", "info"),
    "done": ("Ready", "ok"),
    "failed": ("Failed", "bad"),
}
HISTORY_LABELS = {
    "new": ("New", "bad", "Not seen in any earlier run."),
    "regression": ("Regression", "bad", "Marked fixed during triage, but it came back."),
    "recurring": ("Recurring", "neutral", "Also seen in earlier runs."),
    "known": ("Known issue", "neutral", "Triaged as a known issue."),
    "ignored": ("Ignored", "neutral", "Triaged as not worth acting on."),
}
CATEGORY_LABELS = {
    "design": ("Design bug", "Something in the chip design (RTL) misbehaved."),
    "testbench": ("Testbench problem", "The verification code itself has a problem."),
    "infrastructure": ("Tools / compute farm", "Licenses, crashes, or memory. Not a chip bug."),
    "unknown": ("Unclear", "Not enough information in the log to tell."),
}


def number(value: int | float | None) -> str:
    return "0" if value is None else f"{value:,}"


def filesize(value: int | None) -> str:
    size = float(value or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def timeago(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return ""
    seconds = int(((now or datetime.now(UTC)) - value).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def duration(seconds: float | None) -> str:
    s = float(seconds or 0)
    if s < 1:
        return "under a second"
    if s < 60:
        return f"{s:.0f} second{'s' if round(s) != 1 else ''}"
    return f"{s / 60:.1f} minutes"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count:,} {singular if count == 1 else (plural_form or singular + 's')}"


_PLACEHOLDER = re.compile(r"<(hex|n|time|\*)>")
_PLACEHOLDER_LABELS = {"hex": "value", "n": "number", "time": "time", "*": "…"}


def signature(text: str) -> Markup:
    """Show signature placeholders (<hex>, <n>, <*>) as small labeled pills instead of raw markup."""
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(text):
        parts.append(str(escape(text[pos : m.start()])))
        label = _PLACEHOLDER_LABELS[m.group(1)]
        parts.append(f'<span class="ph" title="Varies between occurrences">{label}</span>')
        pos = m.end()
    parts.append(str(escape(text[pos:])))
    return Markup("".join(parts))


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LIST_ITEM = re.compile(r"^(?:[-*]|(\d+)[.)])\s+(.*)$")


def _inline(text: str) -> str:
    html = str(escape(text))
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return _CODE.sub(r"<code>\1</code>", html)


def simple_markdown(text: str) -> Markup:
    """Render the small Markdown subset Claude's summaries use (headings, lists, bold, code).
    Everything is escaped first, so model output can never inject HTML."""
    out: list[str] = []
    list_tag: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        item = _LIST_ITEM.match(line)
        tag = ("ol" if item.group(1) else "ul") if item else None
        if list_tag and tag != list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None
        if item:
            if not list_tag:
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline(item.group(2))}</li>")
        elif line.startswith("#"):
            out.append(f"<h4>{_inline(line.lstrip('#').strip())}</h4>")
        elif line:
            out.append(f"<p>{_inline(line)}</p>")
    if list_tag:
        out.append(f"</{list_tag}>")
    return Markup("\n".join(out))


templates.env.globals.update(
    app_version=__version__, STATUS_LABELS=STATUS_LABELS, CATEGORY_LABELS=CATEGORY_LABELS, HISTORY_LABELS=HISTORY_LABELS
)
templates.env.filters.update(
    simple_markdown=simple_markdown,
    signature=signature,
    number=number,
    filesize=filesize,
    timeago=timeago,
    duration=duration,
    plural=plural,
)
