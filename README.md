# VeriMind4
Context-Aware RTL Verification Intelligence Platform

VeriMind4 converts real RTL verification artifacts into an AI-assisted verification intelligence workflow for four tasks:
- Failure Risk Prediction
- Verification Plan Generation
- Coverage Prioritization
- Debug Queue Prioritization

This repository is structured for local execution on a Windows laptop (Anaconda environment) and is demo-ready for hackathon judging.

## Submission Snapshot
- Data source style: real open-source verification artifacts
- Storage: DuckDB
- Backend: FastAPI
- Frontend: Streamlit
- ML: LightGBM / RandomForest + similarity ranking
- Explainability: evidence + reason generation per recommendation

## Core Data Sources
Primary repositories ingested:
- `lowRISC/opentitan`
- `lowRISC/ibex`
- `chipsalliance/Cores-VeeR-EH1`
- `openhwgroup/cva6`

Artifacts and metadata extracted include:
- commits / issues
- simulation and regression-related files
- test descriptors and logs
- coverage-related artifacts

## Implemented Architecture
Data Ingestion -> Parsing -> Context Builder -> Feature Engineering -> ML Models -> Explainability -> API -> Dashboard

Detailed architecture: `docs/architecture.md`

## Repository Layout
- `backend/` FastAPI app + services
- `frontend/dashboard/` Streamlit app
- `pipeline/` reproducible data + ML pipeline stages
- `ml/` model package placeholder
- `data/` DuckDB and pipeline outputs
- `notebooks/` evaluation notebook
- `tests/` automated checks
- `docs/` architecture, dataset, API, demo script

## Local Setup (Windows + Anaconda)
1. Create environment:
   - `conda create -n verimind4 python=3.11 -y`
2. Activate:
   - `conda activate verimind4`
3. Install dependencies:
   - `pip install -r requirements.txt`

## End-to-End Pipeline Commands
1. Ingest:
- `python -m pipeline.ingest_data --mode all --skip-clone-if-exists`

2. Parse:
- `python -m pipeline.parse_logs --db-path data/verimind4.duckdb --raw-repos-root data/raw/repos`

3. Build context:
- `python -m pipeline.build_context --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen`

4. Build features:
- `python -m pipeline.feature_engineering --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen --feature-run-id feat_dataset_gen`

5. Train failure model:
- `python -m pipeline.train_models --task failure --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen`

6. Train coverage model:
- `python -m pipeline.train_models --task coverage --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen`

7. Run debug prioritization:
- `python -m pipeline.run_analysis --task debug --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen --context-run-id run_m4_dataset_gen --encoder tfidf`

8. Evaluate baseline vs AI-assisted metrics:
- `python -m pipeline.evaluate_metrics --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen --feature-run-id feat_dataset_gen`

## Run API and Dashboard
Backend:
- `uvicorn backend.api.main:app --reload`
- Docs: `http://127.0.0.1:8000/docs`

Dashboard:
- `streamlit run frontend/dashboard/app.py`
- UI: `http://127.0.0.1:8501`

## API Endpoints
- `POST /ingest/artifacts`
- `GET /context/{run_id}`
- `POST /predict/failures`
- `POST /generate/verification-plan`
- `POST /prioritize/coverage`
- `POST /prioritize/debug`
- `GET /explanations/{item_id}`

See `docs/api.md` for usage details.

## Evaluation Artifacts
- CLI output CSV: `data/processed/evaluation_summary.csv`
- Notebook: `notebooks/evaluation_metrics.ipynb`
- DuckDB tables: `evaluation_runs`, `evaluation_metrics`

## Testing
Run test suite:
- `python -m pytest -q`

## Demo Flow
See `docs/demo_script.md`.

## Current Status
Milestones 1-12 completed.
Milestone 13 Docker was intentionally rolled back for a non-Docker local workflow.
Milestone 14 documentation polish completed.
