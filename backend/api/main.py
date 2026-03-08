from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

from backend.api.schemas import (
    ContextItem,
    ContextResponse,
    ExplanationResponse,
    IngestArtifactsRequest,
    PipelineRunResponse,
    PredictionRequest,
    RankingItem,
    RankingResponse,
    UploadLogsResponse,
    VerificationPlanRequest,
    VerificationPlanResponse,
)
from backend.services.db import connect, latest_model_run_id, latest_run_id, parse_json
from backend.services.explainability import build_explanations_for_run, get_explanation


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="VeriMind4 API", version="0.9.0")

# Allow CORS for frontend (React on localhost:3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or restrict to ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    return Path(os.getenv("VERIMIND_DB_PATH", "data/verimind4.duckdb"))


def _classify_artifact(path: Path) -> str:
    lower = str(path).lower()
    name = path.name.lower()
    if any(token in lower for token in ["coverage", "cov", "cov_report", "/cov/", "_cov", "fcov"]):
        return "coverage"
    if any(token in lower for token in ["regress", "regression", "testlist", "results", "junit", "dvsim"]):
        return "regression"
    if any(token in lower for token in ["verif", "dv/", "/dv/", "formal", "strategy"]):
        return "metadata"
    if name.endswith(".log") or "sim" in lower or "trace" in lower:
        return "simulation_log"
    if name.endswith((".xml", ".json", ".hjson", ".yaml", ".yml")):
        return "metadata"
    return "other"


def _upsert_uploaded_artifacts(rows: list[tuple[Any, ...]], target_db_path: Path) -> int:
    if not rows:
        return 0
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(target_db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_files (
                repo TEXT,
                source TEXT,
                category TEXT,
                relative_path TEXT,
                size_bytes BIGINT,
                ingested_at TIMESTAMP,
                PRIMARY KEY (repo, source, relative_path)
            )
            """
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO artifact_files
            (repo, source, category, relative_path, size_bytes, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _run_module(module_name: str, args: list[str]) -> PipelineRunResponse:
    cmd = [sys.executable, "-m", module_name, *args]
    result = subprocess.run(cmd, cwd=_repo_root(), capture_output=True, text=True)
    return PipelineRunResponse(
        ok=result.returncode == 0,
        exit_code=result.returncode,
        command=cmd,
        stdout_tail=(result.stdout or "")[-4000:],
        stderr_tail=(result.stderr or "")[-4000:],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "verimind4-backend"}


@app.post("/ingest/artifacts", response_model=PipelineRunResponse)
def ingest_artifacts(payload: IngestArtifactsRequest) -> PipelineRunResponse:
    args = [
        "--mode",
        payload.mode,
        "--max-commits",
        str(payload.max_commits),
        "--max-issues",
        str(payload.max_issues),
    ]
    if payload.skip_clone_if_exists:
        args.append("--skip-clone-if-exists")
    if payload.repos:
        args.extend(["--repos", *payload.repos])

    result = _run_module("pipeline.ingest_data", args)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.model_dump())
    return result


@app.post("/ingest/logs", response_model=UploadLogsResponse)
async def ingest_logs(
    files: list[UploadFile] = File(...),
    run_pipeline: bool = Form(default=True),
    context_run_id: str = Form(default=""),  # ignored — generated server-side
    feature_run_id: str = Form(default=""),  # ignored — generated server-side
    encoder: str = Form(default="tfidf"),
    isolate_db: bool = Form(default=True),  # kept for compat but always isolated
) -> UploadLogsResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    now = dt.datetime.now(dt.timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")

    # Always generate run IDs server-side from UTC timestamp — avoids client/server timezone mismatch
    context_run_id = f"run_upload_{ts}"
    feature_run_id = f"feat_upload_{ts}"

    upload_repo = "uploaded"
    repo_root = _repo_root() / "data" / "raw" / "repos" / upload_repo / "uploads" / ts
    repo_root.mkdir(parents=True, exist_ok=True)

    ingest_rows: list[tuple[Any, ...]] = []
    for uploaded in files:
        filename = Path(uploaded.filename or "unnamed.log").name
        target = repo_root / filename
        payload = await uploaded.read()
        target.write_bytes(payload)
        rel = target.relative_to(_repo_root() / "data" / "raw" / "repos" / upload_repo).as_posix()
        ingest_rows.append(
            (
                upload_repo,
                "api_upload",
                _classify_artifact(Path(rel)),
                rel,
                len(payload),
                now.isoformat(),
            )
        )

    main_db_path = _db_path()

    # Always use an isolated DB so the pipeline only sees the uploaded files
    upload_db_path = main_db_path.with_name(f"{main_db_path.stem}_upload_{ts}.duckdb")
    if main_db_path.exists():
        shutil.copy2(main_db_path, upload_db_path)

    # Register only this upload's artifacts; remove all other repos
    _upsert_uploaded_artifacts(ingest_rows, upload_db_path)
    with duckdb.connect(str(upload_db_path)) as conn:
        conn.execute("DELETE FROM artifact_files WHERE repo <> ?", [upload_repo])

    upserted = len(ingest_rows)
    pipeline_steps: list[dict[str, Any]] = []
    pipeline_ok = True

    if run_pipeline:
        steps = [
            ("pipeline.parse_logs", ["--db-path", str(upload_db_path), "--raw-repos-root", "data/raw/repos"]),
            ("pipeline.build_context", ["--db-path", str(upload_db_path), "--run-id", context_run_id]),
            (
                "pipeline.feature_engineering",
                ["--db-path", str(upload_db_path), "--run-id", context_run_id, "--feature-run-id", feature_run_id],
            ),
            ("pipeline.train_models", ["--task", "failure", "--db-path", str(upload_db_path), "--feature-run-id", feature_run_id]),
            ("pipeline.train_models", ["--task", "coverage", "--db-path", str(upload_db_path), "--feature-run-id", feature_run_id]),
            (
                "pipeline.run_analysis",
                [
                    "--task", "debug",
                    "--db-path", str(upload_db_path),
                    "--feature-run-id", feature_run_id,
                    "--context-run-id", context_run_id,
                    "--encoder", encoder,
                ],
            ),
        ]
        for module_name, args in steps:
            result = _run_module(module_name, args)
            pipeline_steps.append(
                {
                    "module": module_name,
                    "ok": result.ok,
                    "exit_code": result.exit_code,
                    "stdout_tail": result.stdout_tail,
                    "stderr_tail": result.stderr_tail,
                }
            )
            if not result.ok:
                pipeline_ok = False
                break

        if pipeline_ok:
            # Merge this run's results back into the main DB so the API can serve them
            try:
                with duckdb.connect(str(main_db_path)) as main_conn:
                    # Remove any existing rows for this run_id in the main DB
                    main_conn.execute("DELETE FROM verification_context WHERE run_id = ?", [context_run_id])
                    main_conn.execute("DELETE FROM analysis_results WHERE run_id = ?", [context_run_id])
                    main_conn.execute("DELETE FROM analysis_runs WHERE run_id = ?", [context_run_id])

                    # Copy run results from upload DB into main DB
                    main_conn.execute(f"ATTACH '{upload_db_path}' AS upload_db")
                    main_conn.execute(
                        "INSERT INTO analysis_runs SELECT * FROM upload_db.analysis_runs WHERE run_id = ?",
                        [context_run_id],
                    )
                    main_conn.execute(
                        "INSERT INTO verification_context SELECT * FROM upload_db.verification_context WHERE run_id = ?",
                        [context_run_id],
                    )
                    main_conn.execute(
                        "INSERT INTO analysis_results SELECT * FROM upload_db.analysis_results WHERE run_id = ?",
                        [context_run_id],
                    )
                    main_conn.execute("DETACH upload_db")
            except Exception as merge_err:
                pipeline_steps.append({"module": "merge_results", "ok": False, "exit_code": -1, "stdout_tail": "", "stderr_tail": str(merge_err)})

    with duckdb.connect(str(upload_db_path)) as conn:
        def safe_count(table: str) -> int:
            try:
                return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE repo = ?", [upload_repo]).fetchone()[0])
            except duckdb.Error:
                return 0

        summary = {
            "tests": safe_count("tests"),
            "failures": safe_count("failures"),
            "modules": safe_count("modules"),
            "coverage": safe_count("coverage"),
        }

    # Clean up the temporary upload DB
    try:
        upload_db_path.unlink(missing_ok=True)
    except Exception:
        pass

    return UploadLogsResponse(
        ok=pipeline_ok,
        uploaded_files=len(files),
        artifact_rows_upserted=upserted,
        repo=upload_repo,
        db_path=str(main_db_path),
        context_run_id=context_run_id if run_pipeline else None,
        feature_run_id=feature_run_id if run_pipeline else None,
        parser_summary=summary,
        pipeline_steps=pipeline_steps,
    )



@app.get("/context/{run_id}", response_model=ContextResponse)
def get_context(run_id: str) -> ContextResponse:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT repo, module_name, context_score, risk_level,
                   tests_total, failures_total, failure_rate, coverage_gap,
                   evidence_json
            FROM verification_context
            WHERE run_id = ?
            ORDER BY context_score DESC
            """,
            [run_id],
        ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No context rows found for run_id={run_id}")

    items = [
        ContextItem(
            repo=str(r[0]),
            module_name=str(r[1]),
            context_score=float(r[2] or 0.0),
            risk_level=str(r[3] or "unknown"),
            tests_total=int(r[4] or 0),
            failures_total=int(r[5] or 0),
            failure_rate=float(r[6] or 0.0),
            coverage_gap=float(r[7]) if r[7] is not None else None,
            evidence=parse_json(r[8]),
        )
        for r in rows
    ]
    return ContextResponse(run_id=run_id, count=len(items), items=items)


@app.post("/predict/failures", response_model=RankingResponse)
def predict_failures(payload: PredictionRequest) -> RankingResponse:
    with connect() as conn:
        model_run_id = payload.model_run_id or latest_model_run_id(conn, "failure")
        if not model_run_id:
            raise HTTPException(status_code=404, detail="No failure model run found")

        run_id = payload.run_id or latest_run_id(conn)
        if not run_id:
            raise HTTPException(status_code=404, detail="No context run found")

        rows = conn.execute(
            """
            SELECT repo, module_name, pred_prob, split, evidence_json
            FROM failure_predictions
            WHERE model_run_id = ?
            ORDER BY pred_prob DESC
            LIMIT ?
            """,
            [model_run_id, payload.top_k],
        ).fetchall()

    items = [
        RankingItem(
            item_id=f"{r[0]}:{r[1]}",
            score=float(r[2] or 0.0),
            rank=i + 1,
            evidence={**parse_json(r[4]), "split": r[3], "model_run_id": model_run_id},
        )
        for i, r in enumerate(rows)
    ]
    return RankingResponse(run_id=run_id, analysis_type="failure_prediction", count=len(items), items=items)


@app.post("/prioritize/coverage", response_model=RankingResponse)
def prioritize_coverage(payload: PredictionRequest) -> RankingResponse:
    with connect() as conn:
        run_id = payload.run_id or latest_run_id(conn)
        if not run_id:
            raise HTTPException(status_code=404, detail="No context run found")

        rows = conn.execute(
            """
            SELECT item_id, score, rank, evidence_json
            FROM analysis_results
            WHERE run_id = ? AND analysis_type = 'coverage_priority'
            ORDER BY rank ASC
            LIMIT ?
            """,
            [run_id, payload.top_k],
        ).fetchall()

    items = [
        RankingItem(item_id=str(r[0]), score=float(r[1] or 0.0), rank=int(r[2]), evidence=parse_json(r[3]))
        for r in rows
    ]
    return RankingResponse(run_id=run_id, analysis_type="coverage_priority", count=len(items), items=items)


@app.post("/prioritize/debug", response_model=RankingResponse)
def prioritize_debug(payload: PredictionRequest) -> RankingResponse:
    with connect() as conn:
        run_id = payload.run_id or latest_run_id(conn)
        if not run_id:
            raise HTTPException(status_code=404, detail="No context run found")

        rows = conn.execute(
            """
            SELECT item_id, score, rank, evidence_json
            FROM analysis_results
            WHERE run_id = ? AND analysis_type = 'debug_priority'
            ORDER BY rank ASC
            LIMIT ?
            """,
            [run_id, payload.top_k],
        ).fetchall()

    items = [
        RankingItem(item_id=str(r[0]), score=float(r[1] or 0.0), rank=int(r[2]), evidence=parse_json(r[3]))
        for r in rows
    ]
    return RankingResponse(run_id=run_id, analysis_type="debug_priority", count=len(items), items=items)


@app.post("/generate/verification-plan", response_model=VerificationPlanResponse)
def generate_verification_plan(payload: VerificationPlanRequest) -> VerificationPlanResponse:
    run_id = payload.run_id

    with connect() as conn:
        failure_rows = conn.execute(
            """
            SELECT item_id, score
            FROM analysis_results
            WHERE run_id = ? AND analysis_type = 'failure_prediction'
            ORDER BY rank ASC
            LIMIT ?
            """,
            [run_id, payload.top_k_each],
        ).fetchall()

        coverage_rows = conn.execute(
            """
            SELECT item_id, score
            FROM analysis_results
            WHERE run_id = ? AND analysis_type = 'coverage_priority'
            ORDER BY rank ASC
            LIMIT ?
            """,
            [run_id, payload.top_k_each],
        ).fetchall()

        debug_rows = conn.execute(
            """
            SELECT item_id, score
            FROM analysis_results
            WHERE run_id = ? AND analysis_type = 'debug_priority'
            ORDER BY rank ASC
            LIMIT ?
            """,
            [run_id, payload.top_k_each],
        ).fetchall()

    plan_items: list[dict[str, object]] = []
    for item_id, score in failure_rows:
        plan_items.append(
            {
                "priority_type": "failure",
                "item_id": item_id,
                "score": float(score),
                "action": "Increase directed tests and triage failures",
            }
        )
    for item_id, score in coverage_rows:
        plan_items.append(
            {
                "priority_type": "coverage",
                "item_id": item_id,
                "score": float(score),
                "action": "Add coverage-focused tests and close gaps",
            }
        )
    for item_id, score in debug_rows:
        plan_items.append(
            {
                "priority_type": "debug",
                "item_id": item_id,
                "score": float(score),
                "action": "Schedule immediate debug triage",
            }
        )

    plan_items.sort(key=lambda x: float(x["score"]), reverse=True)
    summary = {
        "run_id": run_id,
        "failure_items": len(failure_rows),
        "coverage_items": len(coverage_rows),
        "debug_items": len(debug_rows),
        "total_plan_items": len(plan_items),
    }
    return VerificationPlanResponse(run_id=run_id, summary=summary, plan_items=plan_items)


@app.get("/explanations/{item_id}", response_model=ExplanationResponse)
def get_explanations(
    item_id: str,
    run_id: str | None = Query(default=None),
    analysis_type: str | None = Query(default=None),
) -> ExplanationResponse:
    with connect() as conn:
        resolved_run = run_id or latest_run_id(conn)
        if resolved_run:
            build_explanations_for_run(conn, resolved_run)
        ex = get_explanation(conn, item_id=item_id, run_id=run_id, analysis_type=analysis_type)

    if not ex:
        raise HTTPException(status_code=404, detail=f"No explanation found for item_id={item_id}")

    return ExplanationResponse(
        item_id=str(ex["item_id"]),
        run_id=str(ex["run_id"]),
        analysis_type=str(ex["analysis_type"]),
        score=float(ex["score"]),
        rank=int(ex["rank"]),
        evidence=parse_json(ex["evidence"]),
        reasons=[str(r) for r in ex.get("reasons", [])],
    )
