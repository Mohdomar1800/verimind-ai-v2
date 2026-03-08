"""Milestone 5: build ML-ready features from verification context and parsed artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import duckdb


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ML feature tables for VeriMind4")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Context run_id from verification_context; default uses latest analysis_runs row.",
    )
    parser.add_argument(
        "--feature-run-id",
        default=None,
        help="Feature snapshot id; default is feat_<context_run_id>.",
    )
    return parser.parse_args()


def init_feature_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_runs (
            feature_run_id TEXT PRIMARY KEY,
            context_run_id TEXT,
            created_at TIMESTAMP,
            row_counts_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS module_features (
            feature_run_id TEXT,
            context_run_id TEXT,
            repo TEXT,
            module_name TEXT,
            tests_total INTEGER,
            failed_tests INTEGER,
            pass_tests INTEGER,
            failures_total INTEGER,
            failure_rate DOUBLE,
            failure_event_density DOUBLE,
            coverage_gap DOUBLE,
            has_coverage INTEGER,
            open_issues INTEGER,
            issue_pressure DOUBLE,
            recent_commits INTEGER,
            commit_activity_ratio DOUBLE,
            artifact_files INTEGER,
            artifact_density DOUBLE,
            context_score DOUBLE,
            risk_level TEXT,
            module_name_len INTEGER,
            module_token_count INTEGER,
            label_failure_risk INTEGER,
            label_coverage_priority INTEGER,
            label_debug_priority INTEGER,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (feature_run_id, repo, module_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS test_features (
            feature_run_id TEXT,
            context_run_id TEXT,
            test_id TEXT,
            repo TEXT,
            module_name TEXT,
            test_name TEXT,
            test_type TEXT,
            total_events INTEGER,
            failure_events INTEGER,
            event_failure_ratio DOUBLE,
            module_context_score DOUBLE,
            module_failure_rate DOUBLE,
            module_open_issues INTEGER,
            module_recent_commits INTEGER,
            module_coverage_gap DOUBLE,
            test_name_len INTEGER,
            source_path_depth INTEGER,
            status TEXT,
            label_failure_risk INTEGER,
            label_debug_priority INTEGER,
            label_coverage_priority INTEGER,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (feature_run_id, test_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS debug_features (
            feature_run_id TEXT,
            context_run_id TEXT,
            failure_id TEXT,
            repo TEXT,
            module_name TEXT,
            test_id TEXT,
            severity_raw TEXT,
            severity_num INTEGER,
            failure_type TEXT,
            message_len INTEGER,
            has_assert INTEGER,
            has_timeout INTEGER,
            has_check_fail INTEGER,
            has_diffail INTEGER,
            source_line_missing INTEGER,
            module_context_score DOUBLE,
            module_failure_rate DOUBLE,
            module_open_issues INTEGER,
            module_recent_commits INTEGER,
            label_debug_priority INTEGER,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (feature_run_id, failure_id)
        )
        """
    )


def clear_feature_run(conn: duckdb.DuckDBPyConnection, feature_run_id: str) -> None:
    conn.execute("DELETE FROM module_features WHERE feature_run_id = ?", [feature_run_id])
    conn.execute("DELETE FROM test_features WHERE feature_run_id = ?", [feature_run_id])
    conn.execute("DELETE FROM debug_features WHERE feature_run_id = ?", [feature_run_id])
    conn.execute("DELETE FROM feature_runs WHERE feature_run_id = ?", [feature_run_id])


def resolve_context_run_id(conn: duckdb.DuckDBPyConnection, run_id: str | None) -> str:
    if run_id:
        exists = conn.execute("SELECT COUNT(*) FROM verification_context WHERE run_id = ?", [run_id]).fetchone()[0]
        if exists == 0:
            raise ValueError(f"No verification_context rows found for run_id={run_id}")
        return run_id

    row = conn.execute("SELECT run_id FROM analysis_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("No analysis_runs found. Run pipeline.build_context first.")
    return str(row[0])


def load_context_index(conn: duckdb.DuckDBPyConnection, context_run_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT repo, module_name, tests_total, failures_total, failure_rate,
               coverage_gap, open_issues, recent_commits, artifact_files,
               context_score, risk_level
        FROM verification_context
        WHERE run_id = ?
        """,
        [context_run_id],
    ).fetchall()

    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        repo, module_name = str(row[0]), str(row[1])
        index[(repo, module_name)] = {
            "tests_total": int(row[2] or 0),
            "failures_total": int(row[3] or 0),
            "failure_rate": float(row[4] or 0.0),
            "coverage_gap": float(row[5]) if row[5] is not None else None,
            "open_issues": int(row[6] or 0),
            "recent_commits": int(row[7] or 0),
            "artifact_files": int(row[8] or 0),
            "context_score": float(row[9] or 0.0),
            "risk_level": str(row[10] or "low"),
        }
    return index


def build_module_features(
    conn: duckdb.DuckDBPyConnection,
    context_run_id: str,
    feature_run_id: str,
) -> list[dict[str, Any]]:
    context_rows = conn.execute(
        """
        SELECT repo, module_name, tests_total, failures_total, failure_rate,
               coverage_gap, open_issues, recent_commits, artifact_files,
               context_score, risk_level
        FROM verification_context
        WHERE run_id = ?
        ORDER BY repo, module_name
        """,
        [context_run_id],
    ).fetchall()

    repo_max = conn.execute(
        """
        SELECT repo,
               MAX(open_issues) AS max_open_issues,
               MAX(recent_commits) AS max_recent_commits,
               MAX(artifact_files) AS max_artifact_files
        FROM verification_context
        WHERE run_id = ?
        GROUP BY repo
        """,
        [context_run_id],
    ).fetchall()
    repo_norm = {str(r[0]): (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in repo_max}

    failed_test_rows = conn.execute(
        """
        SELECT repo, module_name, COUNT(*)
        FROM tests
        WHERE lower(status) = 'fail'
        GROUP BY repo, module_name
        """
    ).fetchall()
    failed_tests_map = {(str(r[0]), str(r[1])): int(r[2]) for r in failed_test_rows}

    coverage_presence_rows = conn.execute(
        """
        SELECT repo, module_name, COUNT(*)
        FROM coverage
        WHERE coverage_gap IS NOT NULL OR coverage_value IS NOT NULL
        GROUP BY repo, module_name
        """
    ).fetchall()
    has_cov_map = {(str(r[0]), str(r[1])): int(r[2]) for r in coverage_presence_rows}

    created_at = utc_now_iso()
    rows: list[dict[str, Any]] = []

    for row in context_rows:
        repo = str(row[0])
        module_name = str(row[1])
        tests_total = int(row[2] or 0)
        failures_total = int(row[3] or 0)
        failure_rate = float(row[4] or 0.0)
        coverage_gap = float(row[5]) if row[5] is not None else None
        open_issues = int(row[6] or 0)
        recent_commits = int(row[7] or 0)
        artifact_files = int(row[8] or 0)
        context_score = float(row[9] or 0.0)
        risk_level = str(row[10] or "low")

        failed_tests = failed_tests_map.get((repo, module_name), 0)
        pass_tests = max(tests_total - failed_tests, 0)

        max_open, max_recent, max_art = repo_norm.get(repo, (0, 0, 0))
        issue_pressure = safe_ratio(open_issues, max_open)
        commit_activity_ratio = safe_ratio(recent_commits, max_recent)
        artifact_density = safe_ratio(artifact_files, max_art)

        has_coverage = 1 if has_cov_map.get((repo, module_name), 0) > 0 else 0

        failure_event_density = safe_ratio(failures_total, tests_total)

        label_failure_risk = 1 if (failed_tests > 0 or failure_rate >= 20.0 or context_score >= 70.0) else 0
        label_coverage_priority = 1 if ((coverage_gap is not None and coverage_gap >= 40.0) or (has_coverage == 0 and tests_total > 0)) else 0
        label_debug_priority = 1 if (failures_total > 0 or label_failure_risk == 1) else 0

        evidence = {
            "failed_tests": failed_tests,
            "rule_failure_risk": "failed_tests>0 or failure_rate>=20 or context_score>=70",
            "rule_coverage_priority": "coverage_gap>=40 or missing_coverage_with_tests",
            "rule_debug_priority": "failures_total>0 or failure_risk",
        }

        rows.append(
            {
                "feature_run_id": feature_run_id,
                "context_run_id": context_run_id,
                "repo": repo,
                "module_name": module_name,
                "tests_total": tests_total,
                "failed_tests": failed_tests,
                "pass_tests": pass_tests,
                "failures_total": failures_total,
                "failure_rate": round(failure_rate, 6),
                "failure_event_density": round(failure_event_density, 6),
                "coverage_gap": coverage_gap,
                "has_coverage": has_coverage,
                "open_issues": open_issues,
                "issue_pressure": round(issue_pressure, 6),
                "recent_commits": recent_commits,
                "commit_activity_ratio": round(commit_activity_ratio, 6),
                "artifact_files": artifact_files,
                "artifact_density": round(artifact_density, 6),
                "context_score": round(context_score, 6),
                "risk_level": risk_level,
                "module_name_len": len(module_name),
                "module_token_count": max(1, len([x for x in module_name.replace('-', '_').split('_') if x])),
                "label_failure_risk": label_failure_risk,
                "label_coverage_priority": label_coverage_priority,
                "label_debug_priority": label_debug_priority,
                "evidence_json": json.dumps(evidence),
                "created_at": created_at,
            }
        )

    return rows


def build_test_features(
    conn: duckdb.DuckDBPyConnection,
    context_run_id: str,
    feature_run_id: str,
    context_index: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    tests_rows = conn.execute(
        """
        SELECT test_id, repo, module_name, test_name, test_type, status,
               total_events, failure_events, source_path
        FROM tests
        ORDER BY repo, module_name, test_id
        """
    ).fetchall()

    module_label_rows = conn.execute(
        """
        SELECT repo, module_name, label_coverage_priority
        FROM module_features
        WHERE feature_run_id = ?
        """,
        [feature_run_id],
    ).fetchall()
    module_cov_label = {(str(r[0]), str(r[1])): int(r[2]) for r in module_label_rows}

    created_at = utc_now_iso()
    rows: list[dict[str, Any]] = []

    for row in tests_rows:
        test_id = str(row[0])
        repo = str(row[1])
        module_name = str(row[2] or "unknown")
        test_name = str(row[3] or "unknown_test")
        test_type = str(row[4] or "unknown")
        status = str(row[5] or "unknown").lower()
        total_events = int(row[6] or 0)
        failure_events = int(row[7] or 0)
        source_path = str(row[8] or "")

        c = context_index.get((repo, module_name), None)
        context_score = float(c["context_score"]) if c else 0.0
        failure_rate = float(c["failure_rate"]) if c else 0.0
        open_issues = int(c["open_issues"]) if c else 0
        recent_commits = int(c["recent_commits"]) if c else 0
        coverage_gap = float(c["coverage_gap"]) if c and c["coverage_gap"] is not None else None

        event_failure_ratio = safe_ratio(failure_events, total_events)

        label_failure_risk = 1 if status == "fail" else 0
        label_debug_priority = 1 if (failure_events > 0 or status == "fail") else 0
        label_coverage_priority = int(module_cov_label.get((repo, module_name), 0))

        evidence = {
            "status": status,
            "source_path": source_path,
            "context_score": context_score,
        }

        rows.append(
            {
                "feature_run_id": feature_run_id,
                "context_run_id": context_run_id,
                "test_id": test_id,
                "repo": repo,
                "module_name": module_name,
                "test_name": test_name,
                "test_type": test_type,
                "total_events": total_events,
                "failure_events": failure_events,
                "event_failure_ratio": round(event_failure_ratio, 6),
                "module_context_score": round(context_score, 6),
                "module_failure_rate": round(failure_rate, 6),
                "module_open_issues": open_issues,
                "module_recent_commits": recent_commits,
                "module_coverage_gap": coverage_gap,
                "test_name_len": len(test_name),
                "source_path_depth": len([x for x in source_path.split('/') if x]),
                "status": status,
                "label_failure_risk": label_failure_risk,
                "label_debug_priority": label_debug_priority,
                "label_coverage_priority": label_coverage_priority,
                "evidence_json": json.dumps(evidence),
                "created_at": created_at,
            }
        )

    return rows


def parse_severity_num(raw: str | None) -> int:
    if raw is None:
        return 0
    text = str(raw).strip().lower()
    if text.isdigit():
        return int(text)
    if text in {"fatal", "high"}:
        return 3
    if text in {"error", "medium"}:
        return 2
    if text in {"warn", "warning", "low"}:
        return 1
    return 0


def build_debug_features(
    conn: duckdb.DuckDBPyConnection,
    context_run_id: str,
    feature_run_id: str,
    context_index: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_raw = conn.execute(
        """
        SELECT failure_id, repo, module_name, test_id, severity, failure_type, message, source_line
        FROM failures
        ORDER BY repo, module_name, failure_id
        """
    ).fetchall()

    created_at = utc_now_iso()
    rows: list[dict[str, Any]] = []

    for row in rows_raw:
        failure_id = str(row[0])
        repo = str(row[1])
        module_name = str(row[2] or "unknown")
        test_id = str(row[3] or "")
        severity_raw = str(row[4] or "")
        failure_type = str(row[5] or "unknown")
        message = str(row[6] or "")
        source_line = row[7]

        severity_num = parse_severity_num(severity_raw)
        msg_l = message.lower()
        has_assert = 1 if "assert" in msg_l else 0
        has_timeout = 1 if "timeout" in msg_l else 0
        has_check_fail = 1 if "check-fail" in msg_l else 0
        has_diffail = 1 if "dif-fail" in msg_l else 0

        c = context_index.get((repo, module_name), None)
        context_score = float(c["context_score"]) if c else 0.0
        failure_rate = float(c["failure_rate"]) if c else 0.0
        open_issues = int(c["open_issues"]) if c else 0
        recent_commits = int(c["recent_commits"]) if c else 0

        # Deterministic priority label for training/debug queue bootstrap.
        label_debug_priority = 1 if (severity_num >= 2 or has_assert or has_timeout or has_check_fail or context_score >= 70.0) else 0

        evidence = {
            "severity_num": severity_num,
            "keyword_flags": {
                "assert": has_assert,
                "timeout": has_timeout,
                "check_fail": has_check_fail,
                "dif_fail": has_diffail,
            },
        }

        rows.append(
            {
                "feature_run_id": feature_run_id,
                "context_run_id": context_run_id,
                "failure_id": failure_id,
                "repo": repo,
                "module_name": module_name,
                "test_id": test_id,
                "severity_raw": severity_raw,
                "severity_num": severity_num,
                "failure_type": failure_type,
                "message_len": len(message),
                "has_assert": has_assert,
                "has_timeout": has_timeout,
                "has_check_fail": has_check_fail,
                "has_diffail": has_diffail,
                "source_line_missing": 1 if source_line is None else 0,
                "module_context_score": round(context_score, 6),
                "module_failure_rate": round(failure_rate, 6),
                "module_open_issues": open_issues,
                "module_recent_commits": recent_commits,
                "label_debug_priority": label_debug_priority,
                "evidence_json": json.dumps(evidence),
                "created_at": created_at,
            }
        )

    return rows


def insert_rows(conn: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    keys = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(keys))
    cols = ", ".join(keys)
    values = [tuple(r[k] for k in keys) for r in rows]
    conn.executemany(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})", values)
    return len(rows)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB not found at {db_path}. Run previous pipeline stages first.")

    with duckdb.connect(str(db_path)) as conn:
        init_feature_tables(conn)

        context_run_id = resolve_context_run_id(conn, args.run_id)
        feature_run_id = args.feature_run_id or f"feat_{context_run_id}"

        clear_feature_run(conn, feature_run_id)

        context_index = load_context_index(conn, context_run_id)

        module_rows = build_module_features(conn, context_run_id, feature_run_id)
        n_modules = insert_rows(conn, "module_features", module_rows)

        test_rows = build_test_features(conn, context_run_id, feature_run_id, context_index)
        n_tests = insert_rows(conn, "test_features", test_rows)

        debug_rows = build_debug_features(conn, context_run_id, feature_run_id, context_index)
        n_debug = insert_rows(conn, "debug_features", debug_rows)

        summary = {
            "module_features": n_modules,
            "test_features": n_tests,
            "debug_features": n_debug,
        }

        conn.execute(
            "INSERT INTO feature_runs (feature_run_id, context_run_id, created_at, row_counts_json) VALUES (?, ?, ?, ?)",
            [feature_run_id, context_run_id, utc_now_iso(), json.dumps(summary)],
        )

        print("Milestone 5 feature engineering complete")
        print(f"  context_run_id: {context_run_id}")
        print(f"  feature_run_id: {feature_run_id}")
        print(f"  module_features: {n_modules}")
        print(f"  test_features: {n_tests}")
        print(f"  debug_features: {n_debug}")


if __name__ == "__main__":
    main()
