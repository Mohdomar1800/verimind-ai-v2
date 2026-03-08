#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-metadata}"

if [[ "$MODE" != "metadata" && "$MODE" != "clone" && "$MODE" != "all" ]]; then
  echo "Usage: scripts/download_data.sh [metadata|clone|all]"
  exit 1
fi

python -m pipeline.ingest_data --mode "$MODE" --skip-clone-if-exists
