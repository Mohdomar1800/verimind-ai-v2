from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd


def connect_db(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path))


def latest_context_run_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute("SELECT run_id FROM analysis_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def context_df(conn: duckdb.DuckDBPyConnection, run_id: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT repo, module_name, context_score, risk_level,
               tests_total, failures_total, failure_rate, coverage_gap,
               open_issues, recent_commits, artifact_files, evidence_json
        FROM verification_context
        WHERE run_id = ?
        ORDER BY context_score DESC
        """,
        [run_id],
    ).fetchdf()


def rankings_df(conn: duckdb.DuckDBPyConnection, run_id: str, analysis_type: str, limit: int = 200) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT item_id, score, rank, evidence_json
        FROM analysis_results
        WHERE run_id = ? AND analysis_type = ?
        ORDER BY rank ASC
        LIMIT ?
        """,
        [run_id, analysis_type, limit],
    ).fetchdf()


def explanations_df(conn: duckdb.DuckDBPyConnection, item_id: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT item_id, run_id, analysis_type, score, rank, evidence_json
        FROM analysis_results
        WHERE item_id = ?
        ORDER BY score DESC
        """,
        [item_id],
    ).fetchdf()


def parse_evidence_column(df: pd.DataFrame, column: str = "evidence_json") -> pd.DataFrame:
    if column not in df.columns:
        return df
    out = df.copy()
    parsed: list[dict[str, object]] = []
    for v in out[column].tolist():
        if isinstance(v, str):
            try:
                parsed.append(json.loads(v))
            except json.JSONDecodeError:
                parsed.append({"raw": v})
        elif isinstance(v, dict):
            parsed.append(v)
        else:
            parsed.append({})
    out["evidence"] = parsed
    return out
