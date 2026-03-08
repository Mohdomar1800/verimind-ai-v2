from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st

try:
    from frontend.dashboard.data_access import (
        connect_db,
        context_df,
        explanations_df,
        latest_context_run_id,
        parse_evidence_column,
        rankings_df,
    )
except ModuleNotFoundError:
    # Support direct "streamlit run frontend/dashboard/app.py"
    from data_access import (  # type: ignore
        connect_db,
        context_df,
        explanations_df,
        latest_context_run_id,
        parse_evidence_column,
        rankings_df,
    )


st.set_page_config(page_title="VeriMind4 Dashboard", layout="wide")
st.title("VeriMind4 Intelligence Console")
st.caption("Context-Aware RTL Verification Intelligence for RTL Verification Teams")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 1.5rem;}
    .vm-card {
        background: linear-gradient(140deg, #f7fbff 0%, #eef8f1 100%);
        border: 1px solid #d6e4f0;
        border-radius: 14px;
        padding: 0.8rem 1rem;
    }
    .vm-title {font-weight: 700; font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def run_module(module_name: str, args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", module_name, *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    tail = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[-4000:]
    return result.returncode == 0, tail


def save_uploaded_files(uploaded_files: list[Any], upload_root: Path) -> list[Path]:
    upload_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = upload_root / f"batch_{ts}"
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for file in uploaded_files:
        out = target_dir / file.name
        out.write_bytes(file.getbuffer())
        paths.append(out)
    return paths


def risk_pie_chart(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No context rows available.")
        return
    dist = df.groupby("risk_level", dropna=False).size().reset_index(name="count")
    st.vega_lite_chart(
        dist,
        {
            "mark": {"type": "arc", "innerRadius": 45},
            "encoding": {
                "theta": {"field": "count", "type": "quantitative"},
                "color": {"field": "risk_level", "type": "nominal"},
                "tooltip": [
                    {"field": "risk_level", "type": "nominal"},
                    {"field": "count", "type": "quantitative"},
                ],
            },
            "height": 320,
        },
        use_container_width=True,
    )


def heatmap_frame(df: pd.DataFrame, top_n: int = 25) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    top = df.sort_values("context_score", ascending=False).head(top_n).copy()
    top["module"] = top["module_name"].astype(str).str.slice(0, 28)
    pivot = top.pivot_table(index="repo", columns="module", values="context_score", aggfunc="max")
    return pivot.fillna(0.0)


def contribution_df(row: pd.Series) -> pd.DataFrame:
    failure_rate = float(row.get("failure_rate", 0.0) or 0.0)
    coverage_gap_raw = row.get("coverage_gap")
    coverage_gap = 50.0 if pd.isna(coverage_gap_raw) else float(coverage_gap_raw or 0.0)
    open_issues = int(row.get("open_issues", 0) or 0)
    recent_commits = int(row.get("recent_commits", 0) or 0)
    artifact_files = int(row.get("artifact_files", 0) or 0)

    parts = [
        ("failure_component", round(failure_rate * 0.45, 4)),
        ("coverage_component", round(coverage_gap * 0.25, 4)),
        ("issue_component", round(min(open_issues * 8.0, 20.0), 4)),
        ("commit_component", round(min(recent_commits * 4.0, 20.0), 4)),
        ("artifact_component", round(min(artifact_files * 1.0, 10.0), 4)),
    ]
    out = pd.DataFrame(parts, columns=["component", "value"])
    out["percent_of_total"] = (out["value"] / max(out["value"].sum(), 1e-9) * 100.0).round(2)
    return out


def ranking_repo_dist(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "item_id" not in df.columns:
        return pd.DataFrame(columns=["repo", "count"])
    out = df.copy()
    out["repo"] = out["item_id"].astype(str).str.split(":", n=1).str[0]
    return out.groupby("repo", dropna=False).size().reset_index(name="count").sort_values("count", ascending=False)


def call_api_upload(
    api_base_url: str,
    files: list[Any],
    context_run_id: str,
    feature_run_id: str,
    run_pipeline: bool,
) -> tuple[bool, dict[str, Any] | str]:
    endpoint = api_base_url.rstrip("/") + "/ingest/logs"
    payload_files = [("files", (f.name, f.getvalue(), "application/octet-stream")) for f in files]
    data = {
        "run_pipeline": str(run_pipeline).lower(),
        "context_run_id": context_run_id,
        "feature_run_id": feature_run_id,
        "encoder": "tfidf",
        "isolate_db": "true",
    }
    try:
        resp = requests.post(endpoint, files=payload_files, data=data, timeout=1800)
        if resp.status_code >= 400:
            return False, f"{resp.status_code}: {resp.text[:1500]}"
        return True, resp.json()
    except Exception as exc:
        return False, str(exc)


default_db = os.getenv("VERIMIND_DB_PATH", "data/verimind4.duckdb")
if "db_path" not in st.session_state:
    st.session_state["db_path"] = default_db
if "context_run_id" not in st.session_state:
    st.session_state["context_run_id"] = "run_m4_dataset_gen"
if "feature_run_id" not in st.session_state:
    st.session_state["feature_run_id"] = "feat_dataset_gen"
if "api_base_url" not in st.session_state:
    st.session_state["api_base_url"] = "http://127.0.0.1:8000"
if "next_db_path" in st.session_state:
    st.session_state["db_path"] = st.session_state.pop("next_db_path")
if "next_context_run_id" in st.session_state:
    st.session_state["context_run_id"] = st.session_state.pop("next_context_run_id")

with st.sidebar:
    st.header("Controls")
    db_path = st.text_input("DuckDB Path", key="db_path")
    context_run_id_input = st.text_input("Context Run ID", key="context_run_id")
    feature_run_id = st.text_input("Feature Run ID", key="feature_run_id")
    api_base_url = st.text_input("API Base URL", key="api_base_url")

    st.markdown("### Artifact Upload")
    uploaded_files = st.file_uploader(
        "Upload logs/artifacts (supports multi-file)",
        accept_multiple_files=True,
        type=["log", "txt", "json", "xml", "yaml", "yml", "hjson", "rpt", "cfg"],
    )
    run_pipeline_on_upload = st.checkbox("Run full pipeline after API upload", value=True)
    if st.button("Save Uploaded Artifacts", type="secondary"):
        if not uploaded_files:
            st.warning("No files selected.")
        else:
            saved = save_uploaded_files(uploaded_files, Path("data/raw/uploads"))
            st.success(f"Saved {len(saved)} files.")
            st.code("\n".join(str(p) for p in saved[:10]))

    if st.button("Upload To API And Process", type="primary"):
        if not uploaded_files:
            st.warning("No files selected.")
        else:
            with st.spinner("Uploading files and running pipeline..."):
                ok, payload = call_api_upload(
                    api_base_url=api_base_url,
                    files=uploaded_files,
                    context_run_id=context_run_id_input.strip() or "run_uploaded_latest",
                    feature_run_id=feature_run_id,
                    run_pipeline=run_pipeline_on_upload,
                )
            if not ok:
                st.error(f"Upload/process failed: {payload}")
            else:
                st.success("Upload + processing completed.")
                st.json(payload)
                if isinstance(payload, dict):
                    if payload.get("db_path"):
                        st.session_state["next_db_path"] = str(payload["db_path"])
                    if payload.get("context_run_id"):
                        st.session_state["next_context_run_id"] = str(payload["context_run_id"])
                st.rerun()

    st.markdown("### Analysis Trigger")
    if st.button("Run Parse -> Context -> Features -> Models -> Debug", type="secondary"):
        progress = st.empty()
        logs = st.empty()
        run_id_local = context_run_id_input.strip() or "run_m4_dashboard"
        steps = [
            ("pipeline.parse_logs", ["--db-path", db_path, "--raw-repos-root", "data/raw/repos"]),
            ("pipeline.build_context", ["--db-path", db_path, "--run-id", run_id_local]),
            (
                "pipeline.feature_engineering",
                ["--db-path", db_path, "--run-id", run_id_local, "--feature-run-id", feature_run_id],
            ),
            ("pipeline.train_models", ["--task", "failure", "--db-path", db_path, "--feature-run-id", feature_run_id]),
            ("pipeline.train_models", ["--task", "coverage", "--db-path", db_path, "--feature-run-id", feature_run_id]),
            (
                "pipeline.run_analysis",
                [
                    "--task",
                    "debug",
                    "--db-path",
                    db_path,
                    "--feature-run-id",
                    feature_run_id,
                    "--context-run-id",
                    run_id_local,
                    "--encoder",
                    "tfidf",
                ],
            ),
        ]
        ok_all = True
        all_logs: list[str] = []
        for idx, (mod, args) in enumerate(steps, start=1):
            progress.info(f"Running {idx}/{len(steps)}: {mod}")
            ok, tail = run_module(mod, args)
            all_logs.append(f"## {mod}\n{tail}")
            if not ok:
                ok_all = False
                break
        logs.code("\n\n".join(all_logs))
        if ok_all:
            progress.success("Analysis pipeline completed.")
        else:
            progress.error("Pipeline failed. Check logs above.")


if not Path(db_path).exists():
    st.error(f"DuckDB not found at: {db_path}")
    st.stop()

with connect_db(db_path) as conn:
    run_id = context_run_id_input.strip() or latest_context_run_id(conn)
    if not run_id:
        st.error("No context run found. Run context builder first.")
        st.stop()

    ctx = parse_evidence_column(context_df(conn, run_id))
    failure_rank = parse_evidence_column(rankings_df(conn, run_id, "failure_prediction", limit=200))
    coverage_rank = parse_evidence_column(rankings_df(conn, run_id, "coverage_priority", limit=200))
    debug_rank = parse_evidence_column(rankings_df(conn, run_id, "debug_priority", limit=500))

st.subheader(f"Run ID: {run_id}")
st.markdown('<div class="vm-card"><span class="vm-title">Executive Summary</span></div>', unsafe_allow_html=True)

summary_cols = st.columns(4)
summary_cols[0].metric("Modules", len(ctx))
summary_cols[1].metric("High-Risk Modules", int((ctx["risk_level"] == "high").sum()) if not ctx.empty else 0)
summary_cols[2].metric("Failure Priority Items", len(failure_rank))
summary_cols[3].metric("Debug Queue Size", len(debug_rank))

viz1, viz2 = st.columns(2)
with viz1:
    st.markdown("### Risk Distribution (Pie)")
    risk_pie_chart(ctx)
with viz2:
    st.markdown("### Top Context Scores (Bar)")
    if not ctx.empty:
        top = ctx.sort_values("context_score", ascending=False).head(20).copy()
        top["module"] = top["repo"] + ":" + top["module_name"]
        st.bar_chart(top.set_index("module")["context_score"])
    else:
        st.info("No context rows available.")

st.markdown("### Failure Risk Heatmap (Repo x Module)")
hm = heatmap_frame(ctx, top_n=30)
if hm.empty:
    st.info("No heatmap data.")
else:
    heat_rows = hm.reset_index().melt(id_vars="repo", var_name="module", value_name="context_score")
    st.vega_lite_chart(
        heat_rows,
        {
            "mark": "rect",
            "encoding": {
                "x": {"field": "module", "type": "nominal", "sort": None},
                "y": {"field": "repo", "type": "nominal"},
                "color": {"field": "context_score", "type": "quantitative", "scale": {"scheme": "yelloworangered"}},
                "tooltip": [
                    {"field": "repo", "type": "nominal"},
                    {"field": "module", "type": "nominal"},
                    {"field": "context_score", "type": "quantitative"},
                ],
            },
            "height": 340,
        },
        use_container_width=True,
    )

panel3, panel4 = st.columns(2)
with panel3:
    st.markdown("### Coverage Priorities (Table + Repo Distribution)")
    cov = coverage_rank[["item_id", "score", "rank"]].copy() if not coverage_rank.empty else pd.DataFrame()
    if not cov.empty:
        cdist = ranking_repo_dist(cov)
        if not cdist.empty:
            st.bar_chart(cdist.set_index("repo")["count"])
        st.dataframe(cov.head(200), use_container_width=True)
        st.download_button(
            "Export Coverage Priorities CSV",
            cov.to_csv(index=False).encode("utf-8"),
            file_name=f"coverage_priority_{run_id}.csv",
            mime="text/csv",
        )
    else:
        st.info("No coverage priorities found.")

with panel4:
    st.markdown("### Debug Queue (Table + Repo Distribution)")
    dbg = debug_rank[["item_id", "score", "rank"]].copy() if not debug_rank.empty else pd.DataFrame()
    if not dbg.empty:
        ddist = ranking_repo_dist(dbg)
        if not ddist.empty:
            st.bar_chart(ddist.set_index("repo")["count"])
        st.dataframe(dbg.head(500), use_container_width=True)
        st.download_button(
            "Export Debug Queue CSV",
            dbg.to_csv(index=False).encode("utf-8"),
            file_name=f"debug_queue_{run_id}.csv",
            mime="text/csv",
        )
    else:
        st.info("No debug priorities found.")

st.markdown("### Verification Plan Suggestions")
plan_rows: list[dict[str, object]] = []
for _, row in failure_rank.head(10).iterrows():
    plan_rows.append({"priority_type": "failure", "item_id": row["item_id"], "score": row["score"]})
for _, row in coverage_rank.head(10).iterrows():
    plan_rows.append({"priority_type": "coverage", "item_id": row["item_id"], "score": row["score"]})
for _, row in debug_rank.head(10).iterrows():
    plan_rows.append({"priority_type": "debug", "item_id": row["item_id"], "score": row["score"]})
plan_df = pd.DataFrame(plan_rows).sort_values("score", ascending=False) if plan_rows else pd.DataFrame()
if not plan_df.empty:
    st.dataframe(plan_df, use_container_width=True)
    st.download_button(
        "Export Verification Plan CSV",
        plan_df.to_csv(index=False).encode("utf-8"),
        file_name=f"verification_plan_{run_id}.csv",
        mime="text/csv",
    )
else:
    st.info("No plan items available.")

st.markdown("### Context Engine Proof (Why This Is Working)")
if ctx.empty:
    st.info("No context rows available for proof view.")
else:
    ctx_picker = (ctx["repo"] + ":" + ctx["module_name"]).tolist()
    selected_module = st.selectbox("Select module to inspect context score decomposition", options=ctx_picker)
    sel_repo, sel_module = selected_module.split(":", 1)
    selected_row = ctx[(ctx["repo"] == sel_repo) & (ctx["module_name"] == sel_module)].iloc[0]
    contrib = contribution_df(selected_row)
    reconstructed_score = float(contrib["value"].sum())
    shown_score = float(selected_row.get("context_score", 0.0) or 0.0)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.bar_chart(contrib.set_index("component")["value"])
        st.dataframe(contrib, use_container_width=True)
    with c2:
        st.metric("Context Score (Stored)", f"{shown_score:.2f}")
        st.metric("Context Score (Reconstructed)", f"{reconstructed_score:.2f}")
        st.metric("Failure Rate", f"{float(selected_row.get('failure_rate', 0.0) or 0.0):.2f}%")
        st.metric("Coverage Gap", f"{float(selected_row.get('coverage_gap', 0.0) or 0.0):.2f}%")
        st.write(
            "This module score is computed from deterministic components: failure rate, coverage gap, open issues, recent commits, and artifact footprint."
        )

st.markdown("### Explanations")
all_items = []
for frame in [failure_rank, coverage_rank, debug_rank]:
    if not frame.empty:
        all_items.extend(frame["item_id"].astype(str).tolist())
all_items = sorted(set(all_items))
if all_items:
    item = st.selectbox("Select item to inspect evidence", options=all_items)
    with connect_db(db_path) as conn:
        ex = parse_evidence_column(explanations_df(conn, item))
    if ex.empty:
        st.info("No explanation found.")
    else:
        row = ex.iloc[0].to_dict()
        st.json(
            {
                "item_id": row.get("item_id"),
                "run_id": row.get("run_id"),
                "analysis_type": row.get("analysis_type"),
                "score": row.get("score"),
                "rank": row.get("rank"),
                "evidence": row.get("evidence"),
            }
        )
else:
    st.info("No ranked items available for explanation.")
