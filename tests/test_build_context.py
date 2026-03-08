import json
from pathlib import Path

import duckdb

from pipeline.build_context import (
    ModuleMetrics,
    build_context_rows,
    compute_context_score,
    context_to_analysis_results,
    init_tables,
)


def test_compute_context_score_bounds() -> None:
    m = ModuleMetrics(
        repo="ibex",
        module_name="alu",
        tests_total=10,
        failed_tests=5,
        failures_total=5,
        failure_rate=50.0,
        coverage_gap=40.0,
        open_issues=3,
        recent_commits=4,
        artifact_files=5,
    )
    score = compute_context_score(m)
    assert 0.0 <= score <= 100.0


def test_build_context_rows_and_results() -> None:
    db_path = Path("data/processed/test_build_context.duckdb")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE tests (
              test_id TEXT, repo TEXT, module_name TEXT, test_name TEXT, test_type TEXT,
              status TEXT, total_events INTEGER, failure_events INTEGER, source_path TEXT,
              metadata_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE failures (
              failure_id TEXT, repo TEXT, test_id TEXT, module_name TEXT, severity TEXT,
              failure_type TEXT, message TEXT, source_path TEXT, source_line INTEGER,
              evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE coverage (
              coverage_id TEXT, repo TEXT, module_name TEXT, coverage_type TEXT,
              coverage_value DOUBLE, coverage_gap DOUBLE, source_path TEXT,
              evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE modules (
              module_id TEXT, repo TEXT, module_name TEXT, module_path TEXT, module_type TEXT,
              source_path TEXT, register_count INTEGER, signal_count INTEGER,
              metadata_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE issues (
              repo TEXT, issue_id BIGINT, issue_number BIGINT, title TEXT, state TEXT,
              created_at TIMESTAMP, updated_at TIMESTAMP, closed_at TIMESTAMP,
              labels_json TEXT, html_url TEXT, ingested_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE commits (
              repo TEXT, sha TEXT, author_name TEXT, author_email TEXT, commit_date TIMESTAMP,
              message TEXT, html_url TEXT, ingested_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE artifact_files (
              repo TEXT, source TEXT, category TEXT, relative_path TEXT, size_bytes BIGINT, ingested_at TIMESTAMP
            )
            """
        )

        # Seed minimal but realistic context signals for one module.
        conn.execute(
            "INSERT INTO tests VALUES ('t1','ibex','alu','alu_test_1','formal','pass',10,0,'x','','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO tests VALUES ('t2','ibex','alu','alu_test_2','formal','fail',10,1,'x','','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO failures VALUES ('f1','ibex','t2','alu','2','assert','boom','x',1,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO coverage VALUES ('c1','ibex','alu','test_pass_rate',50.0,50.0,'x','{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO modules VALUES ('m1','ibex','alu','','derived','',NULL,NULL,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO issues VALUES ('ibex',1,1,'alu issue open','open','2026-03-01','2026-03-02',NULL,'[]','u','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO commits VALUES ('ibex','abc','a','a@b.com','2026-03-05','alu fix','u','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO artifact_files VALUES ('ibex','local_clone','metadata','dv/alu/alu_report.json',100,'2026-03-06')"
        )

        init_tables(conn)
        rows = build_context_rows(conn, run_id="run_test", recent_days=180)
        assert len(rows) >= 1
        target = [r for r in rows if r["repo"] == "ibex" and r["module_name"] == "alu"][0]
        assert target["tests_total"] == 2
        assert target["failures_total"] == 1
        assert target["open_issues"] == 1
        assert target["recent_commits"] == 1

        analysis = context_to_analysis_results("run_test", rows)
        assert len(analysis) >= 2
        summary = [r for r in analysis if r["item_type"] == "run"][0]
        payload = json.loads(summary["evidence_json"])
        assert "module_count" in payload
    finally:
        conn.close()
        if db_path.exists():
            db_path.unlink()
