# Architecture

## Overview
VeriMind4 is a layered verification intelligence platform that transforms raw verification artifacts into explainable recommendations.

Flow:
1. Data Ingestion
2. Artifact Parsing and Normalization
3. Verification Context Construction
4. Feature Engineering
5. ML Scoring and Similarity Ranking
6. Explainability Generation
7. API Serving
8. Interactive Dashboard

## Layer Details

### 1) Data Ingestion (`pipeline/ingest_data.py`)
- Pulls metadata from GitHub APIs (commits/issues).
- Scans local shallow clones for verification-relevant artifacts.
- Persists to DuckDB tables: `commits`, `issues`, `artifact_files`.

### 2) Parsing (`pipeline/parse_logs.py`)
- Converts raw artifacts to normalized records.
- Outputs: `tests`, `modules`, `failures`, `coverage`.
- Includes regression descriptor parsing, formal strategy parsing, simulation log parsing, and XML module parsing.

### 3) Context Builder (`pipeline/build_context.py`)
- Produces run-scoped context signals per module.
- Outputs:
  - `analysis_runs`
  - `verification_context`
  - `analysis_results` (`verification_context`, `run_summary`)

### 4) Feature Engineering (`pipeline/feature_engineering.py`)
- Builds ML-ready tables:
  - `module_features`
  - `test_features`
  - `debug_features`
- Adds deterministic bootstrap labels:
  - `label_failure_risk`
  - `label_coverage_priority`
  - `label_debug_priority`

### 5) ML and Ranking
- `pipeline/train_models.py`
  - Failure model (`failure_predictions`)
  - Coverage model (`coverage_predictions`)
- `pipeline/run_analysis.py`
  - Debug prioritization via similarity + severity/context blending
  - Outputs `debug_similarity_results` and `analysis_results` (`debug_priority`)

### 6) Explainability (`backend/services/explainability.py`)
- Builds explanation records from `analysis_results`.
- Stores cached explanations in `explanations`.
- Returns structured evidence + human-readable reasons.

### 7) API Layer (`backend/api/main.py`)
- Exposes ingestion, context retrieval, predictions, prioritization, plan generation, and explanation endpoints.

### 8) Dashboard (`frontend/dashboard/app.py`)
- Four required panels:
  - Failure Risk Heatmap
  - Verification Plan Suggestions
  - Coverage Priorities
  - Debug Queue
- Supports pipeline trigger, explanation inspection, and CSV export.

## Design Principles
- Run-scoped analytics (`run_id`, `feature_run_id`, `model_run_id`, `eval_run_id`)
- Deterministic and reproducible transformations
- Evidence-first outputs for judge-facing transparency
- Local-resource-friendly execution for laptop-class hardware
