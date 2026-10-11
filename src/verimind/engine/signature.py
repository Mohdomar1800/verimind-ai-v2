"""Failure signatures: the "shape" of an error message, with run-specific data removed.

Two failures share a signature when they are the same kind of problem reported from the same
place, even if addresses, data values, or counts differ:

    [SCBD_MISMATCH] Data mismatch at addr 0x1149054d: expected 0x32ff8b21, actual 0x32ff9b21
    [SCBD_MISMATCH] Data mismatch at addr 0x0728cd9b: expected 0x00a1c3f0, actual 0x00a1c370
      -> Data mismatch at addr <hex>: expected <hex>, actual <hex>

Identity vs. grouping:
- The **identity** (the signature id) uses only facts every log format prints, so the same problem
  gets the same id in every run and every simulator: the UVM message ID plus the reporting
  component, or, for assertions, the assertion's place in the design.
- **Grouping** within a run also uses the source location (file + line) when the format prints
  it. If two call sites share an identity (e.g. identical scoreboards in two blocks), the location
  keeps them apart, and the second one's id gets a location suffix.
- Messages with neither an ID nor a location (tool errors, crashes) are merged when their masked
  text is similar enough. This is the core idea of the Drain log-template algorithm.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from verimind.engine.models import LogEvent

WILDCARD = "<*>"
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:fs|ps|ns|us|ms|s|NS|PS)\b"), "<time>"),
    (re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?"), "<n>"),  # digits inside identifiers (KEY0) are kept
)
SIMILARITY_THRESHOLD = 0.7  # share of tokens that must agree to merge two location-less messages


def mask(text: str) -> str:
    """Replace variable data (hex values, times, numbers) with placeholders."""
    for regex, placeholder in _MASKS:
        text = regex.sub(placeholder, text)
    return text


def _location(event: LogEvent) -> str | None:
    if event.file and event.file_line is not None:
        return f"{event.file}:{event.file_line}"
    return None


@dataclass
class Signature:
    """A group of messages considered "the same problem"."""

    signature_id: str
    kind: str
    msg_id: str | None
    location: str | None
    source: str | None
    template: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.template)

    @property
    def title(self) -> str:
        if self.msg_id:
            return f"[{self.msg_id}] {self.text}"
        if self.kind == "assertion" and self.source:
            return f"Assertion {self.source.rsplit('.', 1)[-1]} failed: {self.text}"
        return self.text


def _merge(template: list[str], tokens: list[str]) -> list[str]:
    return [a if a == b else WILDCARD for a, b in zip(template, tokens, strict=True)]


def _similarity(template: list[str], tokens: list[str]) -> float:
    if len(template) != len(tokens) or not tokens:
        return 0.0
    same = sum(1 for a, b in zip(template, tokens, strict=True) if a in (b, WILDCARD))
    return same / len(tokens)


class SignatureIndex:
    """Assigns a signature to every error event; one index per analysis."""

    def __init__(self, threshold: float = SIMILARITY_THRESHOLD) -> None:
        self.threshold = threshold
        self._by_key: dict[str, list[Signature]] = {}
        self.signatures: dict[str, Signature] = {}

    def assign(self, event: LogEvent) -> Signature:
        tokens = mask(event.message).split()
        location = _location(event)
        identity = _identity(event, location)

        if identity is not None:
            key = f"{identity}|{location or ''}"
            bucket = self._by_key.setdefault(key, [])
            if bucket:  # same identity and call site: same signature; keep a merged template for display
                sig = bucket[0]
                if len(sig.template) == len(tokens):
                    sig.template = _merge(sig.template, tokens)
                return sig
            sig_id = _hash(identity)
            if sig_id in self.signatures:  # a second call site with the same identity in this run
                sig_id = _hash(f"{identity}|{location}")
            return self._new(sig_id, event, location, tokens, bucket)

        key = f"{event.kind}|{event.source or ''}"
        bucket = self._by_key.setdefault(key, [])
        best = max(bucket, key=lambda s: _similarity(s.template, tokens), default=None)
        if best is not None and _similarity(best.template, tokens) >= self.threshold:
            best.template = _merge(best.template, tokens)
            return best
        # The id includes the first message seen, so similar-but-different tool errors stay apart.
        return self._new(_hash(f"{key}|{' '.join(tokens)}"), event, None, tokens, bucket)

    def _new(
        self, sig_id: str, event: LogEvent, location: str | None, tokens: list[str], bucket: list[Signature]
    ) -> Signature:
        sig = Signature(sig_id, event.kind, event.msg_id, location, event.source, tokens)
        bucket.append(sig)
        self.signatures[sig_id] = sig
        return sig


def _identity(event: LogEvent, location: str | None) -> str | None:
    """What makes a problem "the same problem" across runs and formats (None: compare by text)."""
    if event.msg_id:
        return f"{event.kind}|{event.msg_id}|{event.source or ''}"
    if event.kind == "assertion" and event.source:
        return f"assertion|{event.source}"
    if location:
        return f"{event.kind}|{location}"
    return None


def _hash(text: str) -> str:
    return "SIG-" + hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:10]
