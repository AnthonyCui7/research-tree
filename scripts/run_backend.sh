#!/bin/bash
set -euo pipefail

# Local default binds loopback; a container sets RESEARCH_TREE_HOST=0.0.0.0
# and whatever port its ingress expects.
HOST="${RESEARCH_TREE_HOST:-127.0.0.1}"
PORT="${RESEARCH_TREE_PORT:-8000}"
LOG_DIR="${RESEARCH_TREE_DATA_DIR:-data}/logs"

mkdir -p "$LOG_DIR"
exec python -m uvicorn research_tree.api.app:app --host "$HOST" --port "$PORT" 2>&1 | tee -a "$LOG_DIR/backend.log"
