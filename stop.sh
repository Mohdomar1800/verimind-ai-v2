#!/usr/bin/env bash
# Stop VeriMind on macOS or Linux. Your runs and settings are kept; ./start.sh brings everything back.
set -euo pipefail
cd "$(dirname "$0")"
docker compose down && echo "VeriMind stopped. Your data is kept."
