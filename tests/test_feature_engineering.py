import json
from pathlib import Path

import duckdb

from pipeline.feature_engineering import (
    build_debug_features,
    build_module_features,
    build_test_features,
    init_feature_tables,
    load_context_index,
    resolve_context_run_id,
)


def prepare_db(path: Path) -> duckdb.DuckDBPyConnection:
    if path.exists():
        path.unlink()
    conn = duckdb.connect(str(path))

    conn.execute(
        """
        CREATE TABLE analysis_runs (
          run_id TEXT, created_at TIMESTAMP, recent_days INTEGER, source_counts_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE verification_context (
          run_id TEXT, repo TEXT, module_name TEXT, tests_total INTEGER, failures_total INTEGER,
          failure_rate DOUBLE, coverage_gap DOUBLE, open_issues INTEGER, recent_commits INTEGER,
          artifact_files INTEGER, context_score DOUBLE, risk_level TEXT, evidence_json TEXT,
          created_at TIMESTAMP
        )
        """
    )
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

    conn.execute(
        "INSERT INTO analysis_runs VALUES ('run_a', '2026-03-06', 180, '{\"x\":1}')"
    )
    conn.execute(
        """
        INSERT INTO verification_context VALUES
        ('run_a','ibex','alu',2,1,50.0,45.0,3,4,2,75.0,'high','{}','2026-03-06')
        """
    )
    conn.execute(
        "INSERT INTO tests VALUES ('t1','ibex','alu','alu_test_pass','formal','pass',10,0,'dv/alu/pass.log','{}','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO tests VALUES ('t2','ibex','alu','alu_test_fail','formal','fail',10,2,'dv/alu/fail.log','{}','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO failures VALUES ('f1','ibex','t2','alu','2','assert','CHECK-fail: assert timeout','dv/alu/fail.log',42,'{}','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO coverage VALUES ('c1','ibex','alu','test_pass_rate',50.0,50.0,'x','{}','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO issues VALUES ('ibex',1,1,'alu open issue','open','2026-03-01','2026-03-02',NULL,'[]','u','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO commits VALUES ('ibex','abc','a','a@b.com','2026-03-05','alu commit','u','2026-03-06')"
    )
    conn.execute(
        "INSERT INTO artifact_files VALUES ('ibex','local_clone','metadata','dv/alu/report.json',200,'2026-03-06')"
    )

    return conn


def test_feature_engineering_builders() -> None:
    db = Path('data/processed/test_feature_engineering.duckdb')
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = prepare_db(db)

    try:
        init_feature_tables(conn)
        run_id = resolve_context_run_id(conn, None)
        assert run_id == 'run_a'

        feature_run_id = 'feat_run_a'
        mod = build_module_features(conn, run_id, feature_run_id)
        assert len(mod) == 1
        assert mod[0]['label_failure_risk'] == 1

        # Insert module features because test feature builder reads coverage label from this table.
        keys = list(mod[0].keys())
        vals = [tuple(r[k] for k in keys) for r in mod]
        conn.executemany(
            f"INSERT INTO module_features ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})",
            vals,
        )

        idx = load_context_index(conn, run_id)
        tst = build_test_features(conn, run_id, feature_run_id, idx)
        dbg = build_debug_features(conn, run_id, feature_run_id, idx)

        assert len(tst) == 2
        assert len(dbg) == 1
        assert any(r['label_failure_risk'] == 1 for r in tst)
        assert dbg[0]['label_debug_priority'] == 1

        payload = json.loads(tst[0]['evidence_json'])
        assert 'context_score' in payload
    finally:
        conn.close()
        if db.exists():
            db.unlink()
