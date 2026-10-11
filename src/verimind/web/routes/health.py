"""Health endpoints used by Docker healthchecks and monitoring.

- /api/health        liveness: the process is up and serving requests.
- /api/health/ready  readiness: dependencies (the database) are reachable.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from verimind import __version__
from verimind.db import session as db_session

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def liveness() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def readiness() -> JSONResponse:
    db_ok = db_session.check_database()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "unavailable", "database": db_ok},
    )
