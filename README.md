# VeriMind

**Find the root cause in chip-verification logs, not the 10,000th error line.**

Before a chip is manufactured, its design is tested by running thousands of simulations every
night (a *regression*). Each simulation writes a log file, and when something breaks, hundreds of
tests fail at once and print thousands of error lines. Engineers then spend hours reading logs to
work out what actually went wrong.

VeriMind reads all of those logs for you. It groups the errors into a handful of **distinct
problems**, finds **the line where each failing test first went wrong**, says **whether it looks like
a design bug, a testbench bug or a tools problem**, and ranks **what to fix first**, with the reason
for every rank.

> Example from the demo data: *15 failing tests and 119 error lines come down to 6 problems.*

---

## How it works

```
 log files ──► 1. Read ──► 2. Find root cause ──► 3. Group ──► 4. Classify & rank ──► web page
 (or a .zip)    every line    first real error      same error     design / testbench /
                in any        in each failing       shape = same   tools, most tests
                format        test                  problem        broken = top
```

1. **Read.** Logs from Synopsys VCS, Cadence Xcelium, Siemens Questa and in-house runners are
   recognized automatically. New log styles can be added with a small text file, no coding needed.
2. **Find the root cause.** In a failing test, the first error is usually the cause and the rest are
   knock-on effects. VeriMind marks that first error and the exact line.
3. **Group.** Errors that are the same problem with different numbers (addresses, data values) are
   grouped, so 100 copies of one bug show up as one problem.
4. **Classify and rank.** Each problem is labeled *Design bug*, *Testbench problem* or *Tools /
   compute farm*, and ranked by how many tests it breaks. New problems and regressions rank higher;
   known issues rank lower.

Analysis runs in the background, so uploading returns immediately and you watch a progress bar.
Everything runs on your own computer: nothing is sent anywhere unless you turn on the optional AI
summaries.

## Features

| | |
|---|---|
| **Ranked problems** | Every failure grouped into distinct problems, ranked, with "why it's ranked here". |
| **Log viewer** | Opens each log at the line where the test went wrong, with all other errors highlighted. |
| **Triage notes** | Mark a problem as investigating, known, fixed or ignored, add an owner and a ticket. Notes follow the problem into future runs. |
| **Compare runs** | What's new, what's still there and what went away between two regressions. |
| **Problem library** | Every problem ever seen, and which ones keep coming back without anyone looking at them. |
| **Trends** | Failures over time and a heatmap of which design blocks keep failing. |
| **Failure prediction** | Which tests are likely to fail in the next run, learned from history. |
| **Coverage** | Coverage holes ranked by risk, and the smallest set of tests that keeps 95% of the coverage. |
| **AI explanation** *(optional)* | Claude explains a problem in plain words and suggests what to check next. |
| **Reports** | Download results as Markdown, CSV (Excel) or JSON, or use the REST API. |

---

## Getting started

You only need **Docker**, a free program that runs VeriMind and its database in self-contained
boxes (*containers*), so nothing else has to be installed.

### 1. Install Docker
- **Windows or Mac:** install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
  open it once. Wait until it says it is running.
- **Linux:** install [Docker Engine](https://docs.docker.com/engine/install/) with the Compose plugin.

### 2. Get VeriMind
Download this project (on GitHub: **Code → Download ZIP**, then unzip it) or clone it:
```
git clone https://github.com/Mohdomar1800/verimind-ai-v2.git
cd verimind-ai-v2
```

### 3. Start it
- **Windows:** double-click **`start.bat`** in the project folder.
- **Mac / Linux:** open a terminal in the project folder and run **`./start.sh`**
  (if that says "permission denied", run `bash start.sh` instead).

The first start takes a few minutes while Docker downloads and builds everything; after that it
takes seconds. When it's ready, your browser opens **http://localhost:8000**.

### 4. Try it
Click **Try it with demo data**. Four example nightly regressions are loaded and analyzed in a few
seconds. Open one to see its problems.

### 5. Stop it
Double-click **`stop.bat`** (Windows) or run **`./stop.sh`** (Mac / Linux). Your runs and notes are
kept for next time.

---

## Using VeriMind

**Upload logs.** Go to **Upload logs**, then drag in the log files from one regression, a whole
folder, or a `.zip` (`.log`, `.out`, `.txt`, also gzipped). Optionally include a `coverage.json`
([format](docs/coverage-format.md)). Give the run a name if you like and click **Upload and
analyze**. The page shows progress and switches to the results by itself.

**Read the results.** A run's page starts with one sentence, for example "15 failing tests and 119
error lines come down to 6 problems", followed by the problems, most important first. Each shows its
category (Design bug / Testbench problem / Tools / compute farm) and how many tests it breaks. Open
it to see why it's ranked there.

**Drill into a problem.** Click a problem to see:
- its full explanation;
- every test it breaks;
- its **history** (every earlier run where it appeared);
- a **triage** form to record status, owner, ticket and notes.

Click a test to open the **log viewer** at the exact line where it failed. Use *Next error*, *Go to
line* or *Download log* from there.

**Find tests.** *Browse all tests* lists every test, failing ones first, with filters for result,
design block, problem and name.

**See the bigger picture.**
- **Compare** shows what changed between two runs.
- **Trends** shows failures over time, which blocks keep failing, and which tests are likely to
  fail next.
- **Problem library** lists every problem seen so far.
- **Coverage** (on a run's page, when coverage was uploaded) shows the holes to close first and the
  fastest test set.

**Share results.** On a run's page, **Download report** gives Markdown, CSV (opens in Excel) or
JSON. The complete REST API is documented at **http://localhost:8000/api/docs**.

---

## Settings

Settings live in the `.env` file, which `start` creates from `.env.example`. Restart after changing
it.

| Setting | Default | What it does |
|---|---|---|
| `VERIMIND_PORT` | `8000` | The port in the address `http://localhost:8000`. Change it if 8000 is taken. |
| `VERIMIND_BIND` | `127.0.0.1` | `127.0.0.1` = only this computer. `0.0.0.0` = anyone on your network. |
| `ANTHROPIC_API_KEY` | *(empty)* | Turns on the optional AI explanations. Logs are only sent when you click *Explain with AI*. |
| `VERIMIND_MAX_UPLOAD_MB` | `2048` | Largest upload accepted. |
| `POSTGRES_PASSWORD` | `verimind` | Database password (the database is never exposed outside Docker). |

**Your team's log format.** If your logs aren't recognized, describe their format in a short YAML
file in the `formats/` folder ([guide](docs/log-formats.md)) and restart.

## Troubleshooting

The start script checks the common problems and says what to do. If you need more:
- **"Docker is not running".** Open Docker Desktop, wait until it's ready, and start again.
- **Linux: "not allowed to use Docker".** Run `sudo usermod -aG docker $USER`, log out and back in,
  and start again.
- **Port 8000 already in use.** Set `VERIMIND_PORT=8080` in `.env` and start again.
- **Anything else.** In a terminal opened in the project folder, run the built-in self-check. It says
  what's wrong and how to fix it:
  ```
  docker compose exec app verimind doctor
  ```
- **Start over with empty data:** `docker compose down -v`. This deletes all runs.

---

## For developers

**Stack:**
- **Backend:** Python 3.12, FastAPI and SQLAlchemy 2.0 on PostgreSQL 16, with Alembic migrations.
- **UI:** server-rendered Jinja2 templates with HTMX.
- **ML:** scikit-learn.
- **Packaging:** Docker Compose (app, worker, database).
- **Testing:** pytest and Playwright.

```
src/verimind/
  engine/        log parsing (YAML formats), signatures, classification, ranking; no web or DB code
  intelligence/  failure prediction, coverage analysis, AI summaries
  synth/         synthetic regression generator with an answer key, for demos and evaluation
  db/            tables and migrations      services/  business logic shared by API and pages
  web/           FastAPI app: pages, REST API, security    worker/  background analysis jobs
  storage.py     safe upload storage        jobs.py    Postgres job queue (FOR UPDATE SKIP LOCKED)
tests/           unit, database, API, page and end-to-end browser tests
docs/            guides and design decisions (docs/decisions/0001-0011)
```

**Run without Docker** (needs Python 3.11+ and a PostgreSQL database):
```
pip install -e ".[dev]"
export VERIMIND_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/verimind
verimind db upgrade
verimind serve --reload        # web app at http://localhost:8000
verimind worker                # background analysis, in a second terminal
```

**Command-line tools.** These work without the web app:
- `verimind generate demo`: create synthetic logs with an answer key.
- `verimind analyze <folder>`: ranked problems in the terminal, or `--markdown report.md`.
- `verimind evaluate <dataset>`: accuracy against the answer key.
- `verimind parse <folder>`: just the per-log results.
- `verimind doctor`: check that everything VeriMind needs is working.
- `verimind db revision "message"`: after changing `db/models.py`, generate the migration (review it).

**Tests.**
```
pytest                                                      # unit tests (database tests skip without a DB)
VERIMIND_TEST_DATABASE_URL=postgresql+psycopg://...  pytest # + database, API and page tests
VERIMIND_E2E_URL=http://127.0.0.1:8000  pytest tests/e2e    # browser tests against a running instance
```
CI runs lint, type checks, a dependency vulnerability audit, all tests, and the browser tests
against the full Docker stack.

**Further reading:**
- [Design decisions](docs/decisions/): why each part is built the way it is.
- [Log formats](docs/log-formats.md)
- [Coverage format](docs/coverage-format.md)

**Measured accuracy.** On synthetic data with a known answer key, root-cause lines are 100%
correct and problem grouping has an F1 score of 1.0. Synthetic data is cleaner than real logs, so
treat this as proof that the pipeline is correct, not a real-world accuracy figure
([details](docs/decisions/0006-issue-grouping-and-ranking.md)).
