from pathlib import Path

import duckdb

from backend.services.explainability import (
    build_explanations_for_run,
    reason_coverage_priority,
    reason_debug_priority,
    reason_failure_prediction,
)


def test_reason_builders_nonempty() -> None:
    assert len(reason_failure_prediction({"pred_prob": 0.9, "split": "test"})) >= 1
    assert len(reason_coverage_priority({"pred_prob": 0.85, "coverage_gap": 42})) >= 1
    assert len(reason_debug_priority({"severity_num": 2, "similarity_avg": 0.6, "context_score": 75})) >= 1


def test_build_explanations_for_run() -> None:
    db = Path("data/processed/test_explainability.duckdb")
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    conn = duckdb.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE analysis_results (
              result_id TEXT, run_id TEXT, item_id TEXT, item_type TEXT,
              analysis_type TEXT, score DOUBLE, rank INTEGER,
              evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r1','run_x','ibex:alu','module','failure_prediction',0.91,1,'{\"pred_prob\":0.91,\"split\":\"test\"}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r2','run_x','fail_1','failure','debug_priority',0.71,1,'{\"severity_num\":2,\"similarity_avg\":0.66,\"context_score\":74}','2026-03-06')"
        )

        n = build_explanations_for_run(conn, "run_x")
        assert n == 2
        cnt = conn.execute("select count(*) from explanations where run_id='run_x'").fetchone()[0]
        assert cnt == 2
    finally:
        conn.close()
        if db.exists():
            db.unlink()
