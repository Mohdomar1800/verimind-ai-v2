"""Database test fixtures. They need a real PostgreSQL (the job queue relies on SKIP LOCKED).

Start a throwaway one and point the tests at it:

    docker run -d --name verimind-testdb -p 55432:5432 -e POSTGRES_USER=verimind \
        -e POSTGRES_PASSWORD=verimind -e POSTGRES_DB=verimind_test postgres:16-alpine
    VERIMIND_TEST_DATABASE_URL=postgresql+psycopg://verimind:verimind@localhost:55432/verimind_test pytest

The schema is rebuilt from the migrations once per test session, and every table is emptied after
each test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from verimind.config import get_settings
from verimind.db.migrate import upgrade
from verimind.db.session import reset_caches, session_factory
from verimind.storage import Storage
from verimind.synth import PRESETS, generate_dataset

TEST_DB_ENV = "VERIMIND_TEST_DATABASE_URL"
TABLES = "test_results, issues, jobs, runs, triage"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        pytest.skip(f"database tests need PostgreSQL: set {TEST_DB_ENV}")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    upgrade(url)
    return url


@pytest.fixture
def sessions(database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """A session factory bound to the test database; the app's own helpers use it too."""
    monkeypatch.setenv("VERIMIND_DATABASE_URL", database_url)
    monkeypatch.setenv("VERIMIND_STORAGE_DIR", str(tmp_path / "storage"))
    get_settings.cache_clear()
    reset_caches()
    yield session_factory()
    reset_caches()
    get_settings.cache_clear()
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "storage")


@pytest.fixture(scope="session")
def demo_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo dataset, generated once and reused (tests only read it)."""
    out = tmp_path_factory.mktemp("demo-dataset")
    generate_dataset(PRESETS["demo"], out)
    return out


@pytest.fixture
def analyzed_demo(sessions: sessionmaker[Session], demo_dataset: Path) -> None:
    """The four demo runs, loaded and analyzed (oldest first, as the worker would)."""
    from verimind.engine import Analyzer
    from verimind.services.demo import load_demo
    from verimind.storage import default_storage
    from verimind.worker.main import Worker

    with sessions() as s:
        load_demo(s, default_storage(), dataset_dir=demo_dataset)
        s.commit()
    worker = Worker(sessions, default_storage(), Analyzer, name="test-worker")
    while worker.run_once():
        pass
