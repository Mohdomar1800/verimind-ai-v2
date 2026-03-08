"""Milestone 8: debug prioritization using log similarity (SentenceTransformers + FAISS)."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{prefix}_{digest}"


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(matrix, axis=1, keepdims=True)
    denom[denom == 0] = 1.0
    return matrix / denom


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VeriMind4 analysis tasks")
    parser.add_argument("--task", choices=["debug"], default="debug")
    parser.add_argument("--db-path", default="data/verimind4.duckdb")
    parser.add_argument("--feature-run-id", default="feat_dataset_gen")
    parser.add_argument("--context-run-id", default=None)
    parser.add_argument("--encoder", choices=["sentence_transformers", "tfidf"], default="sentence_transformers")
    parser.add_argument("--model-name", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS debug_similarity_results (
            context_run_id TEXT,
            feature_run_id TEXT,
            encoder TEXT,
            failure_id TEXT,
            repo TEXT,
            module_name TEXT,
            test_id TEXT,
            severity_num INTEGER,
            similarity_avg DOUBLE,
            similarity_max DOUBLE,
            context_score DOUBLE,
            issue_score DOUBLE,
            priority_score DOUBLE,
            rank INTEGER,
            message_snippet TEXT,
            evidence_json TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (context_run_id, feature_run_id, encoder, failure_id)
        )
        """
    )


def resolve_context_run_id(conn: duckdb.DuckDBPyConnection, feature_run_id: str, context_run_id: str | None) -> str:
    if context_run_id:
        return context_run_id
    row = conn.execute(
        "SELECT context_run_id FROM feature_runs WHERE feature_run_id = ? ORDER BY created_at DESC LIMIT 1",
        [feature_run_id],
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    row = conn.execute("SELECT run_id FROM analysis_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("Unable to resolve context_run_id. Run context/feature pipeline first.")
    return str(row[0])


def load_debug_frame(conn: duckdb.DuckDBPyConnection, feature_run_id: str) -> np.ndarray | list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            f.failure_id,
            f.repo,
            f.module_name,
            f.test_id,
            COALESCE(df.severity_num, 0) AS severity_num,
            COALESCE(df.module_context_score, 0.0) AS context_score,
            COALESCE(df.module_open_issues, 0) AS open_issues,
            COALESCE(f.failure_type, '') AS failure_type,
            COALESCE(f.message, '') AS message
        FROM failures f
        LEFT JOIN debug_features df
          ON df.failure_id = f.failure_id
         AND df.feature_run_id = ?
        ORDER BY f.repo, f.module_name, f.failure_id
        """,
        [feature_run_id],
    ).fetchall()

    frame: list[dict[str, object]] = []
    for r in rows:
        frame.append(
            {
                "failure_id": str(r[0]),
                "repo": str(r[1]),
                "module_name": str(r[2] or "unknown"),
                "test_id": str(r[3] or ""),
                "severity_num": int(r[4] or 0),
                "context_score": float(r[5] or 0.0),
                "open_issues": int(r[6] or 0),
                "failure_type": str(r[7] or ""),
                "message": str(r[8] or ""),
            }
        )
    return frame


def encode_texts(texts: list[str], encoder: str, model_name: str) -> tuple[np.ndarray, str, str]:
    if encoder == "sentence_transformers":
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model = SentenceTransformer(model_name)
            emb = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            return np.asarray(emb, dtype=np.float32), "sentence_transformers", model_name
        except Exception:
            # Fallback keeps pipeline functional if model download/import fails.
            encoder = "tfidf"

    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(max_features=2048, ngram_range=(1, 2), min_df=1)
    matrix = vec.fit_transform(texts).toarray().astype(np.float32)
    matrix = l2_normalize(matrix)
    return matrix, "tfidf", "sklearn_tfidf"


def build_similarity(embeddings: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
    n = embeddings.shape[0]
    if n == 0:
        return np.array([], dtype=np.float32), np.array([], dtype=np.float32)

    k = min(top_k + 1, n)

    try:
        import faiss  # type: ignore

        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)
        sims, idxs = index.search(embeddings, k)
    except Exception:
        # Numpy fallback if FAISS is unavailable.
        sim_mat = np.matmul(embeddings, embeddings.T)
        idxs = np.argsort(-sim_mat, axis=1)[:, :k]
        sims = np.take_along_axis(sim_mat, idxs, axis=1)

    avg_sim = np.zeros(n, dtype=np.float32)
    max_sim = np.zeros(n, dtype=np.float32)

    for i in range(n):
        neigh = []
        for j, s in zip(idxs[i], sims[i]):
            if int(j) == i:
                continue
            neigh.append(float(s))
        if neigh:
            avg_sim[i] = float(np.mean(neigh))
            max_sim[i] = float(np.max(neigh))

    return avg_sim, max_sim


def priority_score(severity_num: int, avg_sim: float, context_score: float, open_issues: int) -> float:
    sev = min(max(float(severity_num), 0.0), 3.0) / 3.0
    sim = min(max(float(avg_sim), 0.0), 1.0)
    ctx = min(max(float(context_score), 0.0), 100.0) / 100.0
    issue = min(max(float(open_issues), 0.0), 10.0) / 10.0
    score = 0.40 * sev + 0.35 * sim + 0.20 * ctx + 0.05 * issue
    return float(min(max(score, 0.0), 1.0))


def write_results(
    conn: duckdb.DuckDBPyConnection,
    context_run_id: str,
    feature_run_id: str,
    encoder_used: str,
    model_ref: str,
    rows: list[dict[str, object]],
) -> tuple[int, int]:
    conn.execute(
        "DELETE FROM debug_similarity_results WHERE context_run_id = ? AND feature_run_id = ? AND encoder = ?",
        [context_run_id, feature_run_id, encoder_used],
    )
    conn.execute(
        "DELETE FROM analysis_results WHERE run_id = ? AND analysis_type = 'debug_priority'",
        [context_run_id],
    )

    created_at = now_iso()
    d_rows = []
    a_rows = []
    for rank, row in enumerate(rows, start=1):
        evidence = {
            "encoder": encoder_used,
            "model_ref": model_ref,
            "severity_num": int(row["severity_num"]),
            "similarity_avg": float(row["similarity_avg"]),
            "similarity_max": float(row["similarity_max"]),
            "context_score": float(row["context_score"]),
            "open_issues": int(row["open_issues"]),
        }

        d_rows.append(
            (
                context_run_id,
                feature_run_id,
                encoder_used,
                row["failure_id"],
                row["repo"],
                row["module_name"],
                row["test_id"],
                int(row["severity_num"]),
                float(row["similarity_avg"]),
                float(row["similarity_max"]),
                float(row["context_score"]),
                min(float(row["open_issues"]) / 10.0, 1.0),
                float(row["priority_score"]),
                rank,
                str(row["message"])[:200],
                json.dumps(evidence),
                created_at,
            )
        )

        result_id = make_id("result", context_run_id, str(row["failure_id"]), "debug_priority")
        a_rows.append(
            (
                result_id,
                context_run_id,
                str(row["failure_id"]),
                "failure",
                "debug_priority",
                float(row["priority_score"]),
                rank,
                json.dumps(evidence),
                created_at,
            )
        )

    if d_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO debug_similarity_results
            (context_run_id, feature_run_id, encoder, failure_id, repo, module_name, test_id,
             severity_num, similarity_avg, similarity_max, context_score, issue_score,
             priority_score, rank, message_snippet, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            d_rows,
        )

    if a_rows:
        conn.executemany(
            """
            INSERT INTO analysis_results
            (result_id, run_id, item_id, item_type, analysis_type, score, rank, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            a_rows,
        )

    return len(d_rows), len(a_rows)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)

    if args.task != "debug":
        raise ValueError("Only --task debug is supported in Milestone 8")

    with duckdb.connect(str(db_path)) as conn:
        init_tables(conn)
        context_run_id = resolve_context_run_id(conn, args.feature_run_id, args.context_run_id)
        frame = load_debug_frame(conn, args.feature_run_id)

        if not frame:
            print("No failure rows available for debug prioritization.")
            return

        texts = [
            f"repo={r['repo']} module={r['module_name']} type={r['failure_type']} msg={r['message']}"
            for r in frame
        ]

        emb, encoder_used, model_ref = encode_texts(texts, args.encoder, args.model_name)
        avg_sim, max_sim = build_similarity(emb, args.top_k)

        rows: list[dict[str, object]] = []
        for i, rec in enumerate(frame):
            score = priority_score(
                int(rec["severity_num"]),
                float(avg_sim[i]) if i < len(avg_sim) else 0.0,
                float(rec["context_score"]),
                int(rec["open_issues"]),
            )
            row = dict(rec)
            row["similarity_avg"] = float(avg_sim[i]) if i < len(avg_sim) else 0.0
            row["similarity_max"] = float(max_sim[i]) if i < len(max_sim) else 0.0
            row["priority_score"] = score
            rows.append(row)

        rows.sort(key=lambda r: (float(r["priority_score"]), int(r["severity_num"]), float(r["context_score"])), reverse=True)

        n_debug, n_analysis = write_results(conn, context_run_id, args.feature_run_id, encoder_used, model_ref, rows)

        print("Milestone 8 debug prioritization complete")
        print(f"  context_run_id: {context_run_id}")
        print(f"  feature_run_id: {args.feature_run_id}")
        print(f"  encoder_used: {encoder_used}")
        print(f"  model_ref: {model_ref}")
        print(f"  debug_similarity_results rows: {n_debug}")
        print(f"  analysis_results rows inserted (debug_priority): {n_analysis}")
        print("  top debug items:")
        for row in rows[:10]:
            print(
                f"    - {row['failure_id']} {row['repo']}:{row['module_name']} "
                f"priority={float(row['priority_score']):.4f} sim={float(row['similarity_avg']):.4f} sev={int(row['severity_num'])}"
            )


if __name__ == "__main__":
    main()
