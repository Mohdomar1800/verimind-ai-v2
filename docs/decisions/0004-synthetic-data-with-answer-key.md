# 0004: Synthetic regressions with an answer key

**Status:** accepted

## Context
Real regression logs are proprietary, so VeriMind is built and demonstrated on synthetic data. The
hackathon version used random `module=... status=fail` lines, which caused two problems:
- The logs looked nothing like real simulator output, so the parser was never tested against
  realistic formats.
- Nobody knew which failures shared a cause. The evaluation compared the model against labels made
  from the same rules, so the metrics were circular.

## Decision
`verimind.synth` generates realistic regressions **and** an `answer_key.json` that records the truth:

- **Four log formats:** Synopsys VCS, Cadence Xcelium, Siemens Questa, and an in-house runner style.
  Each prints UVM messages and SystemVerilog assertion failures the way that tool does.
- **A bug catalog:** 12 bug types, 9 design and 3 testbench, such as FIFO overflow, AXI handshake
  violations and uncorrectable ECC errors. There are also 3 infrastructure failure types: license
  failure, simulator crash, and out-of-memory kill.
- **Cascades:** a root cause is followed by follow-on errors (more mismatches, timeouts, end-of-test
  checks), so the first error is buried, as in real logs.
- **Traps:** passing tests contain lines like `error_count=0` and "Corrected 3 single-bit errors",
  which defeat naive keyword search.
- **Lifecycles:** across runs, bugs are introduced and fixed, so "new / recurring / resolved"
  comparisons have known answers.
- **Truth by construction:** the simulator records line numbers as it writes them. The answer key
  stores each test's root cause, first-failure line, and every error line.
- **Deterministic:** the same seed gives byte-identical output. Each test has its own RNG
  (`seed:run:test`), so tests are independent and could later be generated in parallel.

## Why it matters
It lets VeriMind report honest metrics in Phase 3, for example:
- *Root-cause accuracy:* did the engine flag the true first-failure line?
- *Clustering quality:* did failures from the same bug end up in the same issue, and different bugs
  in different issues?
- *Ranking:* is the most widespread real bug at the top?

A test suite checks the answer key against the logs line by line, in every format.

## Limitations (worth stating in an interview)
- Each failing test has exactly one root cause. Real tests sometimes hit two bugs.
- The root cause is always the earliest error. In real logs, an unrelated warning-turned-error can
  occasionally come first.
- The message vocabulary is finite. Real projects have far more message types, which is why the
  parser in Phase 2 is driven by configurable rules rather than this catalog.
