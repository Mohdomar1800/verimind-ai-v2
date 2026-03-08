from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb


def db_path() -> Path:
    return Path(os.getenv("VERIMIND_DB_PATH", "data/verimind4.duckdb"))


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path()))


def parse_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {"value": out}
        except json.JSONDecodeError:
            return {"raw": value}
    return {"value": value}


def latest_run_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute("SELECT run_id FROM analysis_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def latest_model_run_id(conn: duckdb.DuckDBPyConnection, task: str) -> str | None:
    row = conn.execute(
        "SELECT model_run_id FROM model_runs WHERE task = ? ORDER BY created_at DESC LIMIT 1",
        [task],
    ).fetchone()
    return str(row[0]) if row else None
