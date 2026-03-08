#!/usr/bin/env bash
set -euo pipefail

python -m pipeline.ingest_data
python -m pipeline.parse_logs
python -m pipeline.build_context
python -m pipeline.feature_engineering
python -m pipeline.train_models
python -m pipeline.run_analysis
