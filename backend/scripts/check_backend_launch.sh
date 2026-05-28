#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

run_py() {
  if command -v conda >/dev/null 2>&1 && { [ -d "$HOME/anaconda3/envs/aist" ] || [ -d "$HOME/miniconda3/envs/aist" ]; }; then
    conda run --no-capture-output -n aist "$@"
  else
    "$@"
  fi
}

cd "$BACKEND_DIR"

run_py python -m py_compile $(find app -name '*.py') $(find tests -name '*.py')
bash "$BACKEND_DIR/scripts/smoke_runtime_surface.sh"
bash "$BACKEND_DIR/scripts/smoke_package_governance.sh"
bash "$BACKEND_DIR/scripts/smoke_external_backends.sh"
run_py python - <<'PY2'
import sys
sys.path.insert(0, '.')
from app.main import app
print(app.title, app.version)
PY2
