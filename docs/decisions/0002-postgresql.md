# 0002: PostgreSQL as the database

**Status:** accepted

## Context
The hackathon version used one DuckDB file shared by the API, the dashboard, and pipeline
subprocesses. DuckDB allows only one process to write at a time, so these processes ran into lock
errors. It also needed a copy-and-merge workaround for uploads, and that workaround lost data.

## Decision
Use PostgreSQL 16, accessed through SQLAlchemy 2.0. Alembic handles schema migrations.

## Why
- **Concurrent writers:** the web app (uploads, triage edits) and any number of workers write at the
  same time. Postgres's row-level locking handles this; an embedded file database does not.
- **Job queue for free:** `SELECT ... FOR UPDATE SKIP LOCKED` lets workers claim jobs safely
  without a separate queue service (see ADR 0001).
- **Integrity:** foreign keys and constraints catch bugs that the old schema let through silently
  (e.g. failures pointing at runs that didn't exist).
- **Full-text search** (`tsvector`) for searching error messages, with no extra service.

## Alternatives considered
- **DuckDB**: excellent for analytics on a single machine, but this workload is mostly many small
  transactional writes from several processes.
- **SQLite**: same single-writer limit.

## Consequences
- An extra container, but Docker Compose starts it automatically and users never install Postgres.
- Schema changes go through Alembic migrations, never ad-hoc `CREATE TABLE IF NOT EXISTS`.
