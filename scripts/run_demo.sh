#!/usr/bin/env bash
set -euo pipefail

uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 &
streamlit run frontend/dashboard/app.py --server.port 8501
