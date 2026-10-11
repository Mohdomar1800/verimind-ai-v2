from __future__ import annotations

from pathlib import Path

import pytest

from verimind.engine import FormatError, FormatRegistry, LogParser

CUSTOM_FORMAT = r"""
name: acme
description: ACME internal runner
extends: [common]
detect:
  - '^ACME-SIM v\d'
test_name:
  - '^TEST (?P<test>\w+)'
end_markers:
  - '^ACME DONE'
rules:
  - name: msg
    pattern: '^\[(?P<severity>E|W|I)\] (?P<source>\w+): (?P<message>.*)$'
"""


def test_builtin_formats_load() -> None:
    registry = FormatRegistry.load()
    assert {"vcs", "xcelium", "questa", "inhouse", "generic"} <= set(registry.names())
    assert "common" not in registry.names()  # abstract formats are hidden


def test_custom_format_from_folder(tmp_path: Path) -> None:
    # Single-letter severities aren't known words, so this rule maps them with a fixed severity per rule.
    (tmp_path / "acme.yaml").write_text(
        CUSTOM_FORMAT.replace(
            r"pattern: '^\[(?P<severity>E|W|I)\] (?P<source>\w+): (?P<message>.*)$'",
            "pattern: '^\\[E\\] (?P<source>\\w+): (?P<message>.*)$'\n    severity: error",
        )
    )
    registry = FormatRegistry.load([tmp_path])
    parser = LogParser(registry)
    lines = ["ACME-SIM v2", "TEST alu_smoke", "[E] scbd: result mismatch", "ACME DONE"]
    result = parser.parse_lines(iter(lines), path=Path("x.log"))

    assert result.format == "acme"
    assert result.test_name == "alu_smoke"
    assert result.completed is True
    (event,) = result.errors
    assert (event.line, event.source, event.message) == (3, "scbd", "result mismatch")
    # Inherited rules from `common` still apply.
    farm = parser.parse_lines(iter(["ACME-SIM v2", "*** Job terminated by grid: out of memory ***"]), Path("y"))
    assert farm.errors[0].kind == "crash"


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            "name: bad\nrules:\n  - name: r\n    pattern: '([unclosed'\n    severity: error\n",
            "invalid regular expression",
        ),
        ("name: bad\nrules:\n  - name: r\n    pattern: 'x'\n", "needs a `severity:`"),
        ("name: bad\nrules:\n  - name: r\n    pattern: 'x'\n    severity: loud\n", "unknown severity"),
        ("name: bad\nunexpected_key: 1\n", "unexpected_key"),
        ("name: bad\nextends: [nope]\n", "extends unknown format 'nope'"),
        ("name: [unbalanced\n", "not valid YAML"),
    ],
)
def test_invalid_formats_give_clear_errors(tmp_path: Path, yaml_text: str, message: str) -> None:
    (tmp_path / "bad.yaml").write_text(yaml_text)
    with pytest.raises(FormatError, match="bad.yaml|nope") as exc_info:
        FormatRegistry.load([tmp_path])
    assert message in str(exc_info.value)


def test_inheritance_loop_is_detected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: a\nextends: [b]\n")
    (tmp_path / "b.yaml").write_text("name: b\nextends: [a]\n")
    with pytest.raises(FormatError, match="loop"):
        FormatRegistry.load([tmp_path])


def test_user_format_overrides_builtin(tmp_path: Path) -> None:
    (tmp_path / "vcs.yaml").write_text("name: vcs\ndescription: my patched VCS\nextends: [common]\n")
    assert FormatRegistry.load([tmp_path]).describe("vcs") == "my patched VCS"
