"""Database engine and session management.

One engine per process (it owns the connection pool). Request handlers and jobs each get their own
short-lived `Session`, which is the unit of work: commit on success, roll back on error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from verimind.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # pool_pre_ping transparently replaces connections that the database closed (e.g. after a restart).
    settings = get_settings()
    return create_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": settings.db_connect_timeout}
    )


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transaction: commits if the block succeeds, rolls back if it raises."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_caches() -> None:
    """Forget the cached engine, e.g. after tests change the database URL."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    session_factory.cache_clear()


def check_database() -> bool:
    """Return True if the database accepts connections."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
