#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT_DIR/backend/scripts/check_backend_launch.sh"
bash "$ROOT_DIR/frontend/scripts/check_frontend_release.sh"
