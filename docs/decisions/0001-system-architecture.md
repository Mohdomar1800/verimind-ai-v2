# 0001: System architecture

**Status:** accepted

## Context
Verification regressions produce thousands of simulation logs. Parsing and analyzing them can take
minutes, but users still need a responsive UI. The system has to run on any machine (Windows, macOS,
Linux) with one command.

## Decision
Three processes, started together by Docker Compose:

| Process | Role |
|---|---|
| `app` | FastAPI. Serves the HTML UI and the JSON API (`/api/...`). Accepts uploads and records a job. |
| `worker` | Picks up queued jobs and runs the analysis engine. Writes results to the database. |
| `db` | PostgreSQL. The single source of truth for runs, jobs, parsed failures, and triage state. |

The analysis engine is a plain Python package (`verimind.engine`) with no web or database imports.
The worker, the CLI, and the tests all call it the same way.

```
browser ──HTTP──► app ──INSERT job──► db ◄──poll / write results── worker ──► engine
                   │                                                  │
                   └──────────── uploaded files (shared volume) ──────┘
```

## Alternatives considered
- **Run the analysis inside the HTTP request** (what the hackathon version did): simple, but the
  request blocks for minutes, times out, and gives the user no progress.
- **Celery + Redis**: the standard task queue, but it adds two more moving parts. A jobs table in
  Postgres (claimed with `SELECT ... FOR UPDATE SKIP LOCKED`) is enough at this scale and keeps
  the job state next to the data it produces.

## Consequences
- Upload returns immediately. The UI polls the job's progress.
- More workers can be added (`docker compose up --scale worker=3`) with no code changes.
- Keeping the engine separate means it can be tested quickly without a database.
