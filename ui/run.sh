#!/usr/bin/env bash
# Live cluster map at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn --app-dir ui cluster_map:app --host 127.0.0.1 --port 8000
