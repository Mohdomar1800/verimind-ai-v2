"""Milestone 4: build shared verification context from parsed artifacts and repo metadata."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True)
class ModuleMetrics:
    repo: str
    module_name: str
    tests_total: int
    failed_tests: int
    failures_total: int
    failure_rate: float
    coverage_gap: float | None
    open_issues: int
    recent_commits: int
    artifact_files: int


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def default_run_id() -> str:
    return f"run_{utc_now().strftime('%Y%m%d_%H%M%S')}"


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def compute_context_score(metrics: ModuleMetrics) -> float:
    # Weighted deterministic score (0..100) used as shared context risk signal.
    failure_component = metrics.failure_rate * 0.45
    coverage_component = (metrics.coverage_gap if metrics.coverage_gap is not None else 50.0) * 0.25
    issue_component = min(metrics.open_issues * 8.0, 20.0)
    commit_component = min(metrics.recent_commits * 4.0, 20.0)
    artifact_component = min(metrics.artifact_files * 1.0, 10.0)

    score = failure_component + coverage_component + issue_component + commit_component + artifact_component
    return round(clamp(score, 0.0, 100.0), 4)


def risk_level(score: float) -> str:
    if score >= 70.0:
        return "high"
    if score >= 40.0:
        return "medium"
    return "low"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build shared verification context model")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument("--run-id", default=None, help="Optional run id. Default is timestamp-based.")
    parser.add_argument(
        "--recent-days",
        type=int,
        default=180,
        help="Recent commit window in days used for context signals",
    )
    return parser.parse_args()


def init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMP,
            recent_days INTEGER,
            source_counts_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_context (
            run_id TEXT,
            repo TEXT,
            module_name TEXT,
            tests_total INTEGER,
            failures_total INTEGER,
            failure_rate DOUBLE,
            coverage_gap DOUBLE,
            open_issues INTEGER,
            recent_commits INTEGER,
            artifact_files INTEGER,
            context_score DOUBLE,
            risk_level TEXT,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (run_id, repo, module_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_results (
            result_id TEXT PRIMARY KEY,
            run_id TEXT,
            item_id TEXT,
            item_type TEXT,
            analysis_type TEXT,
            score DOUBLE,
            rank INTEGER,
            evidence_json TEXT,
            created_at TIMESTAMP
        )
        """
    )


def clear_run(conn: duckdb.DuckDBPyConnection, run_id: str) -> None:
    conn.execute("DELETE FROM verification_context WHERE run_id = ?", [run_id])
    conn.execute("DELETE FROM analysis_results WHERE run_id = ?", [run_id])
    conn.execute("DELETE FROM analysis_runs WHERE run_id = ?", [run_id])


def source_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ["commits", "issues", "artifact_files", "tests", "modules", "failures", "coverage"]:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except duckdb.Error:
            counts[table] = 0
    return counts


def fetch_module_candidates(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        WITH candidates AS (
            SELECT repo, module_name FROM modules WHERE module_name IS NOT NULL AND module_name <> ''
            UNION
            SELECT repo, module_name FROM tests WHERE module_name IS NOT NULL AND module_name <> ''
            UNION
            SELECT repo, module_name FROM failures WHERE module_name IS NOT NULL AND module_name <> ''
            UNION
            SELECT repo, module_name FROM coverage WHERE module_name IS NOT NULL AND module_name <> ''
        )
        SELECT repo, module_name
        FROM candidates
        ORDER BY repo, module_name
        """
    ).fetchall()
    return [(str(repo), str(module_name)) for repo, module_name in rows]


def fetch_module_metrics(
    conn: duckdb.DuckDBPyConnection,
    repo: str,
    module_name: str,
    recent_days: int,
) -> ModuleMetrics:
    module_l = module_name.lower()
    now = utc_now()
    cutoff = now - dt.timedelta(days=recent_days)

    tests_total = conn.execute(
        "SELECT COUNT(*) FROM tests WHERE repo = ? AND lower(module_name) = ?",
        [repo, module_l],
    ).fetchone()[0]

    failed_tests = conn.execute(
        "SELECT COUNT(*) FROM tests WHERE repo = ? AND lower(module_name) = ? AND lower(status) = 'fail'",
        [repo, module_l],
    ).fetchone()[0]

    failures_total = conn.execute(
        "SELECT COUNT(*) FROM failures WHERE repo = ? AND lower(module_name) = ?",
        [repo, module_l],
    ).fetchone()[0]

    failure_rate = (failed_tests / tests_total * 100.0) if tests_total else 0.0

    cov_row = conn.execute(
        """
        SELECT AVG(coverage_gap)
        FROM coverage
        WHERE repo = ? AND lower(module_name) = ? AND coverage_gap IS NOT NULL
        """,
        [repo, module_l],
    ).fetchone()
    coverage_gap = float(cov_row[0]) if cov_row and cov_row[0] is not None else None

    open_issues = conn.execute(
        """
        SELECT COUNT(*)
        FROM issues
        WHERE repo = ?
          AND lower(state) = 'open'
          AND lower(title) LIKE ?
        """,
        [repo, f"%{module_l}%"],
    ).fetchone()[0]

    recent_commits = conn.execute(
        """
        SELECT COUNT(*)
        FROM commits
        WHERE repo = ?
          AND commit_date >= ?
          AND lower(message) LIKE ?
        """,
        [repo, cutoff.isoformat(), f"%{module_l}%"],
    ).fetchone()[0]

    artifact_files = conn.execute(
        """
        SELECT COUNT(*)
        FROM artifact_files
        WHERE repo = ?
          AND lower(relative_path) LIKE ?
        """,
        [repo, f"%{module_l}%"],
    ).fetchone()[0]

    return ModuleMetrics(
        repo=repo,
        module_name=module_name,
        tests_total=tests_total,
        failed_tests=failed_tests,
        failures_total=failures_total,
        failure_rate=round(failure_rate, 4),
        coverage_gap=round(coverage_gap, 4) if coverage_gap is not None else None,
        open_issues=int(open_issues),
        recent_commits=int(recent_commits),
        artifact_files=int(artifact_files),
    )


def build_context_rows(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    recent_days: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    created_at = utc_now().isoformat()
    for repo, module_name in fetch_module_candidates(conn):
        metrics = fetch_module_metrics(conn, repo, module_name, recent_days)
        score = compute_context_score(metrics)
        evidence = {
            "failure_rate": metrics.failure_rate,
            "failed_tests": metrics.failed_tests,
            "coverage_gap": metrics.coverage_gap,
            "open_issues": metrics.open_issues,
            "recent_commits": metrics.recent_commits,
            "artifact_files": metrics.artifact_files,
            "tests_total": metrics.tests_total,
            "failures_total": metrics.failures_total,
            "recent_days": recent_days,
        }
        rows.append(
            {
                "run_id": run_id,
                "repo": metrics.repo,
                "module_name": metrics.module_name,
                "tests_total": metrics.tests_total,
                "failures_total": metrics.failures_total,
                "failure_rate": metrics.failure_rate,
                "coverage_gap": metrics.coverage_gap,
                "open_issues": metrics.open_issues,
                "recent_commits": metrics.recent_commits,
                "artifact_files": metrics.artifact_files,
                "context_score": score,
                "risk_level": risk_level(score),
                "evidence_json": json.dumps(evidence),
                "created_at": created_at,
            }
        )

    rows.sort(key=lambda r: (r["context_score"], r["failures_total"], r["tests_total"]), reverse=True)
    return rows


def insert_rows(conn: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    keys = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    values = [tuple(row[key] for key in keys) for row in rows]
    conn.executemany(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", values)
    return len(rows)


def context_to_analysis_results(run_id: str, context_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created_at = utc_now().isoformat()
    results: list[dict[str, Any]] = []
    for rank, row in enumerate(context_rows, start=1):
        item_id = f"{row['repo']}:{row['module_name']}"
        results.append(
            {
                "result_id": make_id("result", run_id, item_id, "verification_context"),
                "run_id": run_id,
                "item_id": item_id,
                "item_type": "module",
                "analysis_type": "verification_context",
                "score": row["context_score"],
                "rank": rank,
                "evidence_json": row["evidence_json"],
                "created_at": created_at,
            }
        )

    summary = {
        "module_count": len(context_rows),
        "high_risk": sum(1 for r in context_rows if r["risk_level"] == "high"),
        "medium_risk": sum(1 for r in context_rows if r["risk_level"] == "medium"),
        "low_risk": sum(1 for r in context_rows if r["risk_level"] == "low"),
    }
    results.append(
        {
            "result_id": make_id("result", run_id, "run_summary", "verification_context"),
            "run_id": run_id,
            "item_id": run_id,
            "item_type": "run",
            "analysis_type": "run_summary",
            "score": float(summary["high_risk"]),
            "rank": 1,
            "evidence_json": json.dumps(summary),
            "created_at": created_at,
        }
    )
    return results


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB not found at {db_path}. Run ingest + parse first.")

    run_id = args.run_id or default_run_id()

    with duckdb.connect(str(db_path)) as conn:
        init_tables(conn)
        clear_run(conn, run_id)

        counts = source_counts(conn)
        conn.execute(
            "INSERT INTO analysis_runs (run_id, created_at, recent_days, source_counts_json) VALUES (?, ?, ?, ?)",
            [run_id, utc_now().isoformat(), args.recent_days, json.dumps(counts)],
        )

        context_rows = build_context_rows(conn, run_id, args.recent_days)
        analysis_rows = context_to_analysis_results(run_id, context_rows)

        n_context = insert_rows(conn, "verification_context", context_rows)
        n_results = insert_rows(conn, "analysis_results", analysis_rows)

        top = context_rows[:5]
        print(f"Milestone 4 context build complete. run_id={run_id}")
        print(f"  verification_context rows: {n_context}")
        print(f"  analysis_results rows: {n_results}")
        print("  top modules:")
        for row in top:
            print(
                f"    - {row['repo']}:{row['module_name']} score={row['context_score']} "
                f"risk={row['risk_level']} fail_rate={row['failure_rate']}"
            )


if __name__ == "__main__":
    main()
