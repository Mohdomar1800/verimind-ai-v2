from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import duckdb

from backend.services.db import parse_json


def _safe_num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def reason_failure_prediction(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    prob = _safe_num(evidence.get("pred_prob"), 0.0)
    if prob >= 0.8:
        reasons.append(f"High model failure probability ({prob:.2f}).")
    split = evidence.get("split")
    if split:
        reasons.append(f"Prediction originates from '{split}' evaluation split.")
    model_run_id = evidence.get("model_run_id")
    if model_run_id:
        reasons.append(f"Scored by model run {model_run_id}.")
    if not reasons:
        reasons.append("Failure risk estimated from model probability and module context signals.")
    return reasons


def reason_coverage_priority(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    pred_prob = _safe_num(evidence.get("pred_prob"), 0.0)
    if pred_prob >= 0.8:
        reasons.append(f"High predicted coverage-priority score ({pred_prob:.2f}).")
    gap = _safe_num(evidence.get("coverage_gap"), -1)
    if gap >= 0:
        reasons.append(f"Coverage gap signal observed ({gap:.1f}%).")
    if not reasons:
        reasons.append("Prioritized to close expected functional coverage gaps.")
    return reasons


def reason_debug_priority(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    sev = _safe_int(evidence.get("severity_num"), 0)
    sim = _safe_num(evidence.get("similarity_avg"), 0.0)
    ctx = _safe_num(evidence.get("context_score"), 0.0)

    if sev >= 2:
        reasons.append(f"High severity signal (severity={sev}).")
    if sim > 0:
        reasons.append(f"Similar to known failures (avg similarity={sim:.2f}).")
    if ctx >= 70:
        reasons.append(f"Associated module has high context risk ({ctx:.1f}).")
    if not reasons:
        reasons.append("Debug queue rank derived from severity + similarity + context blend.")
    return reasons


def reason_verification_context(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fr = _safe_num(evidence.get("failure_rate"), 0.0)
    gap = _safe_num(evidence.get("coverage_gap"), -1)
    issues = _safe_int(evidence.get("open_issues"), 0)
    commits = _safe_int(evidence.get("recent_commits"), 0)

    reasons.append(f"Failure rate signal: {fr:.1f}%.")
    if gap >= 0:
        reasons.append(f"Coverage gap signal: {gap:.1f}%.")
    reasons.append(f"Open issue mentions: {issues}. Recent commits: {commits}.")
    return reasons


def reason_generic(analysis_type: str, evidence: dict[str, Any]) -> list[str]:
    if analysis_type == "failure_prediction":
        return reason_failure_prediction(evidence)
    if analysis_type == "coverage_priority":
        return reason_coverage_priority(evidence)
    if analysis_type == "debug_priority":
        return reason_debug_priority(evidence)
    if analysis_type == "verification_context":
        return reason_verification_context(evidence)
    return [f"No specialized explainer for analysis type '{analysis_type}'."]


def init_explanations_table(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS explanations (
            explanation_id TEXT PRIMARY KEY,
            run_id TEXT,
            item_id TEXT,
            analysis_type TEXT,
            score DOUBLE,
            rank INTEGER,
            evidence_json TEXT,
            reasons_json TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )


def build_explanations_for_run(conn: duckdb.DuckDBPyConnection, run_id: str) -> int:
    init_explanations_table(conn)

    rows = conn.execute(
        """
        SELECT result_id, run_id, item_id, analysis_type, score, rank, evidence_json
        FROM analysis_results
        WHERE run_id = ?
        """,
        [run_id],
    ).fetchall()

    if not rows:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    upserts = []
    for result_id, r_id, item_id, analysis_type, score, rank, evidence_json in rows:
        evidence = parse_json(evidence_json)
        reasons = reason_generic(str(analysis_type), evidence)
        upserts.append(
            (
                str(result_id),
                str(r_id),
                str(item_id),
                str(analysis_type),
                float(score or 0.0),
                int(rank or 0),
                json.dumps(evidence),
                json.dumps(reasons),
                now,
                now,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO explanations
        (explanation_id, run_id, item_id, analysis_type, score, rank, evidence_json, reasons_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        upserts,
    )
    return len(upserts)


def get_explanation(conn: duckdb.DuckDBPyConnection, item_id: str, run_id: str | None, analysis_type: str | None) -> dict[str, Any] | None:
    init_explanations_table(conn)
    base = """
        SELECT item_id, run_id, analysis_type, score, rank, evidence_json, reasons_json
        FROM explanations
        WHERE item_id = ?
    """
    params: list[object] = [item_id]
    if run_id:
        base += " AND run_id = ?"
        params.append(run_id)
    if analysis_type:
        base += " AND analysis_type = ?"
        params.append(analysis_type)
    base += " ORDER BY score DESC LIMIT 1"

    row = conn.execute(base, params).fetchone()
    if not row:
        return None

    reasons_raw = row[6]
    reasons: list[str]
    if isinstance(reasons_raw, str):
        try:
            parsed = json.loads(reasons_raw)
            if isinstance(parsed, list):
                reasons = [str(x) for x in parsed]
            else:
                reasons = [str(parsed)]
        except json.JSONDecodeError:
            reasons = [reasons_raw]
    elif isinstance(reasons_raw, list):
        reasons = [str(x) for x in reasons_raw]
    else:
        reasons = []

    return {
        "item_id": str(row[0]),
        "run_id": str(row[1]),
        "analysis_type": str(row[2]),
        "score": float(row[3] or 0.0),
        "rank": int(row[4] or 0),
        "evidence": parse_json(row[5]),
        "reasons": reasons,
    }
