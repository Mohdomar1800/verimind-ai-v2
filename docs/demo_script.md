# Demo Script

This script is designed for a 5-8 minute judge demo.

## 1) Launch Services
- API: `uvicorn backend.api.main:app --reload`
- Dashboard: `streamlit run frontend/dashboard/app.py`

## 2) Open Interfaces
- API docs: `http://127.0.0.1:8000/docs`
- Dashboard: `http://127.0.0.1:8501`

## 3) Trigger Analysis
Option A (best demo flow):
- In dashboard sidebar, upload sample logs.
- Click **Upload To API And Process**.
- Show returned parser counts and pipeline step results.

Option B:
- Use local button **Run Parse -> Context -> Features -> Models -> Debug**.

## 4) Show the Four Core Panels
- Failure Risk Heatmap
- Verification Plan Suggestions
- Coverage Priorities
- Debug Queue

## 5) Prove Context Engine Works
- Open **Context Engine Proof** panel.
- Select a module and show:
  - stored context score
  - reconstructed score from deterministic components
  - component contributions (failure, coverage, issues, commits, artifacts)
- Explain that this gives transparent, auditable ranking logic.

## 6) Show Explainability
- In dashboard: pick an item under **Explanations**.
- In API: call `GET /explanations/{item_id}` and show `reasons` + `evidence`.

## 7) Show Evaluation Evidence
- Run: `python -m pipeline.evaluate_metrics --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen --feature-run-id feat_dataset_gen`
- Open `data/processed/evaluation_summary.csv`.
- Highlight baseline vs AI-assisted differences.

## 8) Export Outputs
Use dashboard export buttons for:
- failure risk table
- verification plan
- coverage priorities
- debug queue

## 9) Closing Statement
Summarize that VeriMind4 unifies ingestion, context modeling, ML prioritization, explainability, API serving, and UI for practical verification intelligence.
