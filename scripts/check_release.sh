#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

source "$ROOT_DIR/backend/scripts/python_runner.sh"

cd "$ROOT_DIR/backend"
if ! run_py python -c 'import pytest, pytest_asyncio' >/dev/null 2>&1; then
  printf 'Release check requires backend/requirements-dev.txt. Install it in the selected Python environment.\n' >&2
  exit 1
fi
run_py python -m pytest -q

cd "$ROOT_DIR/frontend"
npm run lint
npm run test -- --run

bash "$ROOT_DIR/scripts/check_launch.sh"
bash "$ROOT_DIR/scripts/check_anonymous_surface.sh"
