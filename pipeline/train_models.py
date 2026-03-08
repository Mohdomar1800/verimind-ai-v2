"""Milestones 6-7: train failure and coverage prioritization models."""

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
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

try:
    from lightgbm import LGBMClassifier  # type: ignore

    HAS_LIGHTGBM = True
except Exception:
    HAS_LIGHTGBM = False

try:
    import joblib  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("joblib is required for model persistence") from exc


FEATURE_COLUMNS = [
    "tests_total",
    "failed_tests",
    "pass_tests",
    "failures_total",
    "failure_rate",
    "failure_event_density",
    "coverage_gap",
    "has_coverage",
    "open_issues",
    "issue_pressure",
    "recent_commits",
    "commit_activity_ratio",
    "artifact_files",
    "artifact_density",
    "context_score",
    "module_name_len",
    "module_token_count",
]


TASK_CONFIG = {
    "failure": {
        "label_col": "label_failure_risk",
        "pred_table": "failure_predictions",
        "analysis_type": "failure_prediction",
        "model_prefix": "failure_model",
        "metrics_prefix": "failure_metrics",
    },
    "coverage": {
        "label_col": "label_coverage_priority",
        "pred_table": "coverage_predictions",
        "analysis_type": "coverage_priority",
        "model_prefix": "coverage_model",
        "metrics_prefix": "coverage_metrics",
    },
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train VeriMind4 models")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument("--feature-run-id", default="feat_dataset_gen")
    parser.add_argument("--task", choices=["failure", "coverage"], default="failure")
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--model-out-dir", default="data/processed/models")
    parser.add_argument("--model-run-id", default=None)
    return parser.parse_args()


def recall_at_k(y_true: np.ndarray, scores: np.ndarray, k_frac: float) -> float:
    if len(y_true) == 0:
        return 0.0
    k = max(1, int(np.ceil(len(y_true) * k_frac)))
    top_idx = np.argsort(scores)[::-1][:k]
    hits = int(np.sum(y_true[top_idx]))
    positives = int(np.sum(y_true))
    if positives == 0:
        return 0.0
    return float(hits) / float(positives)


def init_model_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_runs (
            model_run_id TEXT PRIMARY KEY,
            model_name TEXT,
            task TEXT,
            feature_run_id TEXT,
            created_at TIMESTAMP,
            model_path TEXT,
            metrics_json TEXT,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_predictions (
            model_run_id TEXT,
            feature_run_id TEXT,
            context_run_id TEXT,
            repo TEXT,
            module_name TEXT,
            true_label INTEGER,
            pred_label INTEGER,
            pred_prob DOUBLE,
            split TEXT,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (model_run_id, repo, module_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coverage_predictions (
            model_run_id TEXT,
            feature_run_id TEXT,
            context_run_id TEXT,
            repo TEXT,
            module_name TEXT,
            true_label INTEGER,
            pred_label INTEGER,
            pred_prob DOUBLE,
            split TEXT,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (model_run_id, repo, module_name)
        )
        """
    )


def load_training_frame(conn: duckdb.DuckDBPyConnection, feature_run_id: str) -> pd.DataFrame:
    query = """
        SELECT feature_run_id, context_run_id, repo, module_name,
               label_failure_risk, label_coverage_priority,
               tests_total, failed_tests, pass_tests, failures_total,
               failure_rate, failure_event_density, coverage_gap, has_coverage,
               open_issues, issue_pressure, recent_commits, commit_activity_ratio,
               artifact_files, artifact_density, context_score,
               module_name_len, module_token_count
        FROM module_features
        WHERE feature_run_id = ?
        ORDER BY repo, module_name
    """
    frame = conn.execute(query, [feature_run_id]).fetchdf()
    if frame.empty:
        raise ValueError(f"No module_features rows found for feature_run_id={feature_run_id}")
    return frame


def choose_model(random_state: int) -> tuple[str, Pipeline]:
    if HAS_LIGHTGBM:
        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "clf",
                    LGBMClassifier(
                        n_estimators=300,
                        learning_rate=0.05,
                        num_leaves=31,
                        objective="binary",
                        class_weight="balanced",
                        random_state=random_state,
                        verbosity=-1,
                    ),
                ),
            ]
        )
        return "lightgbm", model

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=400,
                    max_depth=12,
                    min_samples_leaf=2,
                    class_weight="balanced_subsample",
                    random_state=random_state,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    return "random_forest", model


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_score: np.ndarray) -> dict[str, float]:
    metrics: dict[str, float] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "pr_auc": float(average_precision_score(y_true, y_score)) if len(np.unique(y_true)) > 1 else 0.0,
        "recall_at_10pct": float(recall_at_k(y_true, y_score, 0.10)),
        "recall_at_20pct": float(recall_at_k(y_true, y_score, 0.20)),
    }
    metrics["roc_auc"] = float(roc_auc_score(y_true, y_score)) if len(np.unique(y_true)) > 1 else 0.0
    return metrics


def insert_predictions(
    conn: duckdb.DuckDBPyConnection,
    pred_table: str,
    model_run_id: str,
    feature_run_id: str,
    context_run_id: str,
    frame: pd.DataFrame,
    analysis_type: str,
) -> int:
    if frame.empty:
        return 0

    rows = []
    created_at = now_iso()
    for _, row in frame.iterrows():
        evidence = {
            "score_source": "model_probability",
            "analysis_type": analysis_type,
            "repo": row["repo"],
            "module_name": row["module_name"],
        }
        rows.append(
            (
                model_run_id,
                feature_run_id,
                context_run_id,
                row["repo"],
                row["module_name"],
                int(row["true_label"]),
                int(row["pred_label"]),
                float(row["pred_prob"]),
                row["split"],
                json.dumps(evidence),
                created_at,
            )
        )

    conn.executemany(
        f"""
        INSERT OR REPLACE INTO {pred_table}
        (model_run_id, feature_run_id, context_run_id, repo, module_name, true_label, pred_label, pred_prob, split, evidence_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_analysis_results(
    conn: duckdb.DuckDBPyConnection,
    model_run_id: str,
    context_run_id: str,
    scored: pd.DataFrame,
    analysis_type: str,
) -> int:
    # Keep only one ranking list per (run_id, analysis_type).
    conn.execute(
        "DELETE FROM analysis_results WHERE run_id = ? AND analysis_type = ?",
        [context_run_id, analysis_type],
    )

    test_rows = scored[scored["split"] == "test"].copy()
    if test_rows.empty:
        return 0
    test_rows = test_rows.sort_values("pred_prob", ascending=False).reset_index(drop=True)

    created_at = now_iso()
    rows = []
    for idx, row in test_rows.iterrows():
        item_id = f"{row['repo']}:{row['module_name']}"
        result_id = make_id("result", model_run_id, item_id, analysis_type)
        evidence = {
            "true_label": int(row["true_label"]),
            "pred_label": int(row["pred_label"]),
            "pred_prob": float(row["pred_prob"]),
            "split": row["split"],
            "model_run_id": model_run_id,
        }
        rows.append(
            (
                result_id,
                context_run_id,
                item_id,
                "module",
                analysis_type,
                float(row["pred_prob"]),
                int(idx + 1),
                json.dumps(evidence),
                created_at,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO analysis_results
        (result_id, run_id, item_id, item_type, analysis_type, score, rank, evidence_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def main() -> None:
    args = parse_args()
    cfg = TASK_CONFIG[args.task]
    db_path = Path(args.db_path)
    model_out_dir = Path(args.model_out_dir)
    model_out_dir.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(db_path)) as conn:
        init_model_tables(conn)

        frame = load_training_frame(conn, args.feature_run_id)
        context_run_ids = frame["context_run_id"].dropna().unique().tolist()
        context_run_id = str(context_run_ids[0]) if context_run_ids else "unknown_context_run"

        label_col = cfg["label_col"]
        y = frame[label_col].astype(int).to_numpy()
        x = frame[FEATURE_COLUMNS].astype(float)

        unique_labels = np.unique(y)
        single_class_mode = len(unique_labels) < 2

        idx_all = np.arange(len(frame))
        if single_class_mode:
            idx_train = idx_all
            idx_test = idx_all
            y_train = y
            y_test = y
            model_name = "rule_based_single_class"
            model: Any = None
            p_train = np.where(y_train == 1, 0.95, 0.05).astype(float)
            p_test = np.where(y_test == 1, 0.95, 0.05).astype(float)
            yhat_train = y_train.copy()
            yhat_test = y_test.copy()
        else:
            x_train, x_test, y_train, y_test, idx_train, idx_test = train_test_split(
                x,
                y,
                idx_all,
                test_size=args.test_size,
                random_state=args.random_state,
                stratify=y,
            )

            model_name, model = choose_model(args.random_state)
            model.fit(x_train, y_train)

            p_train = model.predict_proba(x_train)[:, 1]
            p_test = model.predict_proba(x_test)[:, 1]
            yhat_train = (p_train >= 0.5).astype(int)
            yhat_test = (p_test >= 0.5).astype(int)

        train_metrics = compute_metrics(y_train, yhat_train, p_train)
        test_metrics = compute_metrics(y_test, yhat_test, p_test)

        model_run_id = args.model_run_id or make_id(
            "modelrun", args.feature_run_id, args.task, model_name, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        )
        model_path = model_out_dir / f"{cfg['model_prefix']}_{model_run_id}.joblib"
        if model is not None:
            joblib.dump(model, model_path)
        else:
            model_path.write_text("single_class_rule_based_model_placeholder", encoding="utf-8")

        scored = frame[["repo", "module_name"]].copy()
        scored["true_label"] = y
        scored["pred_prob"] = 0.0
        scored["pred_label"] = 0
        scored["split"] = "train"
        scored.loc[idx_train, "pred_prob"] = p_train
        scored.loc[idx_train, "pred_label"] = yhat_train
        scored.loc[idx_train, "split"] = "train"
        scored.loc[idx_test, "pred_prob"] = p_test
        scored.loc[idx_test, "pred_label"] = yhat_test
        scored.loc[idx_test, "split"] = "test"

        n_preds = insert_predictions(
            conn,
            cfg["pred_table"],
            model_run_id,
            args.feature_run_id,
            context_run_id,
            scored,
            cfg["analysis_type"],
        )
        n_analysis = upsert_analysis_results(conn, model_run_id, context_run_id, scored, cfg["analysis_type"])

        metrics = {
            "train": train_metrics,
            "test": test_metrics,
            "rows": int(len(frame)),
            "positive_labels": int(np.sum(y)),
            "negative_labels": int(len(y) - np.sum(y)),
        }
        metadata = {
            "feature_columns": FEATURE_COLUMNS,
            "label_col": label_col,
            "test_size": args.test_size,
            "random_state": args.random_state,
            "context_run_id": context_run_id,
            "single_class_mode": single_class_mode,
        }

        conn.execute(
            """
            INSERT OR REPLACE INTO model_runs
            (model_run_id, model_name, task, feature_run_id, created_at, model_path, metrics_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                model_run_id,
                model_name,
                args.task,
                args.feature_run_id,
                now_iso(),
                str(model_path),
                json.dumps(metrics),
                json.dumps(metadata),
            ],
        )

        metrics_path = model_out_dir / f"{cfg['metrics_prefix']}_{model_run_id}.json"
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

        print(f"Milestone {'6' if args.task == 'failure' else '7'} {args.task} model training complete")
        print(f"  model_run_id: {model_run_id}")
        print(f"  model_type: {model_name}")
        print(f"  feature_run_id: {args.feature_run_id}")
        print(f"  model_path: {model_path}")
        print(f"  metrics_path: {metrics_path}")
        print(f"  train_metrics: {json.dumps(train_metrics)}")
        print(f"  test_metrics: {json.dumps(test_metrics)}")
        if single_class_mode:
            print("  note: single-class labels detected; used deterministic rule-based scoring fallback")
        print(f"  predictions rows ({cfg['pred_table']}): {n_preds}")
        print(f"  analysis_results rows inserted ({cfg['analysis_type']}): {n_analysis}")


if __name__ == "__main__":
    main()
