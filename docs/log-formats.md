# Adding your team's log format

VeriMind reads Synopsys VCS, Cadence Xcelium, Siemens Questa, and a sample in-house format out of
the box. Logs it doesn't recognize go through a permissive **generic** reader. To get accurate
results for your own log style, describe it in a small text file. No programming is needed beyond
writing a few search patterns.

## 1. Check what VeriMind sees today

```
verimind parse path/to/your/logs
```

This lists the detected format of each file and the first error found in each failing test. If
the format says `generic`, or the errors look wrong, add a format file.

## 2. Write a format file

Create a file such as `acme.yaml` in the project's `formats/` folder.
The easiest start is to copy
[`src/verimind/engine/formats/inhouse.yaml`](../src/verimind/engine/formats/inhouse.yaml).

```yaml
name: acme                          # short unique name
description: ACME simulation farm logs
extends: [common]                   # also understand UVM banners and farm kill messages

detect:                             # text that appears near the top of every ACME log
  - '^ACME-SIM v\d'

test_name:                          # where the test name is printed (capture it as "test")
  - '^TEST (?P<test>\w+)'
seed:
  - 'seed=(?P<seed>\d+)'

end_markers:                        # printed only when the simulation finished normally
  - '^ACME DONE'

verdict:                            # optional explicit pass/fail banners
  pass: ['^RESULT: PASS']
  fail: ['^RESULT: FAIL']

rules:                              # how to recognize each kind of message, tried in order
  - name: message
    pattern: '^\[(?P<severity>ERROR|WARN|INFO)\] (?P<source>\w+): (?P<message>.*)$'
```

Patterns are [Python regular expressions](https://docs.python.org/3/howto/regex.html).
Put each one in single quotes. To include a literal `'` inside a pattern, write it twice (`''`).

### Named parts a rule can capture

| Name | Meaning |
|---|---|
| `severity` | INFO / WARNING / ERROR / FATAL in any common spelling (`UVM_ERROR`, `Error`, `ERR`, `Fatal`, ...) |
| `message` | the human-readable text |
| `source` | who reported it (UVM component, assertion scope, tool) |
| `msg_id` | message ID or error code, e.g. `SCBD_MISMATCH` |
| `file`, `file_line` | source location that issued the message |
| `time`, `unit` | simulation time (`unit` like `ns`/`ps`; otherwise set `time_unit:` on the rule) |
| `detail` | extra lines to attach to the message |
| anything else | kept as extra information on the event |

### Rule options

| Option | Meaning |
|---|---|
| `severity: error` | fixed severity, for patterns without a `severity` part |
| `kind:` | `message` (default), `assertion`, `tool` (simulator/launcher error) or `crash` |
| `contains: 'UVM_'` | speed-up: only try the pattern on lines containing this text |
| `then:` | follow-up lines belonging to the same message (see below) |

### Messages that span several lines

```yaml
  - name: assertion
    pattern: '^ASSERT FAIL (?P<source>\S+) at (?P<time>\d+)ns$'
    kind: assertion
    severity: error
    then:
      - pattern: '^  reason: (?P<message>.*)$'
      - pattern: '^  (?P<detail>.*)$'
        repeat: true                # any number of indented lines
```

Each `then` step must match the next line, unless it is `optional: true` or `repeat: true`.

## 3. Tell VeriMind about the file

**With Docker (the normal setup):** put the file in the project's `formats/` folder and restart
VeriMind (stop, then start). The folder is already connected to the containers.

**Without Docker:** set `VERIMIND_FORMATS_DIR` to the folder that holds your files.

Then check:

```
verimind formats                 # your format should be listed
verimind parse path/to/logs      # files should now show your format name
```

With Docker, run these inside the app container: `docker compose exec app verimind formats`.

If the file has a mistake, VeriMind refuses to start and names the file and the problem, e.g.
`acme.yaml: rules.0: rule 'message' needs a severity: or a (?P<severity>...) group`.

A file named like a built-in format (e.g. `vcs.yaml`) replaces the built-in definition.

## How pass / fail is decided

In this order:
1. Any ERROR or FATAL message → **failed**.
2. A fail banner (e.g. `** TEST FAILED **`) → **failed**.
3. The format has end markers but none appeared → **failed**: the log stopped early, so the
   simulator crashed, was killed, or ran out of time.
4. A pass banner → **passed**.
5. End marker present → **passed**.
6. Otherwise → **unclear** (no errors, but nothing says the test finished).
