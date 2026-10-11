# 0007: Database schema, file storage, and the Postgres job queue

**Status:** accepted

## Context
Phase 3 produced an analysis engine that runs from the command line. To serve a web UI, results must
be stored, uploads must be kept safely, and analysis must run in the background with visible
progress (ADR 0001).

## Decisions

### Schema (SQLAlchemy 2.0, migrations with Alembic)
```
runs ──< jobs                       one run, analyzed by one or more job attempts
runs ──< issues ──< test_results    ranked issues and the tests they break
runs ──< test_results               every test in the run, passing or failing
```
- Run summary numbers (passed, failed, issue count) are stored on the run row, so the runs list is
  one cheap query.
- Variable-shape data (reasons, module counts, formats) uses **JSONB** columns. It's always read
  together with its row, so separate tables would add joins for nothing.
- `issues.signature_id` is indexed: the same problem has the same ID in every run, which powers
  "new / recurring / resolved" (Phase 8).
- `ON DELETE CASCADE` everywhere: deleting a run removes its jobs, issues and test results in one
  statement.
- Re-analysis replaces a run's results in one transaction, so it's safe to repeat.
- Migrations ship inside the package. A one-shot `migrate` service in Docker Compose runs
  `verimind db upgrade` before the app and worker start, so users never run migrations by hand.

### File storage
Uploads live at `<storage>/runs/<run_id>/logs/` on a Docker volume shared by the app and the
worker. Uploads are untrusted:
- Names are normalized; absolute paths, `..` and drive letters are rejected. Every target path is
  resolved and must stay inside the run's folder.
- Zips are checked *before* extraction for file count, declared size, symlinks and zip-slip
  paths. The real size is enforced again while copying, because declared sizes can lie (zip bombs).
- A total size limit applies across all files of an upload (`VERIMIND_MAX_UPLOAD_MB`).
- If saving fails halfway, the partial files are deleted and the run row is rolled back.

### Job queue in Postgres
```sql
SELECT ... FROM jobs WHERE status = 'queued' ORDER BY created_at
LIMIT 1 FOR UPDATE SKIP LOCKED
```
- **SKIP LOCKED:** a worker locks one row; other workers skip it instead of waiting. No job is
  processed twice and no coordinator is needed. Verified with 3 workers: 12 jobs split 2 / 3 / 7,
  one attempt each, no duplicate results.
- **Short transactions:** claiming commits immediately, the analysis runs with no open
  transaction, and progress updates commit separately (throttled to once a second), so the UI
  sees live progress and a crash never leaves locks behind.
- **Retries:** unexpected errors are retried up to 3 attempts. Errors the user must fix (missing
  files, bad format file) fail at once with a plain-language message.
- **Crash recovery:** progress updates double as heartbeats. A running job with no heartbeat for
  10 minutes is requeued by any live worker.

## Alternatives considered
- **Celery + Redis / RQ:** proven, but two more services to run and monitor, and job state would
  live apart from the data it produces. At this scale a jobs table is simpler and transactional.
- **Storing logs in the database (bytea / large objects):** bloats backups and buys nothing. The
  engine reads files as streams anyway.
- **Object storage (S3/MinIO):** the right next step for multi-machine deployments. The `Storage`
  class is the single place to swap.

## Testing
Database tests run against a real PostgreSQL (SQLite has no `SKIP LOCKED`). They cover claim order,
a lock held by one session being skipped by another, retries, stale-job recovery, a full worker run
compared with the CLI analysis, re-analysis, cascading deletes, cleanup after a failed upload, and a
migration downgrade/upgrade round trip. CI starts a Postgres service for them.
