# API

Base app: `backend.api.main:app`

## Start
- `uvicorn backend.api.main:app --reload`
- Swagger docs: `http://127.0.0.1:8000/docs`

## Endpoint Reference

### `GET /health`
Returns API status.

### `POST /ingest/artifacts`
Triggers ingestion pipeline module.
Request fields:
- `mode`: `metadata|clone|all`
- `max_commits`
- `max_issues`
- `repos` (optional)
- `skip_clone_if_exists`

### `POST /ingest/logs`
Uploads one or more log/artifact files and optionally runs:
- parse logs
- context build
- feature engineering
- failure model
- coverage model
- debug prioritization

Multipart form fields:
- `files`: one or many files
- `run_pipeline`: `true|false` (default `true`)
- `context_run_id` (default `run_uploaded_latest`)
- `feature_run_id` (default `feat_uploaded_latest`)
- `encoder` (default `tfidf`)
- `isolate_db`: `true|false` (default `true`, runs upload analysis on a separate `*_upload.duckdb`)

Returns:
- upload counts
- `db_path` of the analysis database
- parser summary for `uploaded` repo
- per-step pipeline status/log tails

### `GET /context/{run_id}`
Returns module-level context rows for a run.

### `POST /predict/failures`
Returns ranked failure risk items from `failure_predictions`.
Request fields:
- `model_run_id` (optional)
- `run_id` (optional)
- `top_k`

### `POST /prioritize/coverage`
Returns ranked coverage priorities from `analysis_results` (`coverage_priority`).

### `POST /prioritize/debug`
Returns ranked debug queue items from `analysis_results` (`debug_priority`).

### `POST /generate/verification-plan`
Returns merged plan actions from failure + coverage + debug rankings.
Request fields:
- `run_id`
- `top_k_each`

### `GET /explanations/{item_id}`
Returns explainability payload.
Query params:
- `run_id` (optional)
- `analysis_type` (optional)

Response includes:
- `evidence`: structured evidence object
- `reasons`: human-readable reason list

## Notes
- DuckDB path is read from `VERIMIND_DB_PATH` (default `data/verimind4.duckdb`).
- Explainability entries are cached in table `explanations`.
