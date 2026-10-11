# 0006: How failures become ranked issues

**Status:** accepted

## Context
A nightly regression with 48 failing tests produced 341 error lines in the demo data. Real
regressions are far bigger. Engineers don't need 341 lines; they need to know how many *distinct*
problems there are, which one to fix first, and who should fix it.

## Decision

### 1. Root cause = the first error in each failing test
Once something goes wrong in a simulation, everything after it is suspect: scoreboards keep
mismatching, drivers time out, end-of-test checks fail. The first error is the best single
predictor of the cause. Later errors are kept as *follow-on* errors and shown as context
("usually followed by RSP_TIMEOUT").

### 2. Group failing tests by the signature of their root cause
A **signature** is a message with its variable data masked (`0x1149054d` → `<hex>`, `5000` → `<n>`).
Two separate questions are involved:

- **Identity** ("is this the same problem as in last night's run?") uses only facts every log format
  prints: the UVM message ID plus the reporting component (`SCBD_MISMATCH` from
  `uvm_test_top.env.dma_env.scbd`), or an assertion's place in the design. The signature id is a hash
  of the identity, so it's the same in every run and every simulator.
- **Grouping** within a run also uses the source location (`tb/dma_scoreboard.sv:214`) when the format
  prints it. If two call sites share an identity (e.g. identical checkers in a flat testbench), the
  location keeps them apart, and the second gets a location-suffixed id.
- Messages with neither an ID nor a location (tool errors, crashes) are merged when at least 70% of
  their masked tokens agree, with differing tokens becoming `<*>`. This is the core idea of
  **Drain** (He et al., 2017), the standard online log-template algorithm.

*How this was found:* the first version keyed signatures on `file:line`. All per-run metrics were
perfect, but comparing a VCS run with an in-house-format run (which prints no file locations) showed
the same bug as both "new" and "resolved". The fix separated identity from grouping, and
`verimind evaluate` now measures **signature stability**: every design/testbench bug must keep one
id across all runs and formats. That check then exposed a second cause. The synthetic testbench used
a flat component hierarchy (`uvm_test_top.env.scbd` for every block), which real SoC testbenches don't,
so the generator now nests per-block environments.

### 3. Classify with transparent rules
In order: crash → *infrastructure*; simulator/tool error → *infrastructure*; RTL assertion →
*design*; testbench words (null handle, virtual interface, config_db, reference model) →
*testbench*; fatal at time 0 → *testbench*; anything else a checker reports → *design*.
Each issue shows its rule's reason in plain language.

### 4. Rank by impact
`priority = tests broken x category weight` (design 1.0, testbench 0.8, unknown 0.6,
infrastructure 0.5), scaled so the top issue scores 100. Every issue lists its reasons:
"Breaks 6 of 14 failing tests (43%)", "Fatal: it stops the simulation", and so on.
Phase 8 adds history (new issues rank higher, known ones lower).

## Results (from `verimind evaluate`)
| Dataset | Failing tests | Error lines | Issues | Root-cause line | Cluster F1 | Category |
|---|---|---|---|---|---|---|
| demo (4 runs, 4 formats) | 48 | 341 | 20 | 100% | 1.000 | 100% |
| history (10 runs) | 109 | 659 | 48 | 100% | 1.000 | 100% |

Signature stability: 6 of 6 bugs keep a single id across all runs, in both datasets, and in 20 further
random seeds. The test suite repeats the checks on 5 seeds and requires F1 ≥ 0.95, 100% root-cause
accuracy, and full stability.
The in-house format prints no file locations, so its runs exercise identity-only grouping. Tool errors
and crashes, which carry neither an ID nor a location, exercise the Drain-style fallback.

## Honest caveats
- **Synthetic data is cleaner than reality.** Every failing test has exactly one bug, and that
  bug's error comes first. Real logs sometimes have two independent failures, or an unrelated error
  that happens first. Perfect scores here show the pipeline is correct, not that real-world
  accuracy will be 100%.
- **Flat testbenches limit cross-format identity.** If two blocks' checkers have the same component
  path and message ID, only the source location can separate them. In formats without locations they
  merge, and the ids of the two call sites depend on which was seen first.
- **Infrastructure failures are simulator-specific.** A license failure in VCS and one in Xcelium are
  different messages, so they get different signatures.
- **Module names are a heuristic.** They come from design paths (`rtl/<block>/`,
  `tb_top.dut.u_<block>`), or else the first word of the test name.
- **Rule-based classification is limited to its rules.** A learned classifier needs labeled triage
  history, which Phase 8 starts collecting through the triage workflow.

## Alternatives considered
- **Embeddings + clustering** (e.g. sentence transformers + DBSCAN, as in the hackathon version's
  dependencies): heavier (torch is gigabytes), slower, needs a threshold tuned per dataset, and is
  harder to explain. Call-site signatures are exact for the common case.
- **Ranking with a trained model:** there are no honest labels yet. The hackathon version trained
  on labels derived from its own features, which made its metrics circular.
