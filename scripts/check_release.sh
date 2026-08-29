#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT_DIR/scripts/check_launch.sh"
bash "$ROOT_DIR/scripts/check_anonymous_surface.sh"
