"""FastAPI dependencies: database session, storage, parser, and common lookups."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from verimind.config import get_settings
from verimind.db.models import Run
from verimind.db.session import get_session
from verimind.engine import FormatRegistry, LogParser
from verimind.services.ai import make_client
from verimind.storage import Storage, default_storage

SessionDep = Annotated[Session, Depends(get_session)]


def get_storage() -> Storage:
    return default_storage()


@lru_cache
def get_parser() -> LogParser:
    formats_dir = get_settings().formats_dir
    return LogParser(FormatRegistry.load([formats_dir] if formats_dir else None))


def get_ai_client() -> Any | None:
    """Anthropic client for the optional AI summaries; None when no API key is configured."""
    return make_client()


StorageDep = Annotated[Storage, Depends(get_storage)]
AiClientDep = Annotated[Any | None, Depends(get_ai_client)]
ParserDep = Annotated[LogParser, Depends(get_parser)]


def load_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} does not exist.")
    return run
