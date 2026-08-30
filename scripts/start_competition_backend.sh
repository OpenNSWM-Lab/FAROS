#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
DATA_DIR="${FAROS_COMPETITION_DATA_DIR:-$ROOT_DIR/backend/runtime/competition-data}"
PORT="${API_PORT:-8005}"

if [[ ! -f "$DATA_DIR/competition_workspace_manifest.json" ]]; then
  printf 'Competition workspace is missing. Run scripts/prepare_competition_workspace.py first.\n' >&2
  exit 1
fi

source "$BACKEND_DIR/scripts/python_runner.sh"
cd "$BACKEND_DIR"
export BACKEND_DIR DATA_DIR
run_py python -m uvicorn app.main:app --host "${API_HOST:-127.0.0.1}" --port "$PORT"
