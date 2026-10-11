# 0005: Log formats are data (YAML rules), not code

**Status:** accepted

## Context
Every simulator and every company prints logs differently. The same UVM error looks slightly
different in VCS, Xcelium and Questa, and assertion failures differ completely. Some are 4 lines
long in VCS and 2 in Questa. VeriMind is meant to work on *any* team's verification logs, so
supporting a new style can't require changing Python code.

## Decision
One generic streaming parser executes **format definitions written in YAML**:

- **detect:** fingerprints near the top of a file. Auto-detection picks the format with the most
  matches and falls back to a permissive `generic` format.
- **rules:** regular expressions with named groups (`severity`, `message`, `source`, `msg_id`,
  `file`, `time`, ...). `then:` steps fold multi-line reports into one event.
- **end_markers / verdict:** how to tell a finished simulation from a crashed one, and explicit
  pass/fail banners.
- **extends:** formats inherit shared rules (`common.yaml`: UVM banners, farm kill messages).

Every format becomes the same normalized model (`ParsedLog` → `LogEvent`), so all later analysis
is format-independent. The built-in formats are just the first YAML files; user files load from
`VERIMIND_FORMATS_DIR` and are validated by pydantic, with errors that name the file and field.

## Design details worth knowing
- **Streaming:** one line in memory at a time, plus at most one open multi-line report. Gzipped
  logs are read transparently.
- **Speed:** INFO lines (~90% of a log) are counted but never turned into objects. A `contains:`
  pre-check skips regexes that can't match. Measured: 2.46 M lines (340 MB, 2,000 logs) in about
  14 s on a laptop (about 175 k lines/s), up from 41 s before the INFO fast path.
- **Status is explained:** each log gets pass / fail / unknown plus a plain-language reason. An
  error message, a FAILED banner, or a missing end marker (the log was cut off) each mean fail.
- **Accuracy is tested against the answer key:** for every generated format, the parser must
  reproduce the exact error lines, first-failure line, status, test name and seed of every test.

## Alternatives considered
- **A Python class per format:** flexible, but every new format needs a developer and a release.
- **Machine-learned log parsing** (e.g. Drain-style template mining) to discover formats
  automatically: useful for grouping *messages* (Phase 3 uses that idea for signatures), but
  deciding what counts as an error and where a report ends needs explicit, auditable rules.
- **Keyword search for "error"** (the hackathon approach): fails on lines like `error_count=0`.
  The generator includes such lines on purpose, and the tests prove they're ignored.

## Limitations
- One log file = one test. Logs that contain several tests back to back would need a "test
  boundary" rule, which can be added to the schema later.
- Regular expressions are a skill. The guide (docs/log-formats.md) and a copyable example format
  keep this manageable.
