# 0010: Learning from history: ranking, prediction, coverage, and AI summaries

**Status:** accepted

Phase 8 adds features that use more than one run. Each one is explainable, measured honestly, and
optional where it depends on extra data or services.

## 1. History-aware ranking and the problem library
The engine ranks one run in isolation. The service layer then adjusts the ranking using what the team
already knows. Each adjustment is a factor shown in the problem's reasons:

| Situation | Factor | Reason shown |
|---|---|---|
| New: not seen in any earlier run | x1.5 | "something probably changed recently" |
| Regression: triaged *fixed*, but back | x2.0 | "this was marked fixed (JIRA-9), but it is back" |
| Triaged *known issue* | x0.5 | "ranked lower so new problems surface first" |
| Triaged *ignore* | x0.2 | |

This is only possible because signature IDs are stable across runs and simulators (ADR 0006). The
**problem library** lists every signature ever seen: how many runs, how many tests, first and last
seen, and triage status. It has a "keeps coming back, not triaged" view. Triage changes take effect
on the next analysis; "Analyze again" re-ranks an existing run.

## 2. Trends
Failing tests per run (bars split by problem category, with a dot on runs that introduced new
problems) and a block x run heatmap. Both are inline SVG/CSS. ADR 0003 planned Chart.js, but two
simple charts didn't justify a second vendored library.

## 3. Failure prediction
- **Question:** which tests are likely to fail in the next run?
- **Labels:** the real outcome (did the test fail in the next run?). Nothing is derived from the
  features themselves. The hackathon version's labels were, which made its metrics circular.
- **Features (backward-looking only):** recent failure rate, failed last run, failing streak, ever
  failed, the block's failure rate last run and recently.
- **Model:** logistic regression (scikit-learn), standardized features, balanced classes. Each
  prediction lists its top contributing signals in words.
- **Evaluation:** a rolling time-based backtest (train on runs < t, predict run t), compared with
  "whatever failed last time" and with random guessing.

Results, honestly reported. Across 5 seeds of 12-run synthetic histories, and on the `history` preset
via `verimind evaluate`:
- **AUC 0.73–0.86 for the model vs 0.64–0.78 for "failed last time".** The model orders the whole
  test list consistently better.
- **Top-k hit rate about the same as "failed last time".** On the default `history` preset it's 52% vs
  47% (random: 16%). Across other seeds it ranges from 1 point worse to 5 points better. The simple
  rule is already strong at the very top, and both are far above random.
- The UI states exactly this ("How reliable is this?"), recomputed from the stored history.

Limitations: synthetic bugs fire randomly with fixed probabilities, so there's a ceiling on
predictability. Real projects add signals this doesn't use yet, such as code churn per block and
recent commits.

## 4. Coverage prioritization
A tool-neutral `coverage.json` (docs/coverage-format.md) declares covergroups, coverpoints and bins,
plus each test's hits and CPU time. Real teams produce it with a short export script from URG,
vcover or IMC. The demo data includes one per run.
- **Hole ranking:** missing bins x (1 + 2 x block failure rate) x 1.5 if the coverpoint is partly
  covered (existing stimulus already reaches the area, so a targeted test is cheap).
- **Test grading:** greedy weighted set cover, picking the most new bins per CPU second until nothing
  new is added. This is what commercial "test ranking" does. On the demo, 48 of 120 tests keep 95% of
  the coverage in 35% of the compute time.
- A bad `coverage.json` never fails the log analysis; the run page shows the problem instead.

## 5. Optional AI summaries (Claude)
- **Off by default.** With `ANTHROPIC_API_KEY` set, a problem page shows *Explain with AI*.
  Nothing is sent until the user clicks, and the card says exactly what will be sent: the problem's
  messages and about 30 log lines from up to three affected tests.
- **Request:** Claude Opus 5.5 with effort `low` (a short summary doesn't need deep thinking), a
  fixed three-section format, and the server-side refusal fallback enabled. Refusals, empty answers,
  auth, rate-limit and connection errors become plain-language messages.
- **Storage:** the summary is stored per signature, so it follows the problem into later runs.
- **Safe rendering:** the model's Markdown goes through a tiny renderer that escapes everything first,
  so model output can't inject HTML.
- **Testing:** the tests use a fake client (no network, no key, no cost).

## Alternatives considered
- **Gradient boosting / deep models for prediction:** possibly a little more accurate, but harder to
  explain. With a handful of signals and modest data, logistic regression is the honest default.
- **Re-ranking at display time instead of analysis time:** always current with triage, but the rank
  would change while someone is looking at it. Explicit re-analysis keeps runs stable and auditable.
- **Running AI summaries automatically for every problem:** costs money on every upload and sends
  logs without consent. Click-to-explain keeps both under the user's control.
