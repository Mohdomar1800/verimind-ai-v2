# 0009: A UI a non-specialist can use

**Status:** accepted

## Context
The system is complex inside: parsers, signatures, a job queue. Someone who has never seen it should
still be able to get results by following a one-line instruction ("open the page and click *Try it
with demo data*").

## Decisions
- **The first screen has exactly two choices:** *Upload your logs* or *Try it with demo data*. There
  are no settings, IDs or paths to type.
- **Upload is drag-and-drop** for files, whole folders (walked recursively in the browser) or a zip,
  with a file list, client-side filtering of non-log files, and an upload progress bar. The upload
  returns immediately and the page switches to a live analysis progress card.
- **Plain language everywhere:**
  - Statuses are "Waiting to start / Analyzing / Ready / Failed".
  - Categories are "Design bug / Testbench problem / Tools / compute farm".
  - The headline translates the result: "15 failing tests and 119 error lines come down to 6 problems."
  - Signature placeholders (`<hex>`, `<n>`) render as small "value"/"number" pills with a tooltip.
- **Every ranking is explained** ("Why is this ranked #1?") with the same reasons the engine produced.
- **Failures explain themselves:**
  - A failed run says why and offers *Try again*.
  - A missing page says so in words.
  - If the database is down, pages say so within 5 seconds (connection timeout) instead of hanging.
- **Progressive enhancement:** every action is a normal link or form, so the app works without
  JavaScript. HTMX adds self-refreshing tables and progress cards: the server sends `HX-Refresh` when
  analysis finishes, so the browser reloads the page with results. A small script adds drag-and-drop
  and the upload bar.
- **No build step, no CDN:** HTMX is vendored into `static/vendor/` (works offline, on air-gapped
  verification networks).
- **Light and dark themes** come from design tokens and follow the operating system. The layout works
  down to phone width (verified with headless Edge screenshots at 1280 px and 500 px).

## Found by looking, not by tests
Screenshots during development caught two problems the tests didn't:
- Raw `<hex>`/`<*>` placeholders were confusing in titles. They're now rendered as labeled pills.
- Block names were truncated ("axi" instead of "axi_xbar") for blocks without RTL assertions. Block
  names are now also learned from the per-block testbench environments (`uvm_test_top.env.axi_xbar_env`).

## Phase 7 additions
- **Problem page:** the reasons, a triage form (status, owner, ticket, notes) that saves in place, every
  affected test, and a **history** of every run where the same signature appeared ("first seen in…").
  The history is the payoff of stable signatures: one problem, followed across simulators.
- **Log viewer:** opens on the root-cause line, highlighted in red, with follow-on errors in amber.
  It has previous/next error, go to line, earlier/later pages and a raw download. It reads only a
  200-line window, so multi-megabyte logs stay fast.
- **Tests list:** filters by result, block, problem and name. Filters update instantly and are kept
  in the URL, so a filtered view can be bookmarked or shared.
- **Compare:** "new problems / still there / went away", worded for people. It defaults to the two
  newest runs, so it's useful with zero choices.
