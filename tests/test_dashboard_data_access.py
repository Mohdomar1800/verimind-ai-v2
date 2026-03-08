from pathlib import Path

import duckdb

from frontend.dashboard.data_access import context_df, explanations_df, latest_context_run_id, rankings_df


def test_dashboard_data_access_queries() -> None:
    db = Path('data/processed/test_dashboard.duckdb')
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    conn = duckdb.connect(str(db))
    try:
        conn.execute("CREATE TABLE analysis_runs (run_id TEXT, created_at TIMESTAMP, recent_days INTEGER, source_counts_json TEXT)")
        conn.execute(
            "CREATE TABLE verification_context (run_id TEXT, repo TEXT, module_name TEXT, context_score DOUBLE, risk_level TEXT, tests_total INTEGER, failures_total INTEGER, failure_rate DOUBLE, coverage_gap DOUBLE, open_issues INTEGER, recent_commits INTEGER, artifact_files INTEGER, evidence_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE analysis_results (result_id TEXT, run_id TEXT, item_id TEXT, item_type TEXT, analysis_type TEXT, score DOUBLE, rank INTEGER, evidence_json TEXT, created_at TIMESTAMP)"
        )

        conn.execute("INSERT INTO analysis_runs VALUES ('run_dash','2026-03-06',180,'{}')")
        conn.execute(
            "INSERT INTO verification_context VALUES ('run_dash','ibex','alu',82.2,'high',10,3,30.0,50.0,4,5,6,'{}')"
        )
        conn.execute(
            "INSERT INTO analysis_results VALUES ('r1','run_dash','ibex:alu','module','failure_prediction',0.91,1,'{}','2026-03-06')"
        )

        rid = latest_context_run_id(conn)
        assert rid == 'run_dash'

        cdf = context_df(conn, 'run_dash')
        assert len(cdf) == 1
        assert cdf.iloc[0]['repo'] == 'ibex'

        rdf = rankings_df(conn, 'run_dash', 'failure_prediction', 10)
        assert len(rdf) == 1
        assert rdf.iloc[0]['item_id'] == 'ibex:alu'

        edf = explanations_df(conn, 'ibex:alu')
        assert len(edf) == 1
        assert edf.iloc[0]['analysis_type'] == 'failure_prediction'
    finally:
        conn.close()
        if db.exists():
            db.unlink()
