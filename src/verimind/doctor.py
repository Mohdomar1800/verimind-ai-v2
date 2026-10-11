"""`verimind doctor`: check every moving part and say, in plain words, how to fix what's wrong."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select

from verimind.config import get_settings
from verimind.db.session import check_database, get_engine, session_scope

Level = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    message: str


def _database() -> Check:
    if check_database():
        return Check("Database", "ok", "Connected.")
    return Check(
        "Database",
        "fail",
        "Can't connect. With Docker, run `docker compose ps` and check that the db container is running.",
    )


def _migrations() -> Check:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from verimind.db.migrate import alembic_config

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with get_engine().connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current == head:
        return Check("Database tables", "ok", "Up to date.")
    return Check("Database tables", "fail", f"Out of date ({current} != {head}). Run `verimind db upgrade`.")


def _storage() -> Check:
    root = get_settings().storage_dir
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root):
            pass
    except OSError as exc:
        return Check("File storage", "fail", f"Can't write to {root}: {exc.strerror}. Check the folder's permissions.")
    return Check("File storage", "ok", f"{root} is writable.")


def _formats() -> Check:
    from verimind.engine import FormatError, FormatRegistry

    formats_dir = get_settings().formats_dir
    try:
        registry = FormatRegistry.load([formats_dir] if formats_dir else None)
    except FormatError as exc:
        return Check("Log formats", "fail", f"{exc}. Fix the file or remove it from VERIMIND_FORMATS_DIR.")
    return Check("Log formats", "ok", f"{len(registry.names())} formats: {', '.join(registry.names())}.")


def _worker() -> Check:
    from verimind.db.models import Job, JobStatus, utcnow

    with session_scope() as session:
        oldest_queued = session.scalar(select(func.min(Job.created_at)).where(Job.status == JobStatus.QUEUED))
        latest_beat = session.scalar(select(func.max(Job.heartbeat_at)))
    now = utcnow()
    if oldest_queued and now - oldest_queued > timedelta(minutes=2):
        return Check(
            "Background worker",
            "warn",
            "Runs have been waiting for over 2 minutes. Check that the worker is running "
            "(`docker compose ps`, then `docker compose logs worker`).",
        )
    if latest_beat is None:
        return Check("Background worker", "info", "No analysis has run yet.")
    return Check("Background worker", "ok", "Processing jobs normally.")


def _ai() -> Check:
    if get_settings().ai_summaries_enabled:
        return Check("AI summaries", "info", "On (ANTHROPIC_API_KEY is set).")
    return Check("AI summaries", "info", "Off. Optional: set ANTHROPIC_API_KEY to enable 'Explain with AI'.")


def run_checks() -> list[Check]:
    checks = [_storage(), _formats(), _ai()]
    database = _database()
    checks.insert(0, database)
    if database.level == "ok":
        checks.insert(1, _migrations())
        checks.insert(2, _worker())
    return checks
