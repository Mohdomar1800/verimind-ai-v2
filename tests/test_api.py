import os
from pathlib import Path

import duckdb

from backend.api.main import (
    generate_verification_plan,
    get_context,
    get_explanations,
    health,
    predict_failures,
    prioritize_coverage,
    prioritize_debug,
)
from backend.api.schemas import PredictionRequest, VerificationPlanRequest


def _seed_db(path: Path) -> None:
    conn = duckdb.connect(str(path))
    try:
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
              run_id TEXT, repo TEXT, module_name TEXT, tests_total INTEGER,
              failures_total INTEGER, failure_rate DOUBLE, coverage_gap DOUBLE,
              open_issues INTEGER, recent_commits INTEGER, artifact_files INTEGER,
              context_score DOUBLE, risk_level TEXT, evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE model_runs (
              model_run_id TEXT, model_name TEXT, task TEXT, feature_run_id TEXT,
              created_at TIMESTAMP, model_path TEXT, metrics_json TEXT, metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE failure_predictions (
              model_run_id TEXT, feature_run_id TEXT, context_run_id TEXT,
              repo TEXT, module_name TEXT, true_label INTEGER, pred_label INTEGER,
              pred_prob DOUBLE, split TEXT, evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE analysis_results (
              result_id TEXT, run_id TEXT, item_id TEXT, item_type TEXT,
              analysis_type TEXT, score DOUBLE, rank INTEGER,
              evidence_json TEXT, created_at TIMESTAMP
            )
            """
        )

        conn.execute("INSERT INTO analysis_runs VALUES ('run_api','2026-03-06',180,'{}')")
        conn.execute(
            "INSERT INTO verification_context VALUES ('run_api','ibex','alu',10,2,20.0,40.0,3,4,5,72.5,'high','{\"k\":1}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO model_runs VALUES ('mr_fail','lightgbm','failure','feat_x','2026-03-06','m.joblib','{}','{}')"
        )
        conn.execute(
            "INSERT INTO failure_predictions VALUES ('mr_fail','feat_x','run_api','ibex','alu',1,1,0.91,'test','{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r1','run_api','ibex:alu','module','failure_prediction',0.91,1,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r2','run_api','ibex:alu','module','coverage_priority',0.81,1,'{}','2026-03-06')"
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r3','run_api','failure_x','failure','debug_priority',0.71,1,'{}','2026-03-06')"
        )
    finally:
        conn.close()


def test_api_route_functions() -> None:
    db = Path('data/processed/test_api.duckdb')
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    _seed_db(db)
    os.environ['VERIMIND_DB_PATH'] = str(db)

    h = health()
    assert h['status'] == 'ok'

    ctx = get_context('run_api')
    assert ctx.count == 1

    pred = predict_failures(PredictionRequest(run_id='run_api', top_k=5))
    assert pred.analysis_type == 'failure_prediction'
    assert pred.count == 1

    cov = prioritize_coverage(PredictionRequest(run_id='run_api', top_k=5))
    assert cov.analysis_type == 'coverage_priority'
    assert cov.count == 1

    dbg = prioritize_debug(PredictionRequest(run_id='run_api', top_k=5))
    assert dbg.analysis_type == 'debug_priority'
    assert dbg.count == 1

    plan = generate_verification_plan(VerificationPlanRequest(run_id='run_api', top_k_each=2))
    assert plan.summary['total_plan_items'] >= 3

    ex = get_explanations('ibex:alu', run_id='run_api', analysis_type='failure_prediction')
    assert ex.item_id == 'ibex:alu'
    assert isinstance(ex.reasons, list)
    assert len(ex.reasons) >= 1

    if db.exists():
        db.unlink()
