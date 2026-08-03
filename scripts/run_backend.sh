#!/bin/zsh
set -euo pipefail

mkdir -p data/logs
exec python -m uvicorn research_tree.api.app:app --host 127.0.0.1 --port 8000 2>&1 | tee -a data/logs/backend.log
