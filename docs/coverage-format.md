# Coverage data (`coverage.json`)

Coverage tells you which scenarios your tests have exercised. VeriMind uses it to:
- **rank coverage holes:** coverpoints with bins no test reached, with holes in currently failing
  blocks listed first, and
- **grade tests:** find the smallest set of tests that keeps almost all of the coverage, which is
  useful for a fast regression.

Coverage is optional. If an upload has no `coverage.json`, everything else still works.

## How to provide it
Put a file named exactly `coverage.json` next to the log files (or inside the zip) when you upload a
run. VeriMind finds it automatically. If the file can't be read, the logs are still analyzed and the
run page explains the problem.

## Format
```json
{
  "format": "verimind-coverage/1",
  "covergroups": [
    {
      "name": "dma_cg",
      "block": "dma",
      "coverpoints": [
        {"name": "burst_len", "bins": ["len_1", "len_4", "len_16", "len_64", "len_256"]},
        {"name": "fifo_level", "bins": ["empty", "half", "full", "overflow_attempt"]}
      ]
    }
  ],
  "tests": [
    {
      "test": "dma_stress_test",
      "seed": 1834512,
      "cpu_seconds": 42.5,
      "hits": ["dma_cg.burst_len.len_64", "dma_cg.fifo_level.full"]
    }
  ]
}
```

- **`covergroups`** declares every bin, including the ones never hit. That's how holes are found.
  `block` is the design block the covergroup belongs to; it should match the block names VeriMind shows.
- **`tests`** lists, per test run, the bins it hit, written as `covergroup.coverpoint.bin`, plus the
  simulation time it took (`cpu_seconds`, used for test grading).

## Exporting from your tools
All three major simulators can report per-test coverage, which a short script turns into this file:

| Simulator | Coverage tool | Per-test data |
|---|---|---|
| Synopsys VCS | URG (`urg -dir *.vdb -format text`, or the UCAPI) | one coverage database per test, or `-grade` output |
| Siemens Questa | `vcover report -details` on each test's UCDB | one UCDB per test, merged with `vcover merge` |
| Cadence Xcelium | IMC (`imc -exec` with a report script) | one coverage database per test |

The demo data includes a generated `coverage.json` for each run, so you can see the result without
exporting anything.

## How the numbers are computed
- **Hole priority** = missing bins × (1 + 2 × the block's failing-test rate) × 1.5 if the coverpoint is
  partly covered. Every hole lists the reasons in words.
- **Test grading** repeatedly picks the test that adds the most not-yet-covered bins per CPU second,
  until no test adds anything new. The page shows how many tests (and how much compute time) keep
  95% of the coverage the full regression achieved.
