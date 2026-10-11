"""Log-format definitions: YAML files describing how to recognize and read one log style.

Built-in formats live in `verimind/engine/formats/*.yaml`. Teams add their own by putting YAML
files in a folder and setting VERIMIND_FORMATS_DIR; a user file with the same name as a built-in
one replaces it. See docs/log-formats.md for the file format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from verimind.engine.models import EventKind, Severity

BUILTIN_DIR = Path(__file__).parent / "formats"
FALLBACK_FORMAT = "generic"
TIME_UNITS_PS = {"fs": 0.001, "ps": 1, "ns": 1_000, "us": 1_000_000, "ms": 1_000_000_000, "s": 1_000_000_000_000}


class FormatError(ValueError):
    """A format definition is invalid. The message says which file and what is wrong."""


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc


# --------------------------------------------------------------------------------------------------
# YAML schema
# --------------------------------------------------------------------------------------------------


class StepSpec(BaseModel):
    """A follow-up line that belongs to the same report (multi-line messages)."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    optional: bool = False  # the line may be absent
    repeat: bool = False  # absorb as many matching lines as there are
    kind: EventKind | None = None  # matching this line changes the event's kind

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, pattern: str) -> str:
        _compile(pattern)
        return pattern


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    pattern: str
    kind: EventKind = "message"
    severity: str | None = None  # fixed severity when the pattern has no (?P<severity>...) group
    contains: str | None = None  # fast pre-check: skip the regex if this text isn't in the line
    time_unit: str = "ns"  # unit of (?P<time>...) when the pattern has no (?P<unit>...) group
    then: list[StepSpec] = Field(default_factory=list)

    @field_validator("then", mode="before")
    @classmethod
    def _steps_from_strings(cls, value: object) -> object:
        if isinstance(value, list):
            return [{"pattern": v} if isinstance(v, str) else v for v in value]
        return value

    @model_validator(mode="after")
    def _validate(self) -> RuleSpec:
        groups = _compile(self.pattern).groupindex
        if self.severity is None and "severity" not in groups:
            raise ValueError(f"rule '{self.name}' needs a `severity:` or a (?P<severity>...) group")
        if self.severity is not None and Severity.parse(self.severity) is None:
            raise ValueError(f"rule '{self.name}' has unknown severity '{self.severity}'")
        if self.time_unit.lower() not in TIME_UNITS_PS:
            raise ValueError(f"rule '{self.name}' has unknown time_unit '{self.time_unit}'")
        return self


class VerdictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    passed: list[str] = Field(default_factory=list, alias="pass")
    failed: list[str] = Field(default_factory=list, alias="fail")


class FormatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    abstract: bool = False  # only used through `extends`, never detected on its own
    extends: list[str] = Field(default_factory=list)
    detect: list[str] = Field(default_factory=list)  # patterns that identify this format near the top
    test_name: list[str] = Field(default_factory=list)  # patterns with (?P<test>...) in the header
    seed: list[str] = Field(default_factory=list)  # patterns with (?P<seed>...) in the header
    end_markers: list[str] = Field(default_factory=list)  # present only if the simulator finished normally
    verdict: VerdictSpec = Field(default_factory=VerdictSpec)
    rules: list[RuleSpec] = Field(default_factory=list)

    @field_validator("detect", "test_name", "seed", "end_markers")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            _compile(p)
        return patterns


# --------------------------------------------------------------------------------------------------
# Compiled, ready-to-run formats
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledStep:
    regex: re.Pattern[str]
    optional: bool
    repeat: bool
    kind: EventKind | None


@dataclass(frozen=True)
class CompiledRule:
    name: str
    regex: re.Pattern[str]
    kind: EventKind
    severity: Severity | None
    contains: str | None
    time_unit_ps: float
    steps: tuple[CompiledStep, ...]


def _any_of(patterns: list[str]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


@dataclass(frozen=True)
class CompiledFormat:
    name: str
    description: str
    detect: tuple[re.Pattern[str], ...]
    test_name: tuple[re.Pattern[str], ...]
    seed: tuple[re.Pattern[str], ...]
    end_marker: re.Pattern[str] | None
    verdict_pass: re.Pattern[str] | None
    verdict_fail: re.Pattern[str] | None
    rules: tuple[CompiledRule, ...]

    def detection_score(self, head: list[str]) -> int:
        """How many of this format's fingerprints appear in the first lines of a file."""
        return sum(1 for regex in self.detect if any(regex.search(line) for line in head))


def _compile_rule(spec: RuleSpec) -> CompiledRule:
    return CompiledRule(
        name=spec.name,
        regex=re.compile(spec.pattern),
        kind=spec.kind,
        severity=Severity.parse(spec.severity) if spec.severity else None,
        contains=spec.contains,
        time_unit_ps=TIME_UNITS_PS[spec.time_unit.lower()],
        steps=tuple(CompiledStep(re.compile(s.pattern), s.optional, s.repeat, s.kind) for s in spec.then),
    )


class FormatRegistry:
    """All known formats, with inheritance (`extends`) resolved and regexes compiled once."""

    def __init__(self, specs: dict[str, FormatSpec]) -> None:
        self._specs = specs
        self._compiled: dict[str, CompiledFormat] = {}
        if FALLBACK_FORMAT not in specs:
            raise FormatError(f"the '{FALLBACK_FORMAT}' fallback format is missing")
        for name in specs:
            self.get(name)  # fail fast on broken inheritance

    @classmethod
    def load(cls, extra_dirs: list[Path] | None = None) -> FormatRegistry:
        specs: dict[str, FormatSpec] = {}
        for directory in [BUILTIN_DIR, *(extra_dirs or [])]:
            if not directory.is_dir():
                raise FormatError(f"formats folder not found: {directory}")
            for path in sorted(directory.glob("*.yaml")):
                spec = load_format_file(path)
                specs[spec.name] = spec
        return cls(specs)

    def names(self, include_abstract: bool = False) -> list[str]:
        return [n for n, s in self._specs.items() if include_abstract or not s.abstract]

    def describe(self, name: str) -> str:
        return self._specs[name].description

    def get(self, name: str) -> CompiledFormat:
        if name not in self._compiled:
            if name not in self._specs:
                raise FormatError(f"unknown log format '{name}'. Known formats: {', '.join(self.names())}")
            self._compiled[name] = self._build(name, chain=())
        return self._compiled[name]

    def _build(self, name: str, chain: tuple[str, ...]) -> CompiledFormat:
        if name in chain:
            raise FormatError(f"format inheritance loop: {' -> '.join([*chain, name])}")
        if name not in self._specs:
            raise FormatError(f"format '{chain[-1]}' extends unknown format '{name}'")
        spec = self._specs[name]
        parents = [self._build(p, (*chain, name)) for p in spec.extends]

        def merged(own: list[str], attr: str) -> tuple[re.Pattern[str], ...]:
            inherited = [r for p in parents for r in getattr(p, attr)]
            return (*[re.compile(x) for x in own], *inherited)

        def merged_any(own: list[str], attr: str) -> re.Pattern[str] | None:
            patterns = own + [r.pattern for p in parents if (r := getattr(p, attr)) is not None]
            return _any_of(patterns)

        return CompiledFormat(
            name=spec.name,
            description=spec.description,
            detect=tuple(re.compile(p) for p in spec.detect),  # fingerprints are never inherited
            test_name=merged(spec.test_name, "test_name"),
            seed=merged(spec.seed, "seed"),
            end_marker=merged_any(spec.end_markers, "end_marker"),
            verdict_pass=merged_any(spec.verdict.passed, "verdict_pass"),
            verdict_fail=merged_any(spec.verdict.failed, "verdict_fail"),
            # A format's own rules take priority over inherited ones.
            rules=(*[_compile_rule(r) for r in spec.rules], *[r for p in parents for r in p.rules]),
        )

    def detect(self, head: list[str]) -> CompiledFormat:
        """Pick the format whose fingerprints best match the top of a file; fall back to 'generic'."""
        best, best_score = self.get(FALLBACK_FORMAT), 0
        for name in self.names():
            fmt = self.get(name)
            score = fmt.detection_score(head)
            if score > best_score:
                best, best_score = fmt, score
        return best


def load_format_file(path: Path) -> FormatSpec:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return FormatSpec.model_validate(data)
    except yaml.YAMLError as exc:
        raise FormatError(f"{path.name}: not valid YAML: {exc}") from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in exc.errors())
        raise FormatError(f"{path.name}: {problems}") from exc
