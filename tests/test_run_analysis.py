from pathlib import Path

import duckdb
import numpy as np

from pipeline.run_analysis import (
    build_similarity,
    encode_texts,
    init_tables,
    load_debug_frame,
    priority_score,
    write_results,
)


def test_priority_score_bounds() -> None:
    s = priority_score(2, 0.7, 50.0, 3)
    assert 0.0 <= s <= 1.0


def test_build_similarity_nonempty() -> None:
    emb = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)
    avg, mx = build_similarity(emb, top_k=2)
    assert len(avg) == 3
    assert len(mx) == 3


def test_debug_pipeline_helpers() -> None:
    db = Path('data/processed/test_run_analysis.duckdb')
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    conn = duckdb.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE failures (
              failure_id TEXT, repo TEXT, module_name TEXT, test_id TEXT,
              severity TEXT, failure_type TEXT, message TEXT, source_path TEXT,
              source_line INTEGER, evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE debug_features (
              feature_run_id TEXT, context_run_id TEXT, failure_id TEXT,
              repo TEXT, module_name TEXT, test_id TEXT, severity_raw TEXT,
              severity_num INTEGER, failure_type TEXT, message_len INTEGER,
              has_assert INTEGER, has_timeout INTEGER, has_check_fail INTEGER,
              has_diffail INTEGER, source_line_missing INTEGER,
              module_context_score DOUBLE, module_failure_rate DOUBLE,
              module_open_issues INTEGER, module_recent_commits INTEGER,
              label_debug_priority INTEGER, evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute("CREATE TABLE feature_runs (feature_run_id TEXT, context_run_id TEXT, created_at TIMESTAMP, row_counts_json TEXT)")
        conn.execute("CREATE TABLE analysis_runs (run_id TEXT, created_at TIMESTAMP, recent_days INTEGER, source_counts_json TEXT)")
        conn.execute(
            """
            CREATE TABLE analysis_results (
              result_id TEXT, run_id TEXT, item_id TEXT, item_type TEXT,
              analysis_type TEXT, score DOUBLE, rank INTEGER,
              evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )

        conn.execute("INSERT INTO feature_runs VALUES ('feat_x','run_x','2026-03-06','{}')")
        conn.execute("INSERT INTO analysis_runs VALUES ('run_x','2026-03-06',180,'{}')")

        conn.execute(
            "INSERT INTO failures VALUES ('f1','ibex','alu','t1','2','assert','CHECK-fail assert happened','x',1,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO failures VALUES ('f2','ibex','lsu','t2','1','timeout','timeout in sim','x',2,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO debug_features VALUES ('feat_x','run_x','f1','ibex','alu','t1','2',2,'assert',20,1,0,1,0,0,70.0,50.0,2,3,1,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO debug_features VALUES ('feat_x','run_x','f2','ibex','lsu','t2','1',1,'timeout',14,0,1,0,0,0,40.0,20.0,1,1,1,'{}','2026-03-06')"
        )

        init_tables(conn)
        frame = load_debug_frame(conn, 'feat_x')
        assert len(frame) == 2

        texts = [f"repo={r['repo']} module={r['module_name']} msg={r['message']}" for r in frame]
        emb, enc, model_ref = encode_texts(texts, encoder='tfidf', model_name='unused')
        assert enc == 'tfidf'
        assert emb.shape[0] == 2
        avg, mx = build_similarity(emb, top_k=1)
        rows = []
        for i, rec in enumerate(frame):
            d = dict(rec)
            d['similarity_avg'] = float(avg[i])
            d['similarity_max'] = float(mx[i])
            d['priority_score'] = priority_score(int(rec['severity_num']), float(avg[i]), float(rec['context_score']), int(rec['open_issues']))
            rows.append(d)
        rows.sort(key=lambda r: float(r['priority_score']), reverse=True)

        n_debug, n_analysis = write_results(conn, 'run_x', 'feat_x', 'tfidf', model_ref, rows)
        assert n_debug == 2
        assert n_analysis == 2
        assert conn.execute("select count(*) from debug_similarity_results").fetchone()[0] == 2
        assert conn.execute("select count(*) from analysis_results where analysis_type='debug_priority'").fetchone()[0] == 2
    finally:
        conn.close()
        if db.exists():
            db.unlink()
