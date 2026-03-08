# Dataset

## Source Repositories
VeriMind4 uses real open-source hardware verification artifacts from:
- `lowRISC/opentitan`
- `lowRISC/ibex`
- `chipsalliance/Cores-VeeR-EH1`
- `openhwgroup/cva6`

## Ingested Data Types
- GitHub commits and issues
- Simulation/log and trace-like files
- Regression descriptors (testlist/regression configs/scripts)
- Coverage-related files
- XML/JSON/HJSON/YAML metadata files

## Storage
Primary analytical store: DuckDB (`data/verimind4.duckdb`)

## DuckDB Schema by Stage

### Ingestion
- `commits`
- `issues`
- `artifact_files`

### Parsing
- `tests`
- `modules`
- `failures`
- `coverage`

### Context and Scoring
- `analysis_runs`
- `verification_context`
- `analysis_results`

### Features
- `feature_runs`
- `module_features`
- `test_features`
- `debug_features`

### Model Outputs
- `model_runs`
- `failure_predictions`
- `coverage_predictions`
- `debug_similarity_results`

### Explainability
- `explanations`

### Evaluation
- `evaluation_runs`
- `evaluation_metrics`

## Label Columns (Bootstrap Targets)
- `label_failure_risk`
- `label_coverage_priority`
- `label_debug_priority`

These are deterministic labels derived from real parsed/context signals and used for initial model training.

## Reproducible Command Chain
- `python -m pipeline.ingest_data --mode all --skip-clone-if-exists`
- `python -m pipeline.parse_logs --db-path data/verimind4.duckdb --raw-repos-root data/raw/repos`
- `python -m pipeline.build_context --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen`
- `python -m pipeline.feature_engineering --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen --feature-run-id feat_dataset_gen`
- `python -m pipeline.train_models --task failure --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen`
- `python -m pipeline.train_models --task coverage --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen`
- `python -m pipeline.run_analysis --task debug --db-path data/verimind4.duckdb --feature-run-id feat_dataset_gen --context-run-id run_m4_dataset_gen --encoder tfidf`
- `python -m pipeline.evaluate_metrics --db-path data/verimind4.duckdb --run-id run_m4_dataset_gen --feature-run-id feat_dataset_gen`
