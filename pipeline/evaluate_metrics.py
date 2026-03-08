"""Milestone 12: evaluate baseline vs AI-assisted verification workflow metrics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate VeriMind4 models and ranking outputs")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument("--run-id", default="run_m4_dataset_gen")
    parser.add_argument("--feature-run-id", default="feat_dataset_gen")
    parser.add_argument("--failure-model-run-id", default=None)
    parser.add_argument("--coverage-model-run-id", default=None)
    parser.add_argument("--output-csv", default="data/processed/evaluation_summary.csv")
    return parser.parse_args()


def init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            eval_run_id TEXT PRIMARY KEY,
            run_id TEXT,
            feature_run_id TEXT,
            failure_model_run_id TEXT,
            coverage_model_run_id TEXT,
            created_at TIMESTAMP,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluation_metrics (
            metric_id TEXT PRIMARY KEY,
            eval_run_id TEXT,
            metric_group TEXT,
            metric_name TEXT,
            method TEXT,
            k_value INTEGER,
            value DOUBLE,
            details_json TEXT,
            created_at TIMESTAMP
        )
        """
    )


def latest_model_run_id(conn: duckdb.DuckDBPyConnection, task: str) -> str:
    row = conn.execute(
        "SELECT model_run_id FROM model_runs WHERE task = ? ORDER BY created_at DESC LIMIT 1",
        [task],
    ).fetchone()
    if not row:
        raise ValueError(f"No model run found for task={task}")
    return str(row[0])


def recall_at_k_binary(labels: np.ndarray, scores: np.ndarray, k: int) -> float:
    if len(labels) == 0:
        return 0.0
    positives = int(np.sum(labels))
    if positives == 0:
        return 0.0
    k = max(1, min(k, len(labels)))
    idx = np.argsort(scores)[::-1][:k]
    hits = int(np.sum(labels[idx]))
    return float(hits) / float(positives)


def precision_at_k_binary(labels: np.ndarray, scores: np.ndarray, k: int) -> float:
    if len(labels) == 0:
        return 0.0
    k = max(1, min(k, len(labels)))
    idx = np.argsort(scores)[::-1][:k]
    hits = int(np.sum(labels[idx]))
    return float(hits) / float(k)


def dcg_at_k(relevance: np.ndarray, scores: np.ndarray, k: int) -> float:
    if len(relevance) == 0:
        return 0.0
    k = max(1, min(k, len(relevance)))
    idx = np.argsort(scores)[::-1][:k]
    rel = relevance[idx]
    denom = np.log2(np.arange(2, k + 2))
    return float(np.sum(rel / denom))


def ndcg_at_k(relevance: np.ndarray, scores: np.ndarray, k: int) -> float:
    dcg = dcg_at_k(relevance, scores, k)
    ideal_scores = relevance.astype(float)
    idcg = dcg_at_k(relevance, ideal_scores, k)
    if idcg <= 0:
        return 0.0
    return float(dcg / idcg)


def evaluate_failure(conn: duckdb.DuckDBPyConnection, model_run_id: str) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        SELECT fp.repo, fp.module_name, fp.true_label, fp.pred_prob,
               COALESCE(vc.context_score, 0.0) AS baseline_score
        FROM failure_predictions fp
        LEFT JOIN verification_context vc
          ON vc.run_id = fp.context_run_id
         AND vc.repo = fp.repo
         AND vc.module_name = fp.module_name
        WHERE fp.model_run_id = ? AND fp.split = 'test'
        """,
        [model_run_id],
    ).fetchdf()

    if df.empty:
        return []

    y = df["true_label"].astype(int).to_numpy()
    ai = df["pred_prob"].astype(float).to_numpy()
    base = df["baseline_score"].astype(float).to_numpy()
    n = len(df)

    metrics: list[dict[str, Any]] = []
    for frac in [0.1, 0.2, 0.3]:
        k = max(1, int(np.ceil(n * frac)))
        metrics.append({"metric_group": "failure", "metric_name": "topk_recall", "method": "ai", "k_value": k, "value": recall_at_k_binary(y, ai, k), "details": {"fraction": frac, "n": n}})
        metrics.append({"metric_group": "failure", "metric_name": "topk_recall", "method": "baseline", "k_value": k, "value": recall_at_k_binary(y, base, k), "details": {"fraction": frac, "n": n}})
    return metrics


def evaluate_coverage(conn: duckdb.DuckDBPyConnection, model_run_id: str) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        SELECT cp.repo, cp.module_name, cp.true_label, cp.pred_prob,
               COALESCE(mf.tests_total, 0) AS tests_total,
               COALESCE(mf.coverage_gap, 0.0) AS coverage_gap,
               COALESCE(mf.context_score, 0.0) AS baseline_score
        FROM coverage_predictions cp
        LEFT JOIN module_features mf
          ON mf.feature_run_id = cp.feature_run_id
         AND mf.repo = cp.repo
         AND mf.module_name = cp.module_name
        WHERE cp.model_run_id = ? AND cp.split = 'test'
        """,
        [model_run_id],
    ).fetchdf()

    if df.empty:
        return []

    y = df["true_label"].astype(int).to_numpy()
    ai = df["pred_prob"].astype(float).to_numpy()
    base = df["baseline_score"].astype(float).to_numpy()
    n = len(df)

    # Coverage-gain proxy per test: average(coverage_gap / (tests_total + 1)) over top-k.
    gain_proxy = (df["coverage_gap"].astype(float) / (df["tests_total"].astype(float) + 1.0)).to_numpy()

    metrics: list[dict[str, Any]] = []
    for frac in [0.1, 0.2, 0.3]:
        k = max(1, int(np.ceil(n * frac)))
        ai_idx = np.argsort(ai)[::-1][:k]
        base_idx = np.argsort(base)[::-1][:k]

        metrics.append({"metric_group": "coverage", "metric_name": "topk_recall", "method": "ai", "k_value": k, "value": recall_at_k_binary(y, ai, k), "details": {"fraction": frac, "n": n}})
        metrics.append({"metric_group": "coverage", "metric_name": "topk_recall", "method": "baseline", "k_value": k, "value": recall_at_k_binary(y, base, k), "details": {"fraction": frac, "n": n}})

        metrics.append({"metric_group": "coverage", "metric_name": "coverage_gain_per_test", "method": "ai", "k_value": k, "value": float(np.mean(gain_proxy[ai_idx])) if len(ai_idx) else 0.0, "details": {"fraction": frac, "n": n}})
        metrics.append({"metric_group": "coverage", "metric_name": "coverage_gain_per_test", "method": "baseline", "k_value": k, "value": float(np.mean(gain_proxy[base_idx])) if len(base_idx) else 0.0, "details": {"fraction": frac, "n": n}})
    return metrics


def evaluate_debug(conn: duckdb.DuckDBPyConnection, run_id: str, feature_run_id: str) -> list[dict[str, Any]]:
    df = conn.execute(
        """
        SELECT ar.item_id AS failure_id,
               ar.score AS ai_score,
               COALESCE(df2.severity_num, 0) AS severity_num,
               COALESCE(df2.module_context_score, 0.0) AS context_score
        FROM analysis_results ar
        LEFT JOIN debug_features df2
          ON df2.failure_id = ar.item_id
         AND df2.feature_run_id = ?
        WHERE ar.run_id = ? AND ar.analysis_type = 'debug_priority'
        """,
        [feature_run_id, run_id],
    ).fetchdf()

    if df.empty:
        return []

    ai = df["ai_score"].astype(float).to_numpy()
    # Baseline: severity first, then context score.
    base = (df["severity_num"].astype(float) * 10.0 + df["context_score"].astype(float)).to_numpy()

    # Relevance: severity >= 2 as positive, graded relevance by severity.
    binary_rel = (df["severity_num"].astype(int) >= 2).astype(int).to_numpy()
    graded_rel = np.clip(df["severity_num"].astype(float).to_numpy(), 0.0, 3.0)

    n = len(df)
    metrics: list[dict[str, Any]] = []
    for k in [10, 20, 50]:
        kk = min(k, n)
        metrics.append({"metric_group": "debug", "metric_name": "precision_at_k", "method": "ai", "k_value": kk, "value": precision_at_k_binary(binary_rel, ai, kk), "details": {"n": n}})
        metrics.append({"metric_group": "debug", "metric_name": "precision_at_k", "method": "baseline", "k_value": kk, "value": precision_at_k_binary(binary_rel, base, kk), "details": {"n": n}})

        metrics.append({"metric_group": "debug", "metric_name": "ndcg_at_k", "method": "ai", "k_value": kk, "value": ndcg_at_k(graded_rel, ai, kk), "details": {"n": n}})
        metrics.append({"metric_group": "debug", "metric_name": "ndcg_at_k", "method": "baseline", "k_value": kk, "value": ndcg_at_k(graded_rel, base, kk), "details": {"n": n}})
    return metrics


def persist_metrics(conn: duckdb.DuckDBPyConnection, eval_run_id: str, metrics: list[dict[str, Any]]) -> int:
    if not metrics:
        return 0
    created_at = now_iso()
    rows = []
    for m in metrics:
        metric_id = make_id("metric", eval_run_id, m["metric_group"], m["metric_name"], m["method"], str(m["k_value"]))
        rows.append(
            (
                metric_id,
                eval_run_id,
                m["metric_group"],
                m["metric_name"],
                m["method"],
                int(m["k_value"]),
                float(m["value"]),
                json.dumps(m.get("details", {})),
                created_at,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO evaluation_metrics
        (metric_id, eval_run_id, metric_group, metric_name, method, k_value, value, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(db_path)) as conn:
        init_tables(conn)

        failure_model_run_id = args.failure_model_run_id or latest_model_run_id(conn, "failure")
        coverage_model_run_id = args.coverage_model_run_id or latest_model_run_id(conn, "coverage")

        eval_run_id = make_id(
            "evalrun",
            args.run_id,
            args.feature_run_id,
            failure_model_run_id,
            coverage_model_run_id,
            dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S"),
        )

        failure_metrics = evaluate_failure(conn, failure_model_run_id)
        coverage_metrics = evaluate_coverage(conn, coverage_model_run_id)
        debug_metrics = evaluate_debug(conn, args.run_id, args.feature_run_id)

        all_metrics = failure_metrics + coverage_metrics + debug_metrics

        conn.execute(
            """
            INSERT OR REPLACE INTO evaluation_runs
            (eval_run_id, run_id, feature_run_id, failure_model_run_id, coverage_model_run_id, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                eval_run_id,
                args.run_id,
                args.feature_run_id,
                failure_model_run_id,
                coverage_model_run_id,
                now_iso(),
                json.dumps({"db_path": str(db_path)}),
            ],
        )

        n_metrics = persist_metrics(conn, eval_run_id, all_metrics)

        out_df = pd.DataFrame(all_metrics)
        out_df.insert(0, "eval_run_id", eval_run_id)
        out_df.to_csv(out_csv, index=False)

        summary = conn.execute(
            """
            SELECT metric_group, metric_name, method, k_value, ROUND(value, 6)
            FROM evaluation_metrics
            WHERE eval_run_id = ?
            ORDER BY metric_group, metric_name, method, k_value
            """,
            [eval_run_id],
        ).fetchdf()

    print("Milestone 12 evaluation complete")
    print(f"  eval_run_id: {eval_run_id}")
    print(f"  run_id: {args.run_id}")
    print(f"  feature_run_id: {args.feature_run_id}")
    print(f"  failure_model_run_id: {failure_model_run_id}")
    print(f"  coverage_model_run_id: {coverage_model_run_id}")
    print(f"  metrics_count: {n_metrics}")
    print(f"  output_csv: {out_csv}")
    print("  metric_summary:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
