#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
source "$BACKEND_DIR/scripts/python_runner.sh"

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
